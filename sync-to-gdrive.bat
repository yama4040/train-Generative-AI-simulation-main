@echo off
REM <project-root> → G:\マイドライブ へのバックアップ同期スクリプト
REM 使用方法: このファイルをダブルクリック

chcp 65001 > nul
setlocal enabledelayedexpansion

echo.
echo ============================================
echo   G: へのバックアップ同期開始
echo ============================================
echo.

set SOURCE=%~dp0
if "%SOURCE:~-1%"=="\" set SOURCE=%SOURCE:~0,-1%
set DEST=G:\マイドライブ\train\train-Generative-AI-simulation

echo [1/3] バージョン確認中...
echo.

if not exist "%SOURCE%" (
    echo   ✗ ソース (<project-root>) が見つかりません
    pause
    exit /b 1
)

if not exist "%DEST%" (
    echo   ✗ バックアップ先 (G:\マイドライブ) が見つかりません
    pause
    exit /b 1
)

echo   ✓ ソース: %SOURCE%
echo   ✓ バックアップ先: %DEST%
echo.

echo [2/3] ファイルを同期中...
echo.

REM node_modules と .git は除外
robocopy "%SOURCE%" "%DEST%" ^
    /MIR ^
    /XD node_modules .git __pycache__ .pytest_cache venv env .venv .venvs train-generative-sim ^
    /XF "*.pyc" ".DS_Store" "*.log" ".env" ".venv_name" "route_designs.json" ^
    /NFL /NDL

if errorlevel 8 (
    echo   ✗ 同期中にエラーが発生しました
    pause
    exit /b 1
)

echo.
echo [3/3] 同期完了確認...
echo.

echo   ✓ バックアップを G: に同期しました
echo.
echo   同期されたファイル:
echo   - backend/
echo   - frontend/
echo   - *.bat, *.py (スクリプト類)
echo.

echo ============================================
echo   ✓ バックアップ同期完了！
echo ============================================
echo.

pause
exit /b 0
