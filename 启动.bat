@echo off
rem DeepSeek local Q&A launcher (no console window, use pythonw)
cd /d "%~dp0"

rem 优先使用 PATH 中的 pythonw（无控制台窗口），找不到则回退到 python
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0main.py"
) else (
    start "" python "%~dp0main.py"
)
