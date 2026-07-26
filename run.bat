@echo off
REM Launch Skribe using its dedicated venv.
setlocal EnableDelayedExpansion

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

REM Resolve the active Python's major.minor (e.g. "3.13"). We rebuild
REM the venv whenever it disagrees with whatever currently exists —
REM mixing 3.13 stdlib with 3.14 wheels (or vice versa) yields a fatal
REM "Failed to import encodings module" at the next launch.
set "ACTIVE_PY_VERSION="
for /f "delims=" %%v in ('%PY% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set "ACTIVE_PY_VERSION=%%v"

set "VENV_PY_VERSION="
if exist "%VENV%\pyvenv.cfg" (
    for /f "tokens=* eol=#" %%c in ('type "%VENV%\pyvenv.cfg"') do (
        if "!VENV_PY_VERSION!"=="" (
            for /f "tokens=1,2 delims== " %%k in ("%%c") do (
                if /i "%%k"=="version" set "VENV_PY_VERSION=%%l"
            )
        )
    )
)

if not "!VENV_PY_VERSION!"=="" (
    REM 'version = 3.13.5' -> keep the leading '3.13'; ignore patch.
    for /f "tokens=1,2 delims=." %%a in ("!VENV_PY_VERSION!") do (
        if not "!ACTIVE_PY_VERSION!"=="%%a.%%b" (
            echo Venv was built with Python !VENV_PY_VERSION! but the active interpreter is !ACTIVE_PY_VERSION! -- recreating.
            rmdir /s /q "%VENV%"
        )
    )
)

REM Create the venv if it's missing or was just wiped.
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating virtual environment at %VENV% using Python !ACTIVE_PY_VERSION!...
    %PY% -m venv "%VENV%"
    if errorlevel 1 exit /b 1
)

REM Sanity-check the venv's stdlib. If its 'encodings' module is
REM missing — e.g., because the venv dir was preserved but its
REM stdlib wasn't — rebuild cleanly.
if not exist "%VENV%\Lib\encodings\__init__.py" (
    echo Venv appears incomplete (missing stdlib); recreating...
    rmdir /s /q "%VENV%"
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
