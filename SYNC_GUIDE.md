# C: ↔ G: 同期ガイド

## 概要

- **<project-root>** - 開発・テスト用（ローカル）
- **G:\マイドライブ\train\train-Generative-AI-simulation** - バージョン管理用（バックアップ）

修正 → テスト → G: に自動同期

## ファイル説明

### 1. `sync-to-gdrive.bat` （推奨：最も簡単）
```
sync-to-gdrive.bat
```
バッチファイル版。ダブルクリックで即座に同期開始。

**除外対象:**
- `node_modules/`（npm パッケージ）
- `.git/`（Git ファイル）
- `__pycache__/`（Python キャッシュ）
- `train-generative-sim/`（Python 仮想環境）
- `.venvs/`（個人別の Python 仮想環境）
- `.venv_name`（個人別の仮想環境名）

### 2. `sync-to-gdrive.py` （詳細ログ版）
```
python sync-to-gdrive.py
```
Python版。同期の詳細情報を表示。

**表示内容:**
- ファイル数の統計
- 同期されたファイル数
- エラーログ

### 3. `dev-workflow.py` （統合版：全自動）
```
python dev-workflow.py
```
一度のコマンドで以下を自動実行：
1. サーバを再起動してテスト
2. テスト成功後、ユーザーに確認
3. 確認後、自動で G: に同期

**使用フロー:**
```
$ python dev-workflow.py

[テスト実行中...]
[テストが完了すると聞かれる]
"テストは成功しましたか？ (y/n): " → y と入力
[自動で G: に同期]
```

## 開発ワークフロー

### パターン1: 簡単同期（推奨）

```
1. <project-root> で修正
2. 実行テスト
3. sync-to-gdrive.bat をダブルクリック
4. 終了
```

### パターン2: 統合ワークフロー

```
1. python dev-workflow.py を実行
2. サーバが自動起動
3. テストが成功したら "y" を入力
4. 自動で G: に同期
5. 終了
```

### パターン3: 手動確認版

```
python sync-to-gdrive.py
```

## 同期の詳細

### コピーされるもの
- `backend/` - バックエンドコード
- `frontend/src/` - フロントエンドコード
- `*.bat`, `*.py` - スクリプト類
- `*.md` - ドキュメント

### コピーされないもの
- `node_modules/` - npm の依存パッケージ
- `.git/` - Git の内部ファイル
- `__pycache__/` - Python キャッシュ
- `train-generative-sim/` - Python 仮想環境
- `.venvs/` - 個人別の Python 仮想環境
- `.venv_name` - 個人別の仮想環境名
- `*.log`, `*.pyc` - ログファイル

## トラブルシューティング

### G: ドライブが見つからない場合

```powershell
# Google Drive をマウント確認
Get-PSDrive | grep マイドライブ
```

### ファイルが同期されない場合

```powershell
# 手動でコピー
Copy-Item -Path "<project-root>\*" `
          -Destination "G:\マイドライブ\train\train-Generative-AI-simulation" `
          -Recurse -Force -Exclude node_modules,.git,__pycache__
```

### 権限エラーが出る場合

```powershell
# 管理者権限で実行
powershell -RunAs Administrator
python sync-to-gdrive.py
```

## 自動同期設定（上級）

Windows タスク スケジューラで定期的に同期することも可能：

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "sync-to-gdrive.py" -WorkingDirectory "<project-root>"
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -Action $action -Trigger $trigger -TaskName "Train-Sync-GDrive" -Description "G: へ同期"
```

## まとめ

| 用途 | コマンド |
|------|--------|
| 簡単同期 | `sync-to-gdrive.bat` をダブルクリック |
| テスト＋同期 | `python dev-workflow.py` |
| 詳細確認 | `python sync-to-gdrive.py` |

**推奨:** テスト完了後、毎回 `sync-to-gdrive.bat` を実行して G: を最新に保つ！
