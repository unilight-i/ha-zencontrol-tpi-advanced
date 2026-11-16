# zencontrol TPI Advanced dla Home Assistant

Sterowanie oświetleniem DALI z kontrolera zencontrol w Home Assistant: lokalnie, bez chmury.

*Home Assistant integration for zencontrol DALI controllers over TPI Advanced (local, no cloud).*

## Czego potrzebujesz

- Home Assistant 2026.11 lub nowszy
- kontroler zencontrol z licencją **TPI Advanced**
- Home Assistant i kontroler w tej samej sieci lokalnej

## Instalacja

1. Kliknij przycisk:

   [![Otwórz w Home Assistant](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=unilight-i&repository=ha-zencontrol-tpi-advanced&category=integration)

   Za pierwszym razem strona zapyta o adres Twojego Home Assistanta. Wpisz ten sam adres, pod którym
   otwierasz go w przeglądarce, na przykład `http://homeassistant.local:8123` albo `http://192.168.1.50:8123`.
2. Jeśli Home Assistant poprosi o połączenie z kontem GitHub, potwierdź je (konto jest darmowe, robi się to raz).
3. Kliknij **Pobierz**, a potem **Uruchom ponownie**, gdy Home Assistant o to poprosi.
4. Otwórz **Ustawienia → Urządzenia oraz usługi**. Kontroler jest na liście wykrytych: kliknij **Dodaj**.

   Nie ma go tam? Kliknij **Dodaj integrację**, wpisz `zencontrol` i wybierz kontroler z listy.

Gotowe. Każda nazwana oprawa i każda grupa z zencontrol jest teraz światłem w Home Assistant.

## Co dostajesz

- światła: włącz / wyłącz, jasność, płynne przejście, barwa (Tunable White), kolor (RGBW)
- przyciski scen zapisanych w zencontrol
- stan świateł aktualizowany zwykle w ciągu sekundy, także po zmianie z panelu ściennego
- czujniki ruchu i klawisze paneli ściennych do automatyzacji (włączane osobno, patrz niżej)

## Czujniki ruchu i klawisze paneli

Są domyślnie wyłączone. Włączysz je tak: **Ustawienia → Urządzenia oraz usługi → zencontrol TPI Advanced →
Konfiguruj**, zaznacz „Pokaż czujniki ruchu” lub „Pokaż klawisze paneli ściennych” i zapisz.

## Gdy coś nie działa

| Co widzisz | Co zrobić |
|---|---|
| Kontrolera nie ma na liście | Sprawdź, czy Home Assistant i kontroler są w tej samej sieci. Jeśli Home Assistant działa w Dockerze albo w maszynie wirtualnej, wybierz „Wpisz adres ręcznie” i podaj adres IP kontrolera. |
| Nie znam adresu IP kontrolera | Zobacz listę urządzeń w routerze albo uruchom `ZNAJDZ_KONTROLER.bat` z folderu [`tools`](tools/) na komputerze w tej samej sieci. |
| „Kontroler nie odpowiada” | Zły adres IP, inna sieć albo wyłączone TPI na kontrolerze. |
| „Kontroler odpowiedział PAID_FEATURE” | Kontroler nie ma licencji TPI Advanced. Skontaktuj się ze sprzedawcą. |
| Światła działają, ale stan odświeża się z opóźnieniem, a czujniki milczą | Zdarzenia nie docierają. W „Konfiguruj” włącz odbiór zdarzeń. W Dockerze wpisz tam adres IP komputera z Home Assistantem i przekaż do kontenera port UDP 6969. |

Nie pomogło? [Zgłoś problem](https://github.com/unilight-i/ha-zencontrol-tpi-advanced/issues) i podaj wersję
Home Assistanta, firmware kontrolera oraz fragment logu (Ustawienia → System → Logi, szukaj `zencontrol`).

## Więcej

- [Historia zmian](CHANGELOG.md)
- [Znane ograniczenia](docs/ZNANE_OGRANICZENIA.md)
- [Dla programistów: akcje, testy, budowa repozytorium](docs/DLA_PROGRAMISTOW.md)
- [Licencja](LICENSE.md): PolyForm Shield 1.0.0. Integracji wolno używać, także w firmie i u klientów;
  nie wolno na jej podstawie oferować produktu konkurencyjnego. Oprogramowanie jest dostarczane bez gwarancji.

Integrację przygotował Unilight. Nie jest produktem ani usługą firmy zencontrol.
