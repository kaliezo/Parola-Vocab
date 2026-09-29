@echo off
setlocal

pushd "%~dp0" >nul 2>&1
if errorlevel 1 goto folder_error

py -3 -c "import tkinter, sqlite3" >nul 2>&1
if not errorlevel 1 goto run_py

python -c "import tkinter, sqlite3" >nul 2>&1
if not errorlevel 1 goto run_python

echo Python 3 with Tkinter and SQLite was not found.
echo Install Python for Windows, then run this file again.
pause
popd
exit /b 1

:run_py
py -3 app.py
goto finished

:run_python
python app.py

:finished
set "app_exit=%errorlevel%"
if not "%app_exit%"=="0" (
    echo.
    echo Italian Vocabulary stopped with error code %app_exit%.
    pause
)
popd
exit /b %app_exit%

:folder_error
echo Could not open the Italian Vocabulary folder.
pause
exit /b 1
