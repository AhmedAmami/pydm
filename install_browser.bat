@echo off
rem Register PyDM's native messaging host with Chrome and Edge.
rem Pass --uninstall to remove it.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -m venv .venv || exit /b 1
    .venv\Scripts\python -m pip install -q -r requirements.txt || exit /b 1
)
.venv\Scripts\python -m pydm.install_browser %*
pause
