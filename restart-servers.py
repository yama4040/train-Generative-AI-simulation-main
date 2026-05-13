#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Train Simulation - サーバ再起動スクリプト
バックエンドとフロントエンドサーバを再起動し、動作確認を行う
"""

import os
import sys
import subprocess
import time
import socket
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_NAME_FILE = PROJECT_ROOT / ".venv_name"
VENV_ROOT = PROJECT_ROOT / ".venvs"
DEFAULT_VENV_NAME = "train-generative-sim"
if VENV_NAME_FILE.exists():
    venv_name = VENV_NAME_FILE.read_text(encoding="utf-8").strip() or DEFAULT_VENV_NAME
else:
    venv_name = DEFAULT_VENV_NAME
legacy_venv_path = PROJECT_ROOT / venv_name
VENV_PATH = legacy_venv_path if (legacy_venv_path / "Scripts" / "python.exe").exists() else VENV_ROOT / venv_name
BACKEND_PORT = 8000
FRONTEND_PORT = 5173

def check_port_in_use(port: int) -> int:
    """ポート番号を使用しているプロセスIDを取得"""
    try:
        result = subprocess.run(
            f'netstat -ano | find ":{port}"',
            shell=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            # 最後の数字がPID
            pid = result.stdout.strip().split()[-1]
            return int(pid)
    except:
        pass
    return None

def kill_process(pid: int):
    """プロセスを強制終了"""
    try:
        subprocess.run(f"taskkill /pid {pid} /f", shell=True, capture_output=True)
        return True
    except:
        return False

def port_is_listening(port: int) -> bool:
    """ポートがリッスン中かチェック（IPv4/IPv6 の両方を確認）"""
    hosts = ['127.0.0.1', '::1']
    for h in hosts:
        try:
            family = socket.AF_INET6 if ':' in h else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as s:
                if family == socket.AF_INET6:
                    addr = (h, port, 0, 0)
                else:
                    addr = (h, port)
                result = s.connect_ex(addr)
                if result == 0:
                    return True
        except Exception:
            continue
    return False

def print_header(title: str):
    """ヘッダーを表示"""
    print()
    print("=" * 50)
    print(f"  {title}")
    print("=" * 50)
    print()

def print_status(status: str, message: str):
    """ステータスを表示"""
    symbol = "✓" if status == "ok" else "✗" if status == "error" else "→"
    print(f"  {symbol} {message}")

def main():
    print_header("Train Simulation - サーバ再起動")
    
    # Step 1: 既存プロセスを停止
    print("[1/5] 既存プロセスを停止中...")
    print()
    
    # バックエンドプロセス
    backend_pid = check_port_in_use(BACKEND_PORT)
    if backend_pid:
        if kill_process(backend_pid):
            print_status("ok", f"バックエンド (PID: {backend_pid}) を停止しました")
        else:
            print_status("error", f"バックエンド (PID: {backend_pid}) の停止に失敗")
    else:
        print_status("info", "バックエンドは起動していません")
    
    # フロントエンドプロセス
    frontend_pid = check_port_in_use(FRONTEND_PORT)
    if frontend_pid:
        if kill_process(frontend_pid):
            print_status("ok", f"フロントエンド (PID: {frontend_pid}) を停止しました")
        else:
            print_status("error", f"フロントエンド (PID: {frontend_pid}) の停止に失敗")
    else:
        print_status("info", "フロントエンドは起動していません")
    
    time.sleep(2)
    
    # Step 2: バックエンドを起動
    print()
    print("[2/5] バックエンドサーバを起動中...")
    print()
    
    python_exe = VENV_PATH / "Scripts" / "python.exe"
    if not python_exe.exists():
        print_status("error", f"仮想環境が見つかりません: {VENV_PATH}")
        print_status("info", "setup.bat を実行して仮想環境を作成してください。")
        return False
    backend_cmd = [
        str(python_exe),
        "-m", "uvicorn",
        "main:app",
        "--reload",
        "--host", "127.0.0.1",
        "--port", str(BACKEND_PORT)
    ]
    
    try:
        subprocess.Popen(
            backend_cmd,
            cwd=str(PROJECT_ROOT / "backend"),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        print_status("info", "バックエンド起動コマンドを実行しました")
    except Exception as e:
        print_status("error", f"バックエンド起動に失敗: {e}")
        return False
    
    time.sleep(3)
    
    # Step 3: フロントエンドを起動
    print()
    print("[3/5] フロントエンドサーバを起動中...")
    print()
    
    frontend_dir = PROJECT_ROOT / "frontend"
    frontend_cmd = "npm run dev"
    
    try:
        subprocess.Popen(
            frontend_cmd,
            shell=True,
            cwd=str(frontend_dir),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        print_status("info", "フロントエンド起動コマンドを実行しました")
    except Exception as e:
        print_status("error", f"フロントエンド起動に失敗: {e}")
        return False
    
    time.sleep(5)
    
    # Step 4: サーバの状態確認
    print()
    print("[4/5] サーバの状態確認...")
    print()
    
    backend_running = port_is_listening(BACKEND_PORT)
    frontend_running = port_is_listening(FRONTEND_PORT)
    
    if backend_running:
        print_status("ok", f"バックエンド (http://localhost:{BACKEND_PORT}) 実行中")
    else:
        print_status("error", f"バックエンド (localhost:{BACKEND_PORT}) が起動していません")
    
    if frontend_running:
        print_status("ok", f"フロントエンド (http://localhost:{FRONTEND_PORT}) 実行中")
    else:
        print_status("error", f"フロントエンド (localhost:{FRONTEND_PORT}) が起動していません")
    
    if not (backend_running and frontend_running):
        print()
        print_status("error", "サーバの起動に失敗しました")
        return False
    
    # Step 5: ブラウザを開く
    print()
    print("[5/5] ブラウザを開いています...")
    print()
    
    try:
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")
        print_status("ok", "ブラウザを開きました")
    except Exception as e:
        print_status("info", f"ブラウザを開くのに失敗しました: {e}")
    
    print()
    print_header("✓ サーバが正常に起動しました！")
    print(f"  バックエンド:   http://localhost:{BACKEND_PORT}")
    print(f"  フロントエンド:  http://localhost:{FRONTEND_PORT}")
    print()
    print("  ウェブブラウザで http://localhost:{} を開いて確認してください".format(FRONTEND_PORT))
    print()
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print()
        print_status("info", "スクリプトが中断されました")
        sys.exit(0)
    except Exception as e:
        print()
        print_status("error", f"予期しないエラーが発生しました: {e}")
        sys.exit(1)
