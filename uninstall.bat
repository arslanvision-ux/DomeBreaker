@echo off
setlocal enabledelayedexpansion

title DomeBreaker - Windows Uninstaller

echo =================================================================
echo    🗑️  DomeBreaker - Solaris USD Suite Uninstaller (Windows)
echo =================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: 1. Try python in PATH
where python >nul 2>nul
if %errorlevel% equ 0 (
    python "%SCRIPT_DIR%\scripts\install.py" --uninstall
    goto :done
)

:: 2. Try py launcher
where py >nul 2>nul
if %errorlevel% equ 0 (
    py "%SCRIPT_DIR%\scripts\install.py" --uninstall
    goto :done
)

:: 3. Try finding hython in standard SideFX install directory
set "HYTHON_EXE="
for /d %%H in ("C:\Program Files\Side Effects Software\Houdini*") do (
    if exist "%%H\bin\hython.exe" (
        set "HYTHON_EXE=%%H\bin\hython.exe"
    )
)

if defined HYTHON_EXE (
    "!HYTHON_EXE!" "%SCRIPT_DIR%\scripts\install.py" --uninstall
    goto :done
)

:: 4. Fallback: Pure batch uninstaller
echo [INFO] Running pure batch uninstallation...
set "REMOVED=0"

for /d %%D in ("%USERPROFILE%\Documents\houdini*" "%USERPROFILE%\OneDrive\Documents\houdini*") do (
    if exist "%%D\packages\domebreaker.json" (
        del /f /q "%%D\packages\domebreaker.json"
        echo [SUCCESS] Removed: %%D\packages\domebreaker.json
        set /a REMOVED+=1
    )
    if exist "%%D\packages\hdri_match_solaris.json" (
        del /f /q "%%D\packages\hdri_match_solaris.json"
        echo [SUCCESS] Removed legacy: %%D\packages\hdri_match_solaris.json
        set /a REMOVED+=1
    )
)

echo.
echo =================================================================
if %REMOVED% gtr 0 (
    echo 🎉 DomeBreaker uninstalled from %REMOVED% location(s).
) else (
    echo [INFO] No DomeBreaker package files found to remove.
)
echo =================================================================

:done
echo.
pause
