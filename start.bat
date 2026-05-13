@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul

echo.
echo ========================================
echo   Train Simulation Startup
echo ========================================
echo.

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

echo.
cd /d "%PROJECT_ROOT%"
call "%PROJECT_ROOT%\restart-servers.bat"

exit /b %ERRORLEVEL%
