# Szuka kontrolerow zencontrol w sieci lokalnej i pokazuje ich adresy IP.
#
# Jak dziala: do kazdego adresu w sieci (x.x.x.1 - x.x.x.254) wysyla to samo pytanie o wersje,
# ktore integracja Home Assistanta wysyla przy dodawaniu (TPI, UDP 5108). Kto odpowie poprawna
# ramka TPI, jest kontrolerem zencontrol. Niczego nie ustawia i niczym nie steruje.
#
# Uruchamianie: dwuklik na ZNAJDZ_KONTROLER.bat (ten plik musi lezec obok).
# Plik celowo bez polskich znakow: Windows PowerShell czyta pliki bez BOM jako ANSI.
param([string]$Siec = "")

$Port = 5108
$TpiErrors = @{ 0xB0 = "PAID_FEATURE"; 0xB1 = "INVALID_ARGS"; 0xB2 = "CMD_REFUSED"; 0xB3 = "QUEUE_FAILURE";
                0xB4 = "RESPONSE_UNAVAIL"; 0xB5 = "OTHER_DALI_ERROR"; 0xB6 = "MAX_LIMIT";
                0xB7 = "UNEXPECTED_RESULT"; 0xB8 = "UNKNOWN_TARGET" }

function Get-Xor([byte[]]$Data) {
    $acc = 0
    foreach ($b in $Data) { $acc = $acc -bxor $b }
    return $acc
}

# Ramka TPI: 0x04, numer, polecenie, adres 0, trzy bajty danych 0, suma XOR
function New-Frame([int]$Seq, [int]$Command) {
    $body = [byte[]]@(0x04, ($Seq -band 0xFF), $Command, 0, 0, 0, 0)
    $sum = Get-Xor $body
    $frame = [byte[]]($body + [byte]$sum)
    return ,$frame
}

# Odpowiedz TPI: typ 0xA0-0xA3, numer, dlugosc, dane, suma XOR
function Test-TpiReply([byte[]]$Data) {
    if ($Data.Length -lt 4) { return $false }
    if ($Data[0] -lt 0xA0 -or $Data[0] -gt 0xA3) { return $false }
    if ($Data[2] -ne ($Data.Length - 4)) { return $false }
    return ((Get-Xor $Data) -eq 0)
}

function New-Socket {
    $local = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
    $client = New-Object System.Net.Sockets.UdpClient($local)
    # Windows: komunikat "port nieosiagalny" od innego urzadzenia nie moze przerywac odbioru
    try { [void]$client.Client.IOControl(-1744830452, [byte[]]@(0, 0, 0, 0), $null) } catch { }
    return $client
}

function Receive-Replies($Client, [double]$Seconds, [hashtable]$Found) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ($true) {
        $leftMs = [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds
        if ($leftMs -lt 1) { break }
        $Client.Client.ReceiveTimeout = [Math]::Min($leftMs, 250)
        $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
        $data = $null
        try { $data = $Client.Receive([ref]$remote) } catch { continue }
        if ($null -ne $data -and (Test-TpiReply $data)) {
            $ip = $remote.Address.ToString()
            if (-not $Found.ContainsKey($ip)) { $Found[$ip] = $data }
        }
    }
}

function Search-Network([string]$Prefix) {
    $found = @{}
    $client = New-Socket
    try {
        foreach ($wait in @(1.0, 1.5)) {     # dwie rundy: pierwszy pakiet do uspionego urzadzenia moze przepasc
            for ($h = 1; $h -le 254; $h++) {
                $ip = "$Prefix.$h"
                if ($found.ContainsKey($ip)) { continue }
                $frame = New-Frame $h 0x1C
                $target = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($ip), $Port)
                try { [void]$client.Send($frame, $frame.Length, $target) } catch { }
                if (($h % 16) -eq 0) { Receive-Replies $client 0.02 $found }
            }
            Receive-Replies $client $wait $found
        }
    } finally {
        $client.Close()
    }
    return $found
}

function Request-Tpi([string]$Ip, [int]$Command) {
    $client = New-Socket
    try {
        for ($attempt = 0; $attempt -lt 3; $attempt++) {
            $frame = New-Frame (0x80 + $attempt) $Command
            $target = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($Ip), $Port)
            try { [void]$client.Send($frame, $frame.Length, $target) } catch { }
            $got = @{}
            Receive-Replies $client 0.8 $got
            if ($got.ContainsKey($Ip)) {
                $reply = [byte[]]$got[$Ip]
                return ,$reply
            }
        }
    } finally {
        $client.Close()
    }
    return $null
}

function Get-LocalNetworks {
    $withGateway = @()
    $others = @()
    foreach ($nic in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($nic.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
        if ($nic.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) { continue }
        $props = $nic.GetIPProperties()
        $gateway = $false
        foreach ($gw in $props.GatewayAddresses) {
            if ($gw.Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
                $gw.Address.ToString() -ne "0.0.0.0") { $gateway = $true }
        }
        foreach ($ua in $props.UnicastAddresses) {
            if ($ua.Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { continue }
            $ip = $ua.Address.ToString()
            if ($ip.StartsWith("127.") -or $ip.StartsWith("169.254.")) { continue }
            $mask = ""
            try { $mask = $ua.IPv4Mask.ToString() } catch { }
            $item = New-Object PSObject -Property @{
                Prefix = $ip.Substring(0, $ip.LastIndexOf(".")); Own = $ip; Card = $nic.Name; Mask = $mask }
            if ($gateway) { $withGateway += $item } else { $others += $item }
        }
    }
    # karty z brama to prawdziwa siec; karty bez bramy to zwykle wirtualne przelaczniki (Docker, WSL, Hyper-V)
    $chosen = $withGateway
    if ($chosen.Count -eq 0) { $chosen = $others }
    $seen = @{}
    $result = @()
    foreach ($item in $chosen) {
        if (-not $seen.ContainsKey($item.Prefix)) {
            $seen[$item.Prefix] = $true
            $result += $item
        }
    }
    return $result
}

# "192.168.2", "192.168.2.x", "192.168.2.15" -> "192.168.2"; cokolwiek innego -> $null
function ConvertTo-Prefix([string]$Value) {
    $m = [regex]::Match($Value.Trim(), '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})(\.(\d{1,3}|[xX*]))?$')
    if (-not $m.Success) { return $null }
    $parts = @()
    foreach ($i in @(1, 2, 3)) {
        $n = [int]$m.Groups[$i].Value
        if ($n -gt 255) { return $null }
        $parts += $n
    }
    return ($parts -join ".")
}

# Przeszukuje jedna siec, wypisuje znalezione kontrolery, zwraca ich liczbe
function Show-Network([string]$Prefix, [string]$Own, [string]$Card) {
    $where = ""
    if ($Own) { $where = " (ten komputer: $Own, karta: $Card)" }
    Write-Host -NoNewline "Szukam w sieci $Prefix.x$where ... "
    $found = Search-Network $Prefix
    Write-Host "gotowe."
    $ips = @($found.Keys | Sort-Object { [int]($_.Substring($_.LastIndexOf(".") + 1)) })
    foreach ($ip in $ips) {
        $reply = [byte[]]$found[$ip]
        $name = ""
        $firmware = ""
        $note = ""
        $len = [int]$reply[2]
        if ($reply[0] -eq 0xA1 -and $len -ge 3) {
            $firmware = "{0}.{1}.{2}" -f $reply[3], $reply[4], $reply[5]
        } elseif ($reply[0] -eq 0xA3) {
            $code = 0
            if ($len -ge 1) { $code = [int]$reply[3] }
            $errName = $TpiErrors[$code]
            if (-not $errName) { $errName = "0x{0:X2}" -f $code }
            $note = "odpowiada bledem $errName"
            if ($code -eq 0xB0) { $note = $note + " - kontroler nie ma wlaczonej licencji TPI" }
        }
        $label = Request-Tpi $ip 0x24
        if ($null -ne $label -and $label[0] -eq 0xA1) {
            $count = [int]$label[2]
            if ($count -gt 0) {
                $name = [System.Text.Encoding]::UTF8.GetString($label, 3, $count).Trim(@([char]0, [char]32))
            }
        }
        Write-Host ""
        Write-Host "  KONTROLER ZENCONTROL   adres IP: $ip" -ForegroundColor Green
        if ($name)     { Write-Host "                         nazwa:    $name" }
        if ($firmware) { Write-Host "                         firmware: $firmware" }
        if ($note)     { Write-Host "                         uwaga:    $note" -ForegroundColor Yellow }
    }
    return $ips.Count
}

function Show-Result([int]$Count) {
    Write-Host ""
    if ($Count -gt 0) {
        Write-Host 'Adres IP wpisz w Home Assistant w polu "IP kontrolera" (Controller IP).'
    } else {
        Write-Host "Nie znalazlem zadnego kontrolera. Najczestsze przyczyny:"
        Write-Host "  1. Komputer i kontroler sa w roznych sieciach (inny router, siec dla gosci, VPN)."
        Write-Host "  2. Kontroler jest wylaczony albo nie ma kabla sieciowego."
        Write-Host "  3. Kontroler nie ma wlaczonego TPI."
        Write-Host "  4. Zapora albo antywirus na tym komputerze blokuje ruch UDP."
    }
}

Write-Host "Wyszukiwanie kontrolerow zencontrol (TPI, UDP $Port)"
Write-Host ""
$total = 0
if ($Siec) {
    $prefix = ConvertTo-Prefix $Siec
    if (-not $prefix) {
        Write-Host "Nie rozumiem '$Siec'. Podaj trzy pierwsze liczby adresu, na przyklad 192.168.1"
    } else {
        $total = Show-Network $prefix "" ""
    }
} else {
    $nets = @(Get-LocalNetworks)
    if ($nets.Count -eq 0) { Write-Host "Ten komputer nie jest podlaczony do zadnej sieci." }
    foreach ($net in $nets) {
        $n = Show-Network $net.Prefix $net.Own $net.Card
        $total = $total + $n
        if ($net.Mask -and $net.Mask -ne "255.255.255.0") {
            Write-Host "  (Ta siec ma maske $($net.Mask), czyli wiecej niz 254 adresy. Przeszukalem tylko $($net.Prefix).x)"
        }
    }
}
Show-Result $total

while ($true) {
    Write-Host ""
    $answer = Read-Host "Inna siec? Wpisz jej trzy pierwsze liczby (np. 192.168.2) albo sam Enter, zeby zakonczyc"
    if (-not $answer) { break }
    $prefix = ConvertTo-Prefix $answer
    if (-not $prefix) {
        Write-Host "Nie rozumiem '$answer'. Przyklad: 192.168.2"
        continue
    }
    $n = Show-Network $prefix "" ""
    Show-Result $n
}
