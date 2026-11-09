@echo off
REM ============================================================
REM  book2audio - Windows setup & environment initializer
REM  Run from inside book2audio/:  install.bat
REM ============================================================
setlocal
echo.
echo === book2audio setup ===
echo.

REM ---- Python check ----
where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python not found. Install Python 3.12 or 3.13 (ARM64 recommended):
    echo     https://www.python.org/downloads/
    echo     ^(Make sure to tick "Add python.exe to PATH"^)
    goto :fail
)
python --version

REM ---- Virtual environment creation & activation ----
if not exist .venv (
    echo [+] Creating virtual environment in .venv...
    python -m venv .venv || goto :fail
)
echo [+] Activating virtual environment...
call .venv\Scripts\activate.bat

REM ---- Pip upgrade & dependencies ----
echo [+] Upgrading pip...
python -m pip install --upgrade pip >nul
echo [+] Installing core dependencies from requirements.txt...
pip install -r requirements.txt || goto :fail

REM ---- Optional Dev / Test dependencies ----
set "WANT_DEV="
set /p WANT_DEV="Install developer & testing tools (pytest)? [y/N] "
if /i "%WANT_DEV%"=="y" (
    echo [+] Installing test suite dependencies...
    pip install -r requirements-dev.txt || echo [!] Dev install skipped.
)

REM ---- Environment verification ----
echo.
echo [+] Running environment doctor check...
python book2audio.py doctor

echo.
echo === Setup Complete ===
echo To run:
echo   call .venv\Scripts\activate.bat
echo   python book2audio.py demo
echo.
goto :eof

:fail
echo.
echo [X] Setup failed - see messages above.
exit /b 1
