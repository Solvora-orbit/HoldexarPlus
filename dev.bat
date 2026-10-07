@echo off
setlocal
title HoldexarPlus (dev)

rem Dev launcher. Runtime data never lands in the git worktree: data goes to
rem %LOCALAPPDATA%\holdexar-dev, which is separate from the packaged app's
rem %LOCALAPPDATA%\holdexar on purpose (a dev-mode schema migration must not be
rem able to touch real user data).
rem The full resolution order lives in server\app\core\paths.py.
rem
rem Default mode is --dev (no build): run.py auto-spawns Vite with hot reload
rem and wires the window to it. Extra flags pass through unchanged.

cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] python not found in PATH.
    echo         Install Python 3.11+ from https://www.python.org/downloads/
    echo         and check "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

rem Only set the env var when this checkout has no database of its own. The env
rem var outranks the "database already sits next to the program" rule, so setting
rem it unconditionally would make an older checkout look like it lost its data.
if exist "%~dp0data\holdexar.db" (
    echo [INFO] data\holdexar.db found in this checkout - using it in place
) else (
    set "HOLDEXAR_DATA_DIR=%LOCALAPPDATA%\holdexar-dev"
    echo [INFO] dev data dir: %LOCALAPPDATA%\holdexar-dev
)

rem No args -> default to the no-build dev flow (Vite hot reload); any explicit
rem flags pass through untouched (e.g. "dev.bat --server", "dev.bat --build").
if "%~1"=="" (
    python run.py --dev
) else (
    python run.py %*
)
set EXITCODE=%errorlevel%

if %EXITCODE% neq 0 (
    echo.
    echo [EXIT] code %EXITCODE% - scroll up for the error above.
    pause
)
endlocal
