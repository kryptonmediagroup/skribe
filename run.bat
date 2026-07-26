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
REM
REM We resolve both problems by picking a clean interpreter, then
REM rebuilding the venv if its Python doesn't match.

REM -- Pick a Python interpreter ----------------------------------------
set "PY="

where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
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

REM -- Verify venv stdlib is intact -------------------------------------
if exist "%VENV%\Lib\encodings\__init__.py" goto :stdlib_ok
echo Venv stdlib is missing; rebuilding.
rmdir /s /q "%VENV%"
%PY% -m venv "%VENV%"
if errorlevel 1 goto :err
if not exist "%VENV%\Lib\encodings\__init__.py" goto :err_stdlib

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

:err
echo pip install failed. Check your network connection and the
echo requirements.txt at %HERE%\requirements.txt.
exit /b 1

:err_stdlib
echo venv creation succeeded but %VENV%\Lib\encodings is missing.
echo Delete %VENV% by hand and re-run this script.
exit /b 1
