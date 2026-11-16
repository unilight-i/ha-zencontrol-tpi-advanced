# Znane ograniczenia i odporność na zmiany

Przegląd z 12.11.2026 dla wersji 0.6.2, uzupełniony 16.11.2026 o zmiany wersji 0.7.0 (pozycje 1, 10, 11,
13 i 22). To jest lista ustaleń, a nie plan.

Oznaczenia źródła: **[symulator]** sprawdzone uruchomieniem integracji w Home Assistant z symulatorem
kontrolera (`tests/fake_controller.py`), **[test]** pokryte stałym testem w `tests/`, **[kod]** wynika z kodu,
bez osobnej próby.

Zakres sprawdzenia na prawdziwym sprzęcie: jeden kontroler (Application Controller Pro, firmware 2.2.145),
Home Assistant 2026.11.1 w Dockerze na Windows.

## Co jest odporne

| Sytuacja | Zachowanie |
|---|---|
| Kontroler nie odpowiada przy starcie Home Assistanta | Integracja ponawia próby, aż kontroler wróci. **[test]** |
| Kontroler został zrestartowany | Ustawienie zdarzeń (nietrwałe na kontrolerze) wraca przy najbliższym odczycie, do 30 s. **[test]** |
| Pojedynczy zgubiony pakiet UDP | Zapytanie jest ponawiane raz, bez widocznych skutków. **[symulator]** |
| Kontroler chwilowo zajęty (QUEUE_FAILURE) | Polecenie jest ponawiane z rosnącym odstępem. **[test]** |
| Zmiana poza Home Assistantem (panel ścienny, zencontrol) | Stan pojawia się po zdarzeniu albo przy odczycie co 30 s. **[test]** |
| Czujnik albo klawisz, którego kontroler nie wymienił | Powstaje przy pierwszym zdarzeniu. **[test]** |
| Polskie znaki w nazwach | Odczyt UTF-8, zapasowo Latin-1. **[test]** |
| Dwa kontrolery z różnymi portami zdarzeń | Oba odbierają zdarzenia. **[symulator]** |

## Co wymaga ręcznej reakcji albo zawodzi

### Sieć

1. **Kontroler dostał nowy adres IP.** Wszystkie encje są „niedostępne” do czasu ręcznej „Rekonfiguracji”.
   Wyszukiwanie w sieci działa tylko przy dodawaniu; istniejący wpis jest rozpoznawany po adresie IP
   i porcie, nie po numerze seryjnym, i nie podąża za kontrolerem. **[kod]** Obejście dziś: rezerwacja
   adresu w routerze.
2. **Home Assistant dostał nowy adres IP.** Zdarzenia cicho przestają przychodzić: integracja dalej każe
   kontrolerowi wysyłać je na stary adres i niczego nie zgłasza. Jedyny ślad to stojący czas w „Ostatnie zdarzenie”.
   **[symulator]**
3. **Dwa zgubione pakiety z rzędu podczas odczytu.** Wszystkie światła stają się „niedostępne” do następnego
   odczytu (do 30 s). Na słabej sieci stan może migać. **[symulator]**
4. **Docker.** Port zdarzeń trzeba ręcznie przekazać do kontenera i odblokować w zaporze. Gdy portu nie da się
   otworzyć, integracja działa dalej bez zdarzeń, a informacja jest tylko w logu. **[test]**

### Zmiany w instalacji zencontrol

5. **Nowa albo przemianowana oprawa, grupa, scena.** Widoczna dopiero po „Wczytaj ponownie” albo restarcie.
   **[symulator]**
6. **Usunięta oprawa albo grupa.** Jej urządzenie i encja zostają na stałe jako „niedostępne”. Integracja ich
   nie sprząta. **[symulator]**
7. **Ponowne adresowanie DALI.** Encja jest przypięta do adresu DALI, nie do fizycznej oprawy. Po zamianie
   adresów `light.downlight_a62` steruje inną lampą, a automatyzacje „przeskakują” razem z nią. **[symulator]**
8. **Zmiana nazwy oprawy.** Zmienia się nazwa wyświetlana, identyfikator encji zostaje stary. To zwyczaj
   Home Assistanta, ale bywa mylące. **[symulator]**
9. **Instalacja bez nazw opraw.** Światła opraw są ukryte, widać tylko grupy, a ich stan jest „nieznany”,
   bo stan grupy liczony jest z opraw. **[symulator]**

### Wiele kontrolerów i wiele systemów

10. **Wyszukiwanie kontrolerów wymaga tej samej sieci.** Od wersji 0.7.0 „Dodaj integrację” przeszukuje sieć,
    a Home Assistant sam zgłasza wykryty kontroler. **[test]** Nie działa to, gdy Home Assistant jest
    w Dockerze z własną siecią, w maszynie wirtualnej za NAT-em albo w innym VLAN-ie: wtedy adres wpisuje
    się ręcznie. Wyszukiwanie sprawdzono na dwóch prawdziwych kontrolerach spoza Home Assistanta;
    wykrywanie przez DHCP sprawdzono tylko testem z symulatorem.
11. **Ten sam port zdarzeń dla dwóch kontrolerów.** Przy automatycznym włączeniu zdarzeń drugi kontroler
    dostaje kolejny wolny port (6970, ...). **[test]** Przy ręcznym wpisaniu tego samego portu w opcjach
    drugi kontroler nie dostaje zdarzeń, a jedyny ślad to wpis w logu. **[symulator]**
12. **Akcje bez pola `host`** (`unilight_zen_tpi_advanced.arc_level` i pozostałe) trafiają do pierwszego kontrolera. **[kod]**
13. **Jeden odbiorca zdarzeń.** Kontroler wysyła zdarzenia na jeden adres. Drugi Home Assistant albo inny system
    ustawiający własny adres zabiera zdarzenia, a integracja co 30 s ustawia swój z powrotem, więc systemy się
    przepychają. **[kod]** Od wersji 0.7.0 integracja przy dodawaniu nie włącza zdarzeń sama, jeśli kontroler
    wysyła je już pod inny adres. **[test]**
14. **Brak sprawdzania nadawcy zdarzeń.** Pakiet z dowolnego urządzenia w sieci wysłany na port zdarzeń jest
    traktowany jak zdarzenie kontrolera (na przykład fałszywy ruch). **[symulator]** TPI samo w sobie nie ma
    zabezpieczeń.

### Home Assistant

15. **Usunięcie i ponowne dodanie integracji** kasuje własne nazwy, obszary i identyfikatory encji, bo
    wewnętrzne identyfikatory zależą od wpisu integracji. **[symulator]**
16. **Nowe wersje Home Assistanta.** Home Assistant zmienia wewnętrzne API co miesiąc. Integracja była
    sprawdzana na 2026.10.4 i 2026.11.1; minimalna wersja nie jest wymuszana w `manifest.json`. Aktualizacja
    Home Assistanta bez wcześniejszego uruchomienia testów może zepsuć integrację.
17. **Home Assistant OS** (najczęstszy u klientów) nie był dotąd sprawdzony na prawdziwej instalacji.

### Zakres urządzeń

18. Rolety są dziś „światłem”. Przekaźniki, oprawy awaryjne, czujniki natężenia światła i kolor xy nie mają
    własnej obsługi. **[kod]**
19. Inne wersje firmware, kontrolery z więcej niż jedną linią DALI i instalacje z dziesiątkami opraw nie były
    sprawdzane. Czas odczytu rośnie liniowo z liczbą opraw (zapytania idą po kolei). **[kod]**

### Drobiazgi

20. Zastępcza nazwa sceny bez etykiety jest po polsku („Scena 3”) niezależnie od języka Home Assistanta.
21. W polskim tłumaczeniu komunikatu błędu jest „Dzienniki”, a w Home Assistant pozycja nazywa się „Logi”.
22. Poprawione w wersji 0.7.0: `manifest.json` ma pola `documentation`, `issue_tracker` i `codeowners`.
