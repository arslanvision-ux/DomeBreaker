@echo off
setlocal enabledelayedexpansion

title DomeBreaker - Command-Line Updater

echo =================================================================
echo    [+] DomeBreaker - Command-Line Auto-Updater (Windows)
echo =================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

where python >nul 2>nul
if %errorlevel% equ 0 (
    python "%SCRIPT_DIR%\scripts\update.py"
    goto :done
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    py "%SCRIPT_DIR%\scripts\update.py"
    goto :done
)

set "HYTHON_EXE="
for /d %%H in ("C:\Program Files\Side Effects Software\Houdini*") do (
    if exist "%%H\bin\hython.exe" (
        set "HYTHON_EXE=%%H\bin\hython.exe"
    )
)

if defined HYTHON_EXE (
    "!HYTHON_EXE!" "%SCRIPT_DIR%\scripts\update.py"
    goto :done
)

where git >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Python not found. Updating via git directly...
    cd /d "%SCRIPT_DIR%"
    git pull origin main
    goto :done
)

echo [ERROR] Could not find Python or Git to run updater.
echo Please run inside Houdini Command Line Tools or install Git/Python.

:done
echo.
echo Press any key to exit...
pause >nul