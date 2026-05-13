@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo.
echo ========================================
echo   Train Simulation 環境セットアップ
echo ========================================
echo.

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "VENV_NAME_FILE=%PROJECT_ROOT%\.venv_name"
set "VENV_ROOT=%PROJECT_ROOT%\.venvs"
set "LOCAL_NODE_DIR=%PROJECT_ROOT%\.tools\node"

if exist "%VENV_NAME_FILE%" (
    set /p DEFAULT_VENV_NAME=<"%VENV_NAME_FILE%"
) else (
    set "DEFAULT_VENV_NAME=train-generative-sim"
)
if "%DEFAULT_VENV_NAME%"=="" set "DEFAULT_VENV_NAME=train-generative-sim"

set /p "VENV_NAME=仮想環境の名前を入力してください（Enter: %DEFAULT_VENV_NAME%）: "
if "%VENV_NAME%"=="" set "VENV_NAME=%DEFAULT_VENV_NAME%"
set "LEGACY_VENV_PATH=%PROJECT_ROOT%\%VENV_NAME%"
set "VENV_PATH=%VENV_ROOT%\%VENV_NAME%"
if exist "%LEGACY_VENV_PATH%\Scripts\python.exe" set "VENV_PATH=%LEGACY_VENV_PATH%"

echo.
echo [1/5] 必要なツールの確認...
where python > nul 2> nul
if errorlevel 1 (
    echo [ERROR] python が見つかりません。Python 3.10 以上をインストールしてから再実行してください。
    goto :fail
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" > nul 2> nul
if errorlevel 1 (
    echo [ERROR] Python 3.10 以上が必要です。
    python --version
    goto :fail
)

call :ensure_node
if errorlevel 1 goto :fail

echo   Python:
python --version
echo   Node.js:
node -v
echo   npm:
call npm -v

echo.
echo [2/5] 仮想環境の準備...
if exist "%VENV_PATH%\Scripts\python.exe" (
    echo   既存の仮想環境を使用します: %VENV_PATH%
) else (
    if not exist "%VENV_ROOT%" mkdir "%VENV_ROOT%"
    python -m venv "%VENV_PATH%"
    if errorlevel 1 goto :venv_error
    if not exist "%VENV_PATH%\Scripts\python.exe" goto :venv_error
)

> "%VENV_NAME_FILE%" echo %VENV_NAME%

call "%VENV_PATH%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] 仮想環境の有効化に失敗しました。
    goto :fail
)

python -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip のアップグレードに失敗しました。
    goto :fail
)

echo.
echo [3/5] バックエンド依存関係のインストール...
python -m pip install -r "%PROJECT_ROOT%\backend\requirements.txt"
if errorlevel 1 (
    echo [ERROR] バックエンド依存関係のインストールに失敗しました。
    goto :fail
)

echo.
echo [4/5] フロントエンド依存関係のインストール...
pushd "%PROJECT_ROOT%\frontend"
if errorlevel 1 (
    echo [ERROR] frontend ディレクトリに移動できません。
    goto :fail
)
call npm install
set "NPM_INSTALL_EXIT=%ERRORLEVEL%"
popd
if not "%NPM_INSTALL_EXIT%"=="0" (
    echo [ERROR] フロントエンド依存関係のインストールに失敗しました。
    goto :fail
)

echo.
echo [5/5] セットアップ完了
echo.
echo 次回以降は restart-servers.bat を実行してください。
echo.
pause
exit /b 0

:ensure_node
where node > nul 2> nul
if not errorlevel 1 (
    where npm > nul 2> nul
    if not errorlevel 1 exit /b 0
)

if exist "%LOCAL_NODE_DIR%\node.exe" if exist "%LOCAL_NODE_DIR%\npm.cmd" (
    set "PATH=%LOCAL_NODE_DIR%;%PATH%"
    exit /b 0
)

echo   Node.js/npm が見つからないため、ローカル Node.js を準備します。
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\setup-node.ps1" -ProjectRoot "%PROJECT_ROOT%"
if errorlevel 1 exit /b 1

if exist "%LOCAL_NODE_DIR%\node.exe" if exist "%LOCAL_NODE_DIR%\npm.cmd" (
    set "PATH=%LOCAL_NODE_DIR%;%PATH%"
    exit /b 0
)

echo [ERROR] ローカル Node.js の準備に失敗しました。
exit /b 1

:venv_error
echo [ERROR] 仮想環境の作成に失敗しました。
goto :fail

:fail
echo.
echo セットアップに失敗しました。上記の [ERROR] メッセージを確認してください。
echo Node.js の自動取得に失敗する場合は、Node.js 18 以上を手動でインストールしてから setup.bat を再実行してください。
echo.
pause
exit /b 1
