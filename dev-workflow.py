#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
開発ワークフロー統合スクリプト
1. サーバを再起動してテスト
2. テスト成功時にG:\マイドライブ へ自動同期
"""

import subprocess
import sys
from pathlib import Path

def print_header(title: str):
    print()
    print("=" * 50)
    print(f"  {title}")
    print("=" * 50)
    print()

def print_status(status: str, message: str):
    symbols = {"ok": "✓", "error": "✗", "info": "→", "warn": "⚠"}
    symbol = symbols.get(status, "•")
    print(f"  {symbol} {message}")

def run_script(script_path: str, description: str) -> bool:
    """スクリプトを実行"""
    print_header(description)
    
    try:
        result = subprocess.run(
            ["python", script_path],
            cwd=str(Path(__file__).resolve().parent)
        )
        return result.returncode == 0
    except Exception as e:
        print_status("error", f"実行エラー: {e}")
        return False

def main():
    print_header("開発ワークフロー")
    
    # Step 1: サーバ再起動
    print("[1/2] サーバを再起動してテスト...")
    print()
    
    if not run_script("restart-servers.py", "サーバ再起動"):
        print_status("error", "サーバ再起動に失敗しました")
        return False
    
    print()
    print("[2/2] テスト成功時にG:\へ同期...")
    print()
    
    response = input("  テストは成功しましたか？ (y/n): ").strip().lower()
    
    if response != "y":
        print_status("info", "同期をキャンセルしました")
        return True
    
    print()
    
    if not run_script("sync-to-gdrive.py", "G: へバックアップ同期"):
        print_status("error", "バックアップ同期に失敗しました")
        return False
    
    print()
    print_header("✓ ワークフロー完了！")
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print()
        print_status("info", "キャンセルされました")
        sys.exit(0)
