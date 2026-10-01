@echo off
rem TiaGen launcher. Runs the bundled Python only - never one from PATH - so
rem nothing else installed on this machine can change what runs.
setlocal
set "TIAGEN_HOME=%~dp0"
set "TIAGEN_PY=%TIAGEN_HOME%python\python.exe"

if not exist "%TIAGEN_PY%" (
    echo The bundled Python is missing: "%TIAGEN_PY%"
    echo The folder is incomplete. Extract the whole zip again - if it happens twice,
    echo antivirus is removing files and IT needs to allow this folder.
    exit /b 2
)

rem Double-clicked with no arguments: show help and wait, instead of a window
rem that flashes and closes before anyone can read it.
if "%~1"=="" (
    "%TIAGEN_PY%" -m tiagen --help
    echo.
    echo Start with:  tiagen selftest
    pause
    exit /b 0
)

"%TIAGEN_PY%" -m tiagen %*
exit /b %ERRORLEVEL%
