@echo off
REM ASCII only - see restart-pdfredactor.bat for why.
title PDF Redactor - Remove Autostart
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall-autostart.ps1"
echo.
pause
