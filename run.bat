@echo off
REM ASCII only - cmd.exe mangles UTF-8 Hebrew in .bat files.
REM Manual launch. For automatic startup on boot, run install-autostart.bat once.
title PDF Redactor
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [1/2] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo ERROR: Python is not installed or not in PATH.
        pause
        exit /b 1
    )
    echo [2/2] Installing dependencies...
    venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
)

echo.
echo ==========================================
echo   PDF Redactor
echo   http://127.0.0.1:8477
echo   Local only - nothing leaves this computer
echo.
echo   Close this window to stop the server.
echo ==========================================
echo.

venv\Scripts\python.exe app.py
pause
