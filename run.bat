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

REM Ensure piper voice model is available
set "VOICE_DIR=%USERPROFILE%\.local\share\piper\voices"
set "VOICE_FILE=%VOICE_DIR%\en_US-lessac-medium.onnx"
if not exist "%VOICE_FILE%" (
    if not exist "%VOICE_DIR%" mkdir "%VOICE_DIR%"
    call "%VENV%\Scripts\python.exe" -m piper.download_voices --download-dir "%VOICE_DIR%" en_US-lessac-medium
)

REM Launch Skribe
call "%VENV%\Scripts\python.exe" -m skribe %*