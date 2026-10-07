@echo off
rem Opens a window that builds Build\ditto.exe and publishes it as a GitHub release (tag vX.Y.Z from res\app.rc).
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\release.ps1"
