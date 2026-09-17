@echo off
setlocal enabledelayedexpansion

title DomeBreaker - Windows Installer

echo =================================================================
echo    [+] DomeBreaker - Solaris USD Suite Installer (Windows)
echo =================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "FORWARD_DIR=%SCRIPT_DIR:\=/%"

echo [INFO] Installation Directory: %SCRIPT_DIR%
echo.

where python >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Found Python in PATH. Running installer...
    python "%SCRIPT_DIR%\scripts\install.py"
    goto :done
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Found Python Launcher (py). Running installer...
    py "%SCRIPT_DIR%\scripts\install.py"
    goto :done
)

echo [INFO] Python not found in system PATH. Searching for Houdini hython...
set "HYTHON_EXE="
for /d %%H in ("C:\Program Files\Side Effects Software\Houdini*") do (
    if exist "%%H\bin\hython.exe" (
        set "HYTHON_EXE=%%H\bin\hython.exe"
    )
)

if defined HYTHON_EXE (
    echo [INFO] Found Houdini Python: "!HYTHON_EXE!"
    "!HYTHON_EXE!" "%SCRIPT_DIR%\scripts\install.py"
    goto :done
)

echo [INFO] Running pure batch package installation...
set "COUNT=0"

for /d %%D in ("%USERPROFILE%\Documents\houdini*" "%USERPROFILE%\OneDrive\Documents\houdini*") do (
    if exist "%%D" (
        set "PKG_DIR=%%D\packages"
        if not exist "!PKG_DIR!" mkdir "!PKG_DIR!"
        
        (
            echo {
            echo     "env": [
            echo         {
            echo             "DOMEBREAKER_ROOT": "%FORWARD_DIR%"
            echo         },
            echo         {
            echo             "HDRI_MATCH_SOLARIS_ROOT": "$DOMEBREAKER_ROOT"
            echo         },
            echo         {
            echo             "PYTHONPATH": {
            echo                 "value": [
            echo                     "$DOMEBREAKER_ROOT/python"
            echo                 ],
            echo                 "method": "append"
            echo             }
            echo         }
            echo     ],
            echo     "path": [
            echo         "$DOMEBREAKER_ROOT/houdini"
            echo     ],
            echo     "houdini_version": ">= 19.5",
            echo     "description": "DomeBreaker - Solaris USD Lighting & Environment Suite"
            echo }
        ) > "!PKG_DIR!\domebreaker.json"
        
        echo [SUCCESS] Registered: !PKG_DIR!\domebreaker.json
        set /a COUNT+=1
    )
)

if %COUNT% gtr 0 (
    echo.
    echo =================================================================
    echo [SUCCESS] DomeBreaker installed successfully into %COUNT% Houdini version(s)!
    echo =================================================================
) else (
    echo [WARNING] No Houdini preference folders detected in Documents.
    echo Please ensure Houdini has been run at least once on this machine.
)

:done
echo.
echo Press any key to exit...
pause >nul