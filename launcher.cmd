@echo off
REM ============================================================================
REM Mistral Intelligence Monitor  (c) 2026 BalTac  |  v3.0.0  |  MIT License
REM ============================================================================
REM launcher.cmd -- Windows CMD wrapper
REM
REM Usage:
REM   launcher.cmd                        interactive menu
REM   launcher.cmd --stats --window 7d    one-liner passthrough
REM   launcher.cmd --models               model catalog
REM ============================================================================
setlocal enabledelayedexpansion
set SCRIPT_DIR=%~dp0
set MONITOR_PY=%SCRIPT_DIR%mistral_monitor\monitor.py

REM Find Python -- prefer project .venv, then uv tool, then system
set PYTHON=
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" set PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe
if "%PYTHON%"=="" if exist "%USERPROFILE%\AppData\Roaming\uv\tools\graphifyy\Scripts\python.exe" set PYTHON=%USERPROFILE%\AppData\Roaming\uv\tools\graphifyy\Scripts\python.exe
if "%PYTHON%"=="" where python >nul 2>&1 && set PYTHON=python
if "%PYTHON%"=="" echo ERROR: Python not found. && exit /b 1

REM Ensure rich is available -- inline check, no multi-line blocks
"%PYTHON%" -c "import rich" 2>nul
if errorlevel 1 echo Installing rich... && "%PYTHON%" -m pip install rich -q 2>nul

REM Passthrough mode: if args given, forward to monitor.py
if not "%~1"=="" "%PYTHON%" "%MONITOR_PY%" %* && exit /b %ERRORLEVEL%

REM No args -> interactive launcher
"%PYTHON%" "%SCRIPT_DIR%launcher.py"
exit /b %ERRORLEVEL%
