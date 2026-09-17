@echo off
setlocal enabledelayedexpansion

title DomeBreaker - Windows Auto-Updater

echo =================================================================
echo    [DomeBreaker] Command-Line Auto-Updater (Windows)
echo =================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM 1. Try Python Launcher py
py -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    py "%SCRIPT_DIR%\scripts\update.py" %*
    goto :done
)

REM 2. Try hython
set "HYTHON_EXE="
for /d %%H in ("C:\Program Files\Side Effects Software\Houdini*") do (
    if exist "%%H\bin\hython.exe" set "HYTHON_EXE=%%H\bin\hython.exe"
)
if defined HYTHON_EXE (
    "!HYTHON_EXE!" "%SCRIPT_DIR%\scripts\update.py" %*
    goto :done
)

REM 3. Try python in PATH
python -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    python "%SCRIPT_DIR%\scripts\update.py" %*
    goto :done
)

REM 4. Fallback if git is available
where git >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Python not found. Updating via git pull...
    cd /d "%SCRIPT_DIR%"
    git pull origin main
    goto :done
)

echo [ERROR] Could not find Python or Git to perform auto-update.
echo Please update manually or visit: https://github.com/arslanvision-ux/DomeBreaker

:done
echo.
pause
