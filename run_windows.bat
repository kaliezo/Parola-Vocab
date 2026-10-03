@echo off
setlocal
pushd "%~dp0" >nul 2>&1
if errorlevel 1 goto folder_error

if exist ".venv\Scripts\python.exe" (
    set "app_python=.venv\Scripts\python.exe"
    goto check_python
)
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "app_python=py -3"
    goto check_python
)
python -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "app_python=python"
    goto check_python
)
echo Python 3 was not found. Install Python 3.10 or newer, then try again.
goto failed

:check_python
%app_python% -c "import PySide6, sqlite3" >nul 2>&1
if errorlevel 1 (
    echo PySide6 is missing. In this folder run:
    echo   py -3 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.lock
    goto failed
)
%app_python% qt_app.py
set "app_exit=%errorlevel%"
if not "%app_exit%"=="0" goto failed_code
popd
exit /b 0

:failed_code
echo Parola Vocab stopped with error code %app_exit%.
pause
popd
exit /b %app_exit%
:failed
pause
popd
exit /b 1
:folder_error
echo Could not open the Parola Vocab folder.
pause
exit /b 1
