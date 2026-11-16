# Historia zmian

Numer wersji jest w `custom_components/unilight_zen_tpi_advanced/manifest.json`.
Wersje do 0.6.2 nosiły domenę `zencontrol_tpi` i nie były publikowane.

## 0.7.1

- Instrukcja instalacji krok po kroku ze zrzutami ekranu: `docs/INSTALACJA.md`.
- Krótszy opis repozytorium. Bez zmian w działaniu integracji.

## 0.7.0

Pierwsza wersja publiczna, instalowana przez Marketplace w Home Assistant.

- **Nowa nazwa:** domena `unilight_zen_tpi_advanced` (wcześniej `zencontrol_tpi`), nazwa
  „zencontrol TPI Advanced (Unilight)”. Zdarzenie dla automatyzacji nazywa się teraz
  `unilight_zen_tpi_advanced_event`. Instalacje wersji 0.6.x trzeba dodać od nowa.
- **Wyszukiwanie kontrolerów:** „Dodaj integrację” przeszukuje sieć i pokazuje listę znalezionych
  kontrolerów; adres IP wpisuje się tylko wtedy, gdy nic nie znaleziono.
- **Wykrywanie przez Home Assistanta:** kontroler w tej samej sieci pojawia się sam w „Wykryto”
  (rozpoznanie po karcie sieciowej, potem sprawdzenie TPI).
- **Zdarzenia włączają się same**, gdy Home Assistant ma adres w sieci kontrolera. Nie są włączane,
  gdy kontroler wysyła już zdarzenia do innego systemu. Drugi kontroler dostaje własny port zdarzeń.
- **Czujniki ruchu i klawisze paneli** są domyślnie wyłączone i włącza się je w opcjach integracji
  („Pokaż czujniki ruchu”, „Pokaż klawisze paneli ściennych”).
- W opcjach pole adresu komputera można zostawić puste: integracja wykryje go sama.
- Minimalna wersja Home Assistanta: 2026.11.

## 0.6.2

- Formularz dodawania integracji zaczyna się bez wpisanego adresu kontrolera (paczka do udostępniania).
- Do repozytorium dodane: testy, symulator kontrolera, instrukcja instalacji w PDF.

## 0.6.1

- Poprawka: po włączeniu lampy Home Assistant przez 2-3 sekundy pokazywał ją jako wyłączoną.
  Zdarzenia o poziomie grupy nie nadpisują już stanu pojedynczych opraw; natychmiast pokazywany jest
  tylko raport oprawy o osiągnięciu poziomu, pozostałe wywołują ponowny odczyt.

## 0.6.0

- Klawisze paneli ściennych jako encje zdarzeń (naciśnięcie / przytrzymanie).

## 0.5.0

- Odbiór zdarzeń TPI z kontrolera (unicast, domyślnie port UDP 6969).
- Czujniki ruchu jako encje, czujnik diagnostyczny „Ostatnie zdarzenie”.
- Zdarzenie `unilight_zen_tpi_advanced_event` dla automatyzacji.

## Wcześniej

- Sterowanie oprawami i grupami (jasność, barwa, kolor, płynne przejście), przyciski scen, akcje.
  Historia zmian przed wersją 0.5.0 nie była zapisywana.
