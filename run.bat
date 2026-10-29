@echo off
rem Launch PyDM. Creates .venv on first run and reinstalls packages when requirements.txt changes.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo First run: setting up .venv ...
    py -m venv .venv || exit /b 1
)
fc /b requirements.txt .venv\requirements.installed >nul 2>&1 || (
    echo Installing packages ...
    .venv\Scripts\python -m pip install -q -r requirements.txt || exit /b 1
    copy /y requirements.txt .venv\requirements.installed >nul
)
start "" .venv\Scripts\pythonw.exe -m pydm
