@echo off
rem Builds dist\PyDM-Setup-<version>.exe: PyInstaller bundles the app, Inno Setup wraps it.
rem Needs Inno Setup 6 (winget install JRSoftware.InnoSetup).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run.bat once first to create .venv
    exit /b 1
)
.venv\Scripts\python -m pip install -q -r requirements.txt -r packaging\requirements-build.txt || exit /b 1
.venv\Scripts\python tools\make_icons.py || exit /b 1
.venv\Scripts\pyinstaller packaging\pydm.spec --noconfirm --distpath build\dist --workpath build\work || exit /b 1

for /f "tokens=2 delims==" %%v in ('findstr /b "__version__" pydm\__init__.py') do set VERSION=%%~v
set VERSION=%VERSION: =%
set VERSION=%VERSION:"=%

set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
    echo Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup
    exit /b 1
)
"%ISCC%" /Q /DAppVersion=%VERSION% packaging\pydm.iss || exit /b 1
echo.
echo Built dist\PyDM-Setup-%VERSION%.exe
