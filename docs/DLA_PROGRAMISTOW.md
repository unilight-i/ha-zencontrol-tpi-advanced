# Dla programistów

Ta strona nie jest potrzebna do instalacji. Instalacja jest opisana w [README](../README.md).

## Akcje dla automatyzacji

Integracja udostępnia akcje `unilight_zen_tpi_advanced.arc_level`, `recall_scene`, `turn_on`, `turn_off`,
`send_command` i `send_raw`. Opis pól jest w Home Assistant: Narzędzia deweloperskie → Akcje.

Zdarzenia z kontrolera (klawisze, czujniki, sceny) trafiają na magistralę Home Assistanta jako
`unilight_zen_tpi_advanced_event`. Gotowy przykład automatyzacji:
[examples/powiadomienie_o_ruchu.yaml](../examples/powiadomienie_o_ruchu.yaml).

## Budowa repozytorium

```
custom_components/unilight_zen_tpi_advanced/   integracja (tylko to trafia do Home Assistanta)
tests/                                         testy + symulator kontrolera (fake_controller.py)
examples/                                      przykłady automatyzacji do wklejenia
tools/                                         wyszukiwanie kontrolerów spoza Home Assistanta
hacs.json                                      opis repozytorium dla Marketplace w Home Assistant
```

## Testy

Testy uruchamiają integrację w prawdziwym rdzeniu Home Assistanta i rozmawiają po UDP z symulatorem,
więc nie potrzebują kontrolera:

```
python -m venv .venv
.venv/bin/pip install -r requirements-test.txt      (Windows: .venv\Scripts\pip ...)
.venv/bin/python -m pytest
```

Te same testy oraz kontrola manifestu uruchamiają się na GitHubie po każdej zmianie
(`.github/workflows/validate.yml`).

Zasada pracy: każda zmiana w integracji przechodzi testy, dostaje nowy numer wersji w `manifest.json`
i wpis w `CHANGELOG.md`.
