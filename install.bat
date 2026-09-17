@echo off
setlocal enabledelayedexpansion

title DomeBreaker - Windows Installer

echo =================================================================
echo    [DomeBreaker] Solaris USD Suite Installer (Windows)
echo =================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo [INFO] Installation Directory: %SCRIPT_DIR%
echo.

REM 1. Try Python Launcher py
py -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Found Python Launcher py. Running installer...
    py "%SCRIPT_DIR%\scripts\install.py" %*
    goto :done
)

REM 2. Try hython in standard SideFX directories
set "HYTHON_EXE="
for /d %%H in ("C:\Program Files\Side Effects Software\Houdini*") do (
    if exist "%%H\bin\hython.exe" set "HYTHON_EXE=%%H\bin\hython.exe"
)
if defined HYTHON_EXE (
    echo [INFO] Found Houdini Python: "!HYTHON_EXE!"
    "!HYTHON_EXE!" "%SCRIPT_DIR%\scripts\install.py" %*
    goto :done
)

REM 3. Try python in PATH (verify execution directly to avoid WindowsApps stub)
python -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Found Python in PATH. Running installer...
    python "%SCRIPT_DIR%\scripts\install.py" %*
    goto :done
)

REM 4. Fallback: PowerShell package registrar
goto :run_powershell_fallback

:run_powershell_fallback
echo [INFO] Python not found. Running PowerShell package registrar...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$src = '%SCRIPT_DIR%\houdini\packages\domebreaker.json'; $dirs = Get-ChildItem \"$HOME\Documents\", \"$HOME\OneDrive\Documents\" -Directory -Filter 'houdini*' -ErrorAction SilentlyContinue; foreach ($d in $dirs) { $pkg = Join-Path $d.FullName 'packages'; if (-not (Test-Path $pkg)) { New-Item -ItemType Directory -Path $pkg -Force | Out-Null }; Copy-Item $src (Join-Path $pkg 'domebreaker.json') -Force; Write-Host \"[SUCCESS] Registered: $(Join-Path $pkg 'domebreaker.json')\" }; Write-Host ''; Write-Host '================================================================='; Write-Host '[SUCCESS] DomeBreaker registered into Houdini packages!'; Write-Host '================================================================='"
goto :done

:done
echo.
pause
