@echo off
rem DeepSeek local Q&A launcher (no console window, use pythonw)
cd /d "%~dp0"

rem Activate venv first so API keys configured via scripts\setup_venv_keys.ps1 are loaded
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"

rem Prefer venv pythonw (no console window); fall back to PATH pythonw/python
set "LAUNCHER=python"
if exist ".venv\Scripts\pythonw.exe" set "LAUNCHER=.venv\Scripts\pythonw.exe"
if exist "venv\Scripts\pythonw.exe" set "LAUNCHER=venv\Scripts\pythonw.exe"
if "%LAUNCHER%"=="python" (
    where pythonw >nul 2>nul
    if not errorlevel 1 set "LAUNCHER=pythonw"
)
start "" "%LAUNCHER%" "%~dp0main.py"
