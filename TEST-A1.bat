@echo off
REM ============================================================
REM  TEST A1 - Casse podinfo (scale a 0) pour declencher HELIOS
REM  Double-cliquez ce fichier OU lancez: TEST-A1.bat
REM ============================================================
cd /d "%~dp0"
echo.
echo === Declenchement du test A1 (podinfo -^> 0) ===
echo.
python scripts\test_a1.py
echo.
echo Regardez l'UI Incidents : IN_PROGRESS dans ~30s, RESOLVED dans ~2min.
echo.
pause
