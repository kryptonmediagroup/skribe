@echo off
REM Launch Skribe using its dedicated venv.
setlocal

REM Pick a Python launcher. Prefer the Windows launcher (`py -3`); fall back
REM to `python` if it isn't installed. Then make sure either one is on PATH.
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL%==0 (
        set "PY=python"
    ) else (
        echo Python is not on PATH. Install Python 3.10 or newer from
        echo https://www.python.org/downloads/ and re-run this script.
        exit /b 1
    )
)

set "VENV=%SKRIBE_VENV%"
if "%VENV%"=="" set "VENV=%USERPROFILE%\skribe\.venv"

set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

REM Create venv on first run.
if not exist "%VENV%" (
    echo Creating virtual environment at %VENV%...
    %PY% -m venv "%VENV%"
    if errorlevel 1 exit /b 1
)

REM Always (re)install requirements. pip is idempotent — already-satisfied
REM packages print a single line and skip download/install, so this is
REM fast on subsequent runs but picks up additions and upgrades to the
REM pinned versions in requirements.txt.
echo Ensuring Skribe dependencies are installed...
call "%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
if errorlevel 1 exit /b 1
call "%VENV%\Scripts\python.exe" -m pip install -r "%HERE%\requirements.txt"
if errorlevel 1 exit /b 1

REM KittenTTS downloads its voice model from Hugging Face on first use, so no
REM voice bootstrap step is needed here.

REM Launch Skribe.
call "%VENV%\Scripts\python.exe" -m skribe %*
