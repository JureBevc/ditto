@echo off
rem Launches ditto without leaving a console window open.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0ditto.py" %*
    exit /b
)
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0ditto.py" %*
    exit /b
)
start "" python "%~dp0ditto.py" %*
