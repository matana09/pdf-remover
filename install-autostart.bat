@echo off
REM ASCII only - see restart-pdfredactor.bat for why.
title PDF Redactor - Install Autostart
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment and installing dependencies...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Python not found in PATH.
        pause
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-autostart.ps1"
echo.
pause
