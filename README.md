# zencontrol TPI Advanced dla Home Assistant

Sterowanie oświetleniem DALI z kontrolera zencontrol w Home Assistant: lokalnie, bez chmury.

*Home Assistant integration for zencontrol DALI controllers over TPI Advanced (local, no cloud).*

## Czego potrzebujesz

- Home Assistant 2026.11 lub nowszy
- kontroler zencontrol z licencją **TPI Advanced**
- Home Assistant i kontroler w tej samej sieci lokalnej

## Instalacja

**[Instrukcja krok po kroku ze zrzutami ekranu](docs/INSTALACJA.md)**

W skrócie:

1. W Home Assistancie otwórz **Ustawienia → Katalog społeczności** i kliknij **Dodaj z linku**.
2. Wklej ten link i kliknij **Utwórz**:

   ```
   https://github.com/unilight-i/ha-zencontrol-tpi-advanced
   ```

3. Za pierwszym razem Home Assistant poprosi o połączenie z kontem GitHub (darmowe, robi się to raz).
4. Kliknij **Zainstaluj**.
5. Wybierz swój kontroler z listy znalezionych i kliknij **Zatwierdź**.

Gotowe. Każda nazwana oprawa i każda grupa z zencontrol jest teraz światłem w Home Assistant.

## Co dostajesz

- światła: włącz / wyłącz, jasność, płynne przejście, barwa (Tunable White), kolor (RGBW)
- przyciski scen zapisanych w zencontrol
- stan świateł aktualizowany zwykle w ciągu sekundy, także po zmianie z panelu ściennego
- czujniki ruchu i klawisze paneli ściennych do automatyzacji (włączane osobno, patrz niżej)

## Czujniki ruchu i klawisze paneli

Są domyślnie ukryte. Włączysz je tak: **Ustawienia → Urządzenia oraz usługi → zencontrol TPI Advanced**,
koło zębate przy kontrolerze, zaznacz „Pokaż czujniki ruchu” lub „Pokaż klawisze paneli ściennych” i zatwierdź.
Zrzut ekranu jest w [instrukcji](docs/INSTALACJA.md#czujniki-ruchu-i-klawisze-paneli-opcjonalnie).

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
