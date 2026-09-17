@echo off
setlocal enabledelayedexpansion

title DomeBreaker - Windows Uninstaller

echo =================================================================
echo    [DomeBreaker] Solaris USD Suite Uninstaller (Windows)
echo =================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM 1. Try Python Launcher py
py -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Found Python Launcher py. Running uninstaller...
    py "%SCRIPT_DIR%\scripts\install.py" --uninstall %*
    goto :done
)

REM 2. Try hython in standard SideFX directories
set "HYTHON_EXE="
for /d %%H in ("C:\Program Files\Side Effects Software\Houdini*") do (
    if exist "%%H\bin\hython.exe" set "HYTHON_EXE=%%H\bin\hython.exe"
)
if defined HYTHON_EXE (
    echo [INFO] Found Houdini Python: "!HYTHON_EXE!"
    "!HYTHON_EXE!" "%SCRIPT_DIR%\scripts\install.py" --uninstall %*
    goto :done
)

REM 3. Try python in PATH
python -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Found Python in PATH. Running uninstaller...
    python "%SCRIPT_DIR%\scripts\install.py" --uninstall %*
    goto :done
)

REM 4. Fallback: PowerShell uninstaller
goto :run_powershell_uninstall

:run_powershell_uninstall
echo [INFO] Running native Windows PowerShell uninstaller...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dirs = Get-ChildItem \"$HOME\Documents\", \"$HOME\OneDrive\Documents\" -Directory -Filter 'houdini*' -ErrorAction SilentlyContinue; $count = 0; foreach ($d in $dirs) { $p = Join-Path $d.FullName 'packages\domebreaker.json'; if (Test-Path $p) { Remove-Item $p -Force; Write-Host \"[SUCCESS] Removed: $p\"; $count++ } }; if ($count -gt 0) { Write-Host ''; Write-Host '================================================================='; Write-Host \"[SUCCESS] DomeBreaker uninstalled from $count Houdini version(s)!\"; Write-Host '=================================================================' } else { Write-Host '[INFO] No DomeBreaker installations found.' }"
goto :done

:done
echo.
pause
