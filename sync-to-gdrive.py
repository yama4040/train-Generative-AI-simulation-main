#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
<project-root> → G:\マイドライブ へのバックアップ同期スクリプト（Python版）
差分確認やログ保存が可能
"""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

SOURCE = Path(__file__).resolve().parent
DEST = Path(r"G:\マイドライブ\train\train-Generative-AI-simulation")

# 除外対象
EXCLUDE_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache", 
    "venv", "env", "train-generative-sim", ".venv", ".venvs"
}
EXCLUDE_FILES = {
    "*.pyc", ".DS_Store", "*.log", ".env", ".venv_name", "route_designs.json"
}

def print_header(title: str):
    """ヘッダーを表示"""
    print()
    print("=" * 50)
    print(f"  {title}")
    print("=" * 50)
    print()

def print_status(status: str, message: str):
    """ステータスを表示"""
    symbols = {"ok": "✓", "error": "✗", "info": "→", "warn": "⚠"}
    symbol = symbols.get(status, "•")
    print(f"  {symbol} {message}")

def count_files(path: Path) -> tuple:
    """ディレクトリ内のファイル数をカウント"""
    total = 0
    excluded = 0
    
    for root, dirs, files in os.walk(path):
        # 除外ディレクトリを削除（os.walk がそれ以下を処理しないようにする）
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for f in files:
            if not any(f.endswith(ext.replace("*", "")) for ext in EXCLUDE_FILES):
                total += 1
            else:
                excluded += 1
    
    return total, excluded

def sync_files():
    """ファイルを同期"""
    print_header("C: → G: バックアップ同期")
    
    print("[1/4] パス確認中...")
    print()
    
    if not SOURCE.exists():
        print_status("error", f"ソース (C:\\work) が見つかりません")
        return False
    
    if not DEST.exists():
        print_status("error", f"バックアップ先 (G:\\マイドライブ) が見つかりません")
        return False
    
    print_status("ok", f"ソース: {SOURCE}")
    print_status("ok", f"バックアップ先: {DEST}")
    
    print()
    print("[2/4] ファイル統計中...")
    print()
    
    src_files, src_excluded = count_files(SOURCE)
    dest_files, dest_excluded = count_files(DEST)
    
    print_status("info", f"ローカル側: {src_files} ファイル（除外: {src_excluded}）")
    print_status("info", f"G: 側: {dest_files} ファイル（除外: {dest_excluded}）")
    
    print()
    print("[3/4] ファイルを同期中...")
    print()
    
    synced = 0
    
    try:
        for root, dirs, files in os.walk(SOURCE):
            # 除外ディレクトリをスキップ
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            # 相対パスを計算
            rel_path = Path(root).relative_to(SOURCE)
            dest_subdir = DEST / rel_path
            
            # ディレクトリを作成
            dest_subdir.mkdir(parents=True, exist_ok=True)
            
            # ファイルをコピー
            for file in files:
                # 除外ファイルをチェック
                if any(file.endswith(ext.replace("*", "")) for ext in EXCLUDE_FILES):
                    continue
                
                src_file = Path(root) / file
                dest_file = dest_subdir / file
                
                try:
                    shutil.copy2(src_file, dest_file)
                    synced += 1
                except Exception as e:
                    print_status("error", f"コピー失敗: {file} - {e}")
        
        print_status("ok", f"{synced} ファイルを同期しました")
        
    except Exception as e:
        print_status("error", f"同期中にエラー: {e}")
        return False
    
    print()
    print("[4/4] 同期完了確認...")
    print()
    
    new_src_files, _ = count_files(SOURCE)
    new_dest_files, _ = count_files(DEST)
    
    print_status("ok", f"ローカル側: {new_src_files} ファイル")
    print_status("ok", f"G: 側: {new_dest_files} ファイル")
    
    if new_src_files == new_dest_files:
        print_status("ok", "ファイル数が一致しています")
    else:
        print_status("warn", f"ファイル数が異なります ({new_src_files} vs {new_dest_files})")
    
    print()
    print_header("✓ バックアップ同期完了！")
    
    print("  同期されたディレクトリ:")
    print("  - backend/")
    print("  - frontend/")
    print("  - *.bat, *.py (スクリプト類)")
    print()
    print(f"  同期日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    return True

if __name__ == "__main__":
    try:
        success = sync_files()
        input("Press Enter to exit...")
        exit(0 if success else 1)
    except Exception as e:
        print_status("error", f"予期しないエラー: {e}")
        input("Press Enter to exit...")
        exit(1)
