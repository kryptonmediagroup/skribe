@echo off
REM Launch Skribe using its dedicated venv.
setlocal

REM Pick a Python interpreter. We avoid conda's python.exe because it
REM drags site-packages from the conda base into any venv it creates,
REM leading to DLL mismatches (e.g. Shiboken6). Prefer the Microsoft
REM Python launcher (`py -3`) which always resolves to the official
REM python.org interpreter; fall back to plain `python` if `py` isn't
REM installed, skipping any conda/miniconda install on PATH.
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :py_resolved
)

where python >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('where python') do (
        echo %%i | findstr /i "miniconda conda anaconda" >nul
        if errorlevel 1 (
            set "PY=python"
            goto :py_resolved
        )
    )
)

:py_resolved
if "%PY%"=="" (
    echo Could not find a suitable Python interpreter.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/
    echo and re-run this script. If you use conda, create a NEW conda
    echo environment with Python 3.13 first, then run this script from a
    echo regular cmd.exe window ^(not "Anaconda Prompt"^).
    exit /b 1
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

REM Force the venv to ignore system site-packages so a conda-forge or
REM miniconda base Python doesn't leak DLLs (Shiboken6, Qt, etc.) into
REM our PySide6 install. If pyvenv.cfg says otherwise, rewrite it.
set "PYVENV_CFG=%VENV%\pyvenv.cfg"
if exist "%PYVENV_CFG%" (
    findstr /v /b "include-system-site-packages" "%PYVENV_CFG%" > "%PYVENV_CFG%.tmp"
    echo include-system-site-packages = false >> "%PYVENV_CFG%.tmp"
    move /y "%PYVENV_CFG%.tmp" "%PYVENV_CFG%" >nul
) else (
    echo include-system-site-packages = false > "%PYVENV_CFG%"
)

REM Always (re)install requirements. pip is idempotent — already-satisfied
REM packages print a single line and skip download/install, so this is
REM fast on subsequent runs but picks up additions and upgrades to the
REM pinned versions in requirements.txt.
echo Ensuring Skribe dependencies are installed...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -m pip install -r "%HERE%\requirements.txt"
if errorlevel 1 exit /b 1

REM Strip any inherited PYTHONHOME / PYTHONPATH so the venv Python
REM cannot reach back into the conda base. PYTHONHOME tells Python
REM where the stdlib lives; pointing it at the venv fixes the DLL
REM resolution path used by Qt's Shiboken6.
set "PYTHONHOME=%VENV%"
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

REM Launch Skribe.
"%VENV%\Scripts\python.exe" -m skribe %*
