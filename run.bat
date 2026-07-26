@echo off
setlocal EnableDelayedExpansion

REM Launch Skribe using its dedicated venv.
REM
REM Common pitfalls we work around:
REM   1. Conda/miniconda's python.exe drags its site-packages into any
REM      venv it creates, leading to Shiboken6 DLL mismatches.
REM   2. The Microsoft Python launcher ('py -3') resolves to the
REM      highest registered Python. If a newer interpreter appears
REM      after the venv was created, pip installs wheels for it that
REM      don't match the venv's stdlib.
REM   3. Windows "embeddable" Python distributions ship without the
REM      full stdlib, so python -m venv creates a venv missing
REM      Lib\encodings\__init__.py and the interpreter immediately
REM      crashes with "Failed to import encodings module".
REM
REM We resolve all three by picking a clean interpreter, verifying it
REM can actually load its own stdlib, then rebuilding the venv if its
REM Python doesn't match.

REM -- Pick a Python interpreter ----------------------------------------
set "PY="

REM Prefer a stable, released Python via the Microsoft launcher. We
REM try known-good minor versions newest-first; PySide6 only ships
REM wheels for released Pythons, so we must avoid pre-release builds
REM like 3.15 that 'py -3' (highest installed) would otherwise pick.
where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.13 3.12 3.11 3.10) do (
        if "!PY!"=="" (
            py -%%V -c "import sys" >nul 2>nul
            if not errorlevel 1 set "PY=py -%%V"
        )
    )
    REM Last resort: whatever 'py -3' resolves to (may be pre-release).
    if "!PY!"=="" set "PY=py -3"
    goto :py_resolved
)

where python >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('where python') do (
        if "!PY!"=="" (
            echo %%i | findstr /i "miniconda conda anaconda" >nul
            if errorlevel 1 set "PY=python"
        )
    )
)

:py_resolved
if "%PY%"=="" goto :no_python

REM -- Verify the chosen interpreter can load its own stdlib -----------
REM If the base Python is the Windows embeddable distribution or is
REM otherwise broken, 'python -c "import encodings"' prints nothing and
REM exits non-zero. Bail out with a clear message rather than building
REM a venv from a broken interpreter.
%PY% -c "import encodings, sys; print(sys.version)" >nul 2>nul
if errorlevel 1 goto :bad_python

REM -- Resolve active Python version -----------------------------------
for /f "delims=" %%v in ('%PY% -c "import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))"') do set "ACTIVE_PY_VERSION=%%v"

set "VENV=%SKRIBE_VENV%"
if "%VENV%"=="" set "VENV=%USERPROFILE%\skribe\.venv"

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

REM -- Detect venv version drift ----------------------------------------
set "VENV_PY_VERSION="
if exist "%VENV%\pyvenv.cfg" (
    for /f "usebackq tokens=1,2 delims==" %%k in ("%VENV%\pyvenv.cfg") do (
        if /i "%%k"=="version" set "VENV_PY_VERSION=%%l"
    )
)

if defined VENV_PY_VERSION (
    for /f "tokens=1,2 delims=." %%a in ("%VENV_PY_VERSION%") do (
        if not "!ACTIVE_PY_VERSION!"=="%%a.%%b" (
            echo Venv built with %%a.%%b but active interpreter is !ACTIVE_PY_VERSION! -- rebuilding.
            rmdir /s /q "%VENV%"
        )
    )
)

REM -- Create the venv if missing ---------------------------------------
if exist "%VENV%\Scripts\python.exe" goto :venv_ready
echo Creating virtual environment at %VENV% using Python !ACTIVE_PY_VERSION!...
%PY% -m venv "%VENV%"
if errorlevel 1 goto :err
goto :venv_ready

:venv_ready

REM -- Verify the venv interpreter can load its stdlib ------------------
REM A healthy Windows venv does NOT copy the stdlib into Lib\encodings;
REM it shares the base interpreter's stdlib via pyvenv.cfg's 'home'.
REM So the only reliable health check is to actually run the venv's
REM python.exe and import encodings. If that fails, the base
REM interpreter's stdlib is unreachable — rebuild once, then give up.
"%VENV%\Scripts\python.exe" -c "import encodings" >nul 2>nul
if not errorlevel 1 goto :stdlib_ok
echo Venv interpreter cannot load its stdlib; rebuilding.
rmdir /s /q "%VENV%"
%PY% -m venv "%VENV%"
if errorlevel 1 goto :err
"%VENV%\Scripts\python.exe" -c "import encodings" >nul 2>nul
if errorlevel 1 goto :err_stdlib

:stdlib_ok

REM -- Force include-system-site-packages = false -----------------------
set "PYVENV_CFG=%VENV%\pyvenv.cfg"
if exist "%PYVENV_CFG%" (
    findstr /v /b "include-system-site-packages" "%PYVENV_CFG%" > "%PYVENV_CFG%.tmp"
    >> "%PYVENV_CFG%.tmp" echo include-system-site-packages = false
    move /y "%PYVENV_CFG%.tmp" "%PYVENV_CFG%" >nul
) else (
    > "%PYVENV_CFG%" echo include-system-site-packages = false
)

REM -- Install requirements --------------------------------------------
echo Ensuring Skribe dependencies are installed...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
if errorlevel 1 goto :err
"%VENV%\Scripts\python.exe" -m pip install -r "%HERE%\requirements.txt"
if errorlevel 1 goto :err

REM -- Isolate venv from any inherited PYTHONHOME / PYTHONPATH ---------
REM Forces the DLL search path used by Qt's Shiboken6 to resolve against
REM the venv's lib directory rather than the conda base.
set "PYTHONHOME=%VENV%"
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

REM -- Launch Skribe ----------------------------------------------------
"%VENV%\Scripts\python.exe" -m skribe %*
exit /b %ERRORLEVEL%

:no_python
echo Could not find a suitable Python interpreter.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and re-run this script. If you use conda, create a NEW conda
echo environment with Python 3.13 first, then run this script from a
echo regular cmd.exe window ^(not "Anaconda Prompt"^).
exit /b 1

:bad_python
echo The Python interpreter "%PY%" resolved to is missing its stdlib.
echo This typically means you registered the Windows "embeddable"
echo Python distribution ^(a python.org zip download^) with the
echo Microsoft launcher.  Embeddable distributions omit encodings and
echo most of the stdlib, so 'python -m venv' produces a broken venv.
echo.
echo Install the official Python 3.10+ from
echo https://www.python.org/downloads/windows/ via the .exe installer
echo ^(NOT the embeddable zip package^), then re-run this script.
exit /b 1

:err
echo pip install failed. Check your network connection and the
echo requirements.txt at %HERE%\requirements.txt.
exit /b 1

:err_stdlib
echo The venv's python.exe cannot import its stdlib, even after a
echo clean rebuild.  The base interpreter's stdlib is unreachable.
echo Install the official Python 3.10+ from
echo https://www.python.org/downloads/windows/ via the .exe installer
echo and re-run this script.
exit /b 1
