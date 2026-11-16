#!/usr/bin/env python3
"""Szuka kontrolerow zencontrol w sieci lokalnej i pokazuje ich adresy IP.

Jak dziala: do kazdego adresu w sieci (x.x.x.1 - x.x.x.254) wysyla to samo pytanie o wersje,
ktore integracja Home Assistanta wysyla przy dodawaniu (TPI, UDP 5108). Kto odpowie poprawna
ramka TPI, jest kontrolerem zencontrol. Niczego nie ustawia i niczym nie steruje.

Uzycie:
    python znajdz_kontroler.py                przeszukuje siec, w ktorej jest ten komputer
    python znajdz_kontroler.py 192.168.2      przeszukuje podana siec (trzy pierwsze liczby adresu)

Wymaga tylko Pythona 3.8+ (bez dodatkowych pakietow). Na Windows wygodniej: ZNAJDZ_KONTROLER.bat.
"""
from __future__ import annotations

import re
import socket
import sys
import time

PORT = 5108
CMD_VERSION = 0x1C
CMD_LABEL = 0x24
REPLY_ANSWER = 0xA1
REPLY_ERROR = 0xA3
ERRORS = {0xB0: "PAID_FEATURE", 0xB1: "INVALID_ARGS", 0xB2: "CMD_REFUSED", 0xB3: "QUEUE_FAILURE",
          0xB4: "RESPONSE_UNAVAIL", 0xB5: "OTHER_DALI_ERROR", 0xB6: "MAX_LIMIT", 0xB7: "UNEXPECTED_RESULT",
          0xB8: "UNKNOWN_TARGET"}


def xor(data: bytes) -> int:
    acc = 0
    for byte in data:
        acc ^= byte
    return acc


def frame(seq: int, command: int) -> bytes:
    """Basic TPI request: magic 0x04, sequence, command, address 0, three data bytes 0, checksum."""
    body = bytes([0x04, seq & 0xFF, command, 0, 0, 0, 0])
    return body + bytes([xor(body)])


def is_tpi_reply(data: bytes) -> bool:
    """[type 0xA0-0xA3, sequence, length, data..., checksum] with a correct length and checksum."""
    return len(data) >= 4 and 0xA0 <= data[0] <= 0xA3 and data[2] == len(data) - 4 and xor(data) == 0


def new_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    if hasattr(sock, "ioctl"):                  # Windows: a "port unreachable" from another device must not
        try:                                    # break the receive loop (SIO_UDP_CONNRESET off)
            sock.ioctl(0x9800000C, False)
        except (OSError, ValueError):
            pass
    return sock


def collect(sock: socket.socket, seconds: float, found: dict[str, bytes]) -> None:
    deadline = time.monotonic() + seconds
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        sock.settimeout(min(left, 0.25))
        try:
            data, (ip, _port) = sock.recvfrom(1024)
        except (socket.timeout, ConnectionError, OSError):
            continue
        if is_tpi_reply(data):
            found.setdefault(ip, data)


def scan(prefix: str, port: int = PORT, waits: tuple[float, ...] = (1.0, 1.5)) -> dict[str, bytes]:
    """Ask every address prefix.1 - prefix.254 for its version; returns {ip: reply}."""
    found: dict[str, bytes] = {}
    sock = new_socket()
    try:
        for wait in waits:                      # two rounds: the first packet to a sleeping device can be lost
            for host in range(1, 255):
                ip = f"{prefix}.{host}"
                if ip in found:
                    continue
                try:
                    sock.sendto(frame(host, CMD_VERSION), (ip, port))
                except OSError:
                    pass
                if host % 16 == 0:
                    collect(sock, 0.02, found)  # a short breath, so the network is not flooded
            collect(sock, wait, found)
    finally:
        sock.close()
    return found


def ask(ip: str, command: int, port: int = PORT, tries: int = 3) -> bytes | None:
    sock = new_socket()
    try:
        for attempt in range(tries):
            sock.sendto(frame(0x80 + attempt, command), (ip, port))
            got: dict[str, bytes] = {}
            collect(sock, 0.8, got)
            if ip in got:
                return got[ip]
    except OSError:
        pass
    finally:
        sock.close()
    return None


def text(data: bytes) -> str:
    try:
        return data.decode("utf-8").strip("\x00 ")
    except UnicodeDecodeError:
        return data.decode("latin-1").strip("\x00 ")


def describe(ip: str, reply: bytes, port: int = PORT) -> dict[str, str]:
    info = {"ip": ip, "name": "", "firmware": "", "note": ""}
    payload = reply[3:-1]
    if reply[0] == REPLY_ANSWER and len(payload) >= 3:
        info["firmware"] = ".".join(str(b) for b in payload[:3])
    elif reply[0] == REPLY_ERROR:
        code = payload[0] if payload else 0
        info["note"] = f"odpowiada bledem {ERRORS.get(code, hex(code))}"
        if code == 0xB0:
            info["note"] += " - kontroler nie ma wlaczonej licencji TPI"
    label = ask(ip, CMD_LABEL, port)
    if label and label[0] == REPLY_ANSWER:
        info["name"] = text(label[3:-1])
    return info


def local_networks() -> list[tuple[str, str]]:
    """[(prefix, own address)] of this computer, the network with the default route first."""
    addresses: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))         # nothing is sent: this only asks the system which card it would use
        addresses.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.append(item[4][0])
    except OSError:
        pass
    result: list[tuple[str, str]] = []
    for ip in addresses:
        if ip.startswith(("127.", "169.254.", "0.")):
            continue
        prefix = ip.rsplit(".", 1)[0]
        if prefix not in [p for p, _ in result]:
            result.append((prefix, ip))
    return result


def parse_prefix(value: str) -> str | None:
    """'192.168.2', '192.168.2.x', '192.168.2.15' -> '192.168.2'; anything else -> None."""
    match = re.fullmatch(r"\s*(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:\.(?:\d{1,3}|[xX*]))?\s*", value)
    if not match or any(int(part) > 255 for part in match.groups()[:3]):
        return None
    return ".".join(str(int(part)) for part in match.groups()[:3])


def search(prefix: str, own: str = "", port: int = PORT) -> int:
    where = f" (ten komputer: {own})" if own else ""
    print(f"Szukam w sieci {prefix}.x{where} ... ", end="", flush=True)
    found = scan(prefix, port)
    print("gotowe.")
    for ip in sorted(found, key=lambda a: int(a.rsplit(".", 1)[1])):
        info = describe(ip, found[ip], port)
        print()
        print(f"  KONTROLER ZENCONTROL   adres IP: {info['ip']}")
        if info["name"]:
            print(f"                         nazwa:    {info['name']}")
        if info["firmware"]:
            print(f"                         firmware: {info['firmware']}")
        if info["note"]:
            print(f"                         uwaga:    {info['note']}")
    return len(found)


def main(argv: list[str]) -> int:
    port = PORT
    args = [a for a in argv if not a.startswith("--port=")]
    for a in argv:
        if a.startswith("--port="):
            port = int(a.split("=", 1)[1])
    print("Wyszukiwanie kontrolerow zencontrol (TPI, UDP %d)" % port)
    print()
    if args:
        targets = []
        for value in args:
            prefix = parse_prefix(value)
            if prefix is None:
                print(f"Nie rozumiem '{value}'. Podaj trzy pierwsze liczby adresu, na przyklad 192.168.1")
                return 2
            targets.append((prefix, ""))
    else:
        targets = local_networks()
        if not targets:
            print("Ten komputer nie jest podlaczony do zadnej sieci.")
            return 1
    total = sum(search(prefix, own, port) for prefix, own in targets)
    print()
    if total:
        print("Adres IP wpisz w Home Assistant w polu \"IP kontrolera\" (Controller IP).")
    else:
        print("Nie znalazlem zadnego kontrolera. Najczestsze przyczyny:")
        print("  1. Komputer i kontroler sa w roznych sieciach (inny router, siec dla gosci, VPN).")
        print("  2. Kontroler jest wylaczony albo nie ma kabla sieciowego.")
        print("  3. Kontroler nie ma wlaczonego TPI.")
        print("  4. Zapora albo antywirus na tym komputerze blokuje ruch UDP.")
        print("Inna siec przeszukasz, podajac jej trzy pierwsze liczby, na przyklad: 192.168.2")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
