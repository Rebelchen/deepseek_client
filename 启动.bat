@echo off
rem DeepSeek local Q&A launcher (no console window, use pythonw)
cd /d "%~dp0"
set "PYW=D:\py\3.14\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0main.py"
