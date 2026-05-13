@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo.
echo ============================================
echo   Train Simulation Server Restart
echo ============================================
echo.

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "VENV_NAME_FILE=%PROJECT_ROOT%\.venv_name"
set "VENV_ROOT=%PROJECT_ROOT%\.venvs"
if exist "%VENV_NAME_FILE%" (
    set /p VENV_NAME=<"%VENV_NAME_FILE%"
) else (
    set "VENV_NAME=train-generative-sim"
)
if "%VENV_NAME%"=="" set "VENV_NAME=train-generative-sim"
set "LEGACY_VENV_PATH=%PROJECT_ROOT%\%VENV_NAME%"
set "VENV_PATH=%VENV_ROOT%\%VENV_NAME%"
if exist "%LEGACY_VENV_PATH%\Scripts\python.exe" set "VENV_PATH=%LEGACY_VENV_PATH%"
call :select_venv
set "LOCAL_NODE_DIR=%PROJECT_ROOT%\.tools\node"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

echo [1/5] Stopping existing project processes...
echo.

call :stop_project_processes
if errorlevel 1 (
    echo ERROR: Failed to stop existing project processes.
    pause
    exit /b 1
)

timeout /t 2 /nobreak > nul

echo.
echo [2/5] Preparing backend environment...
echo.

cd /d "%PROJECT_ROOT%"
echo Using virtual environment: %VENV_PATH%
if not exist "%VENV_PATH%\Scripts\python.exe" (
    where python > nul 2> nul
    if errorlevel 1 (
        echo ERROR: python was not found. Install Python 3.10 or later and retry.
        pause
        exit /b 1
    )
    echo Creating virtual environment: %VENV_PATH%
    if not exist "%VENV_ROOT%" mkdir "%VENV_ROOT%"
    python -m venv "%VENV_PATH%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    if not exist "%VENV_NAME_FILE%" (
        <nul set /p "VENV_FILE_CONTENT=%VENV_NAME%">"%VENV_NAME_FILE%"
    )
)

"%VENV_PATH%\Scripts\python.exe" -c "import fastapi, uvicorn" > nul 2> nul
if errorlevel 1 (
    echo Installing backend dependencies...
    "%VENV_PATH%\Scripts\python.exe" -m pip install -r "%PROJECT_ROOT%\backend\requirements.txt"
    if errorlevel 1 (
        echo ERROR: Failed to install backend dependencies.
        echo If this machine is offline, run setup.bat once while connected to the internet.
        pause
        exit /b 1
    )
)

echo.
echo [3/5] Preparing frontend environment...
echo.

where npm > nul 2> nul
if errorlevel 1 (
    if exist "%LOCAL_NODE_DIR%\node.exe" if exist "%LOCAL_NODE_DIR%\npm.cmd" (
        set "PATH=%LOCAL_NODE_DIR%;%PATH%"
    )
)

where npm > nul 2> nul
if errorlevel 1 (
    echo ERROR: npm was not found. Run setup.bat first, or install Node.js 18 or later and retry.
    pause
    exit /b 1
)

set "FRONTEND_DEPS_OK=0"
if exist "%PROJECT_ROOT%\frontend\node_modules\.bin\vite.cmd" (
    pushd "%PROJECT_ROOT%\frontend"
    call npm ls --depth=0 > nul 2> nul
    if not errorlevel 1 set "FRONTEND_DEPS_OK=1"
    popd
)

if "%FRONTEND_DEPS_OK%"=="0" (
    echo Installing/updating frontend dependencies...
    pushd "%PROJECT_ROOT%\frontend"
    call npm install
    if errorlevel 1 (
        popd
        echo ERROR: Failed to install frontend dependencies.
        pause
        exit /b 1
    )
    popd
)

echo.
echo [4/5] Starting servers...
echo.

set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"
start "Backend" /D "%PROJECT_ROOT%" "%VENV_PATH%\Scripts\python.exe" -m uvicorn backend.main:app --reload --host 127.0.0.1 --port %BACKEND_PORT%
start "Frontend" /D "%PROJECT_ROOT%\frontend" cmd /k npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort

echo.
echo [5/5] Checking servers...
echo.

call :wait_for_port "%BACKEND_PORT%" 30
if errorlevel 1 (
    echo ERROR: Backend failed to start on http://localhost:%BACKEND_PORT%
    pause
    exit /b 1
) else (
    echo OK: Backend running on http://localhost:%BACKEND_PORT%
)

call :wait_for_port "%FRONTEND_PORT%" 30
if errorlevel 1 (
    echo ERROR: Frontend failed to start on http://localhost:%FRONTEND_PORT%
    pause
    exit /b 1
) else (
    echo OK: Frontend running on http://localhost:%FRONTEND_PORT%
)

echo.
echo ============================================
echo   All servers started successfully!
echo ============================================
echo.
echo Backend:  http://localhost:%BACKEND_PORT%
echo Frontend: http://localhost:%FRONTEND_PORT%
echo.

echo Opening browser...
start http://localhost:%FRONTEND_PORT%

echo.
pause
exit /b 0

:stop_project_processes
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\stop-project-processes.ps1" -ProjectRoot "%PROJECT_ROOT%" -Ports "%BACKEND_PORT%,%FRONTEND_PORT%"
exit /b %ERRORLEVEL%

:select_venv
if exist "%VENV_PATH%\Scripts\python.exe" (
    "%VENV_PATH%\Scripts\python.exe" -c "import fastapi, uvicorn" > nul 2> nul
    if not errorlevel 1 exit /b 0
)

for %%p in ("%PROJECT_ROOT%\.venv" "%PROJECT_ROOT%\venv" "%LEGACY_VENV_PATH%" "%VENV_ROOT%\%VENV_NAME%") do (
    if exist "%%~p\Scripts\python.exe" (
        "%%~p\Scripts\python.exe" -c "import fastapi, uvicorn" > nul 2> nul
        if not errorlevel 1 (
            set "VENV_PATH=%%~p"
            exit /b 0
        )
    )
)

if exist "%VENV_PATH%\Scripts\python.exe" exit /b 0

for %%p in ("%PROJECT_ROOT%\.venv" "%PROJECT_ROOT%\venv" "%LEGACY_VENV_PATH%" "%VENV_ROOT%\%VENV_NAME%") do (
    if exist "%%~p\Scripts\python.exe" (
        set "VENV_PATH=%%~p"
        exit /b 0
    )
)
exit /b 0

:wait_for_port
set "PORT=%~1"
set "RETRIES=%~2"
for /l %%i in (1,1,%RETRIES%) do (
    netstat -ano 2> nul | findstr /R /C:":%PORT% .*LISTENING" > nul
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak > nul
)
exit /b 1
