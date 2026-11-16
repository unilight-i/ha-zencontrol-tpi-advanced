@echo off
title Szukanie kontrolera zencontrol
rem Szuka kontrolerow zencontrol w sieci lokalnej. Wymaga pliku znajdz_kontroler.ps1 w tym samym folderze.
rem Opcjonalnie: ZNAJDZ_KONTROLER.bat 192.168.2   (trzy pierwsze liczby adresu sieci)
if not exist "%~dp0znajdz_kontroler.ps1" (
  echo Brakuje pliku znajdz_kontroler.ps1 - musi lezec obok tego pliku.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0znajdz_kontroler.ps1" %*
if errorlevel 1 pause
