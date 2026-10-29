@echo off
REM ASCII only. All Hebrew output comes from the PowerShell script -
REM cmd.exe mangles UTF-8 Hebrew in .bat files and breaks parsing.
title PDF Redactor - Restart
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart.ps1"
echo.
pause
