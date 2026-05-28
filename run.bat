@echo off
REM Launch Skribe using its dedicated venv.
setlocal

set "VENV=%SKRIBE_VENV%"
if "%VENV%"=="" set "VENV=%USERPROFILE%\skribe\.venv"

set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

REM Create venv and install dependencies on first run
if not exist "%VENV%" (
    echo Creating virtual environment at %VENV%...
    python -m venv "%VENV%"
    call "%VENV%\Scripts\pip.exe" install --upgrade pip
    call "%VENV%\Scripts\pip.exe" install -r "%HERE%\requirements.txt"
    echo Virtual environment ready.
)

REM KittenTTS downloads its voice model from Hugging Face on first use, so no
REM voice bootstrap step is needed here.

REM Launch Skribe
call "%VENV%\Scripts\python.exe" -m skribe %*