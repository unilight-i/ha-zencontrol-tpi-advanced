# zencontrol TPI Advanced dla Home Assistant

Integracja Home Assistanta dla kontrolerów zencontrol. Steruje oświetleniem DALI lokalnie,
przez TPI Advanced (UDP), bez chmury. Przygotowana przez Unilight; nie jest produktem ani usługą firmy zencontrol.

*Home Assistant integration for zencontrol DALI controllers over TPI Advanced (local UDP, no cloud).
Made by Unilight; not a product or service of zencontrol.*

Aktualna wersja: **0.7.0** (zob. [CHANGELOG.md](CHANGELOG.md))

## Co daje

- światło dla każdej nazwanej oprawy DALI i każdej grupy: włącz / wyłącz, jasność, płynne przejście,
  barwa (Tunable White), kolor (RGBW)
- przyciski scen zapisanych w zencontrol oraz „Wszystko na max” / „Wyłącz wszystko”
- zdarzenia z kontrolera: stan świateł aktualizowany zwykle w ciągu sekundy
- czujniki ruchu i klawisze paneli (naciśnięcie / przytrzymanie), włączane w opcjach integracji
- akcje dla automatyzacji: `unilight_zen_tpi_advanced.arc_level`, `recall_scene`, `turn_on`, `turn_off`,
  `send_command`, `send_raw`

## Wymagania

- Home Assistant 2026.11 lub nowszy
- kontroler zencontrol z włączoną licencją TPI Advanced
- Home Assistant i kontroler w tej samej sieci lokalnej (UDP 5108 do kontrolera, UDP 6969 dla zdarzeń)

## Instalacja

1. Otwórz repozytorium w sklepie Home Assistanta (Marketplace):

   [![Otwórz w Home Assistant](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=unilight-i&repository=ha-zencontrol-tpi-advanced&category=integration)

   Albo ręcznie: Marketplace → menu (trzy kropki) → repozytoria niestandardowe → wklej
   `https://github.com/unilight-i/ha-zencontrol-tpi-advanced`, rodzaj: integracja.
   Przy pierwszym repozytorium spoza katalogu Home Assistant poprosi o połączenie z kontem GitHub (darmowe).
2. Pobierz integrację i uruchom ponownie Home Assistanta, gdy o to poprosi.
3. Ustawienia → Urządzenia oraz usługi: kontroler pojawia się jako wykryty, wystarczy „Dodaj”.
   Jeśli się nie pojawił: „Dodaj integrację” → `zencontrol` → wybierz kontroler z listy znalezionych
   albo wpisz jego adres IP.

Powstaje urządzenie kontrolera oraz osobne urządzenie dla każdej nazwanej oprawy i każdej grupy.

### Zdarzenia, czujniki ruchu, klawisze paneli

Gdy Home Assistant jest w tej samej sieci co kontroler, odbiór zdarzeń włącza się sam przy dodawaniu.
Czujniki ruchu i klawisze paneli włącza się osobno: urządzenie kontrolera → „Konfiguruj” →
„Pokaż czujniki ruchu” / „Pokaż klawisze paneli ściennych”.

Gdy Home Assistant działa w Dockerze z własną siecią albo w maszynie wirtualnej za NAT-em, kontrolera
nie da się znaleźć automatycznie i zdarzenia trzeba włączyć ręcznie: wpisz adres IP kontrolera przy
dodawaniu, a w opcjach podaj adres komputera z Home Assistantem i przekaż port UDP 6969 do kontenera.

### Nie znam adresu kontrolera

Folder [`tools/`](tools/) zawiera narzędzie, które wyszukuje kontrolery z dowolnego komputera w tej samej
sieci: `ZNAJDZ_KONTROLER.bat` (Windows, dwuklik) albo `python znajdz_kontroler.py`. Niczego nie ustawia.

## Znane ograniczenia

[docs/ZNANE_OGRANICZENIA.md](docs/ZNANE_OGRANICZENIA.md)

## Zgłoszenia

Błędy i pytania: [https://github.com/unilight-i/ha-zencontrol-tpi-advanced/issues](https://github.com/unilight-i/ha-zencontrol-tpi-advanced/issues).
Do zgłoszenia dołącz wersję integracji, wersję Home Assistanta, firmware kontrolera i fragment logu
(Ustawienia → System → Logi, szukaj `zencontrol`).

## Licencja

[PolyForm Shield 1.0.0](LICENSE.md). W skrócie: integracji wolno używać, także w firmie i u klientów;
nie wolno na jej podstawie oferować produktu konkurencyjnego wobec niej ani wobec produktów Unilight.
Oprogramowanie jest dostarczane bez gwarancji.

## Dla rozwijających

```
custom_components/unilight_zen_tpi_advanced/   integracja (to trafia do Home Assistanta)
tests/                                         testy + symulator kontrolera (fake_controller.py)
examples/                                      przykłady automatyzacji do wklejenia
tools/                                         wyszukiwanie kontrolerów spoza Home Assistanta
hacs.json                                      opis repozytorium dla Marketplace w Home Assistant
```

Testy uruchamiają integrację w prawdziwym rdzeniu Home Assistanta i rozmawiają po UDP z symulatorem,
więc nie potrzebują kontrolera:

```
python -m venv .venv
.venv/bin/pip install -r requirements-test.txt      (Windows: .venv\Scripts\pip ...)
.venv/bin/python -m pytest
```

Zasada pracy: każda zmiana w integracji przechodzi testy, dostaje nowy numer wersji w `manifest.json`
i wpis w `CHANGELOG.md`.
