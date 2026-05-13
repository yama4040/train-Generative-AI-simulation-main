# サーバ再起動スクリプト使用ガイド

## 概要
このスクリプトは、バックエンドとフロントエンドのサーバを一度に再起動し、動作確認まで行います。

## ファイル
- `restart-servers.bat` - バッチファイル版（推奨）
- `restart-servers.py` - Python版（詳細ログ表示）

## 使用方法

### 方法1: バッチファイル（最も簡単）
```
restart-servers.bat
```
または、エクスプローラーから直接ダブルクリック。

### 方法2: Python版
```
python restart-servers.py
```

## 実行内容

スクリプトは以下の処理を自動的に実行します：

1. **既存プロセスの停止**
   - バックエンド (port 8000) が起動していればキル
   - フロントエンド (port 5173) が起動していればキル

2. **依存環境の確認 / 作成**
   - 仮想環境がなければ `.venvs\<仮想環境名>` に作成（名前は `.venv_name` にローカル保存）
   - バックエンド依存関係がなければ `backend/requirements.txt` からインストール
   - `frontend/node_modules` がなければ `npm install` を実行

3. **バックエンドサーバの起動**
   - FastAPI + Uvicorn で起動
   - リロード機能を有効化

4. **フロントエンドサーバの起動**
   - Vite 開発サーバで起動
   - HMR (Hot Module Replacement) 機能有効

5. **サーバの状態確認**
   - ポート 8000 でバックエンドがリッスン中か確認
   - ポート 5173 でフロントエンドがリッスン中か確認
   - 両方起動している場合のみ成功と判定

6. **ブラウザを自動起動**
   - 自動的に http://localhost:5173 をブラウザで開く

## アクセス方法

スクリプト実行後、以下のURLでアクセスできます：

- **フロントエンド**: http://localhost:5173
- **バックエンド API**: http://localhost:8000
- **API ドキュメント**: http://localhost:8000/docs

## トラブルシューティング

### バッチファイルが起動しない場合
1. PowerShellで以下を実行：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

2. もう一度バッチファイルを実行

### ポートが既に使用されている場合
```powershell
# ポートを使用しているプロセスを確認
netstat -ano | find ":8000"
netstat -ano | find ":5173"

# プロセスを強制終了 (PID を XXX に置換)
taskkill /pid XXX /f
```

### バックエンドが起動しない場合
```powershell
cd <project-root>
.\train-generative-sim\Scripts\python.exe -m uvicorn backend.main:app --reload
```

### フロントエンドが起動しない場合
```powershell
cd <project-root>\frontend
npm install
npm run dev
```

## 修正したときの手順

1. コード修正
2. `restart-servers.bat` をダブルクリック（またはコマンド実行）
3. ブラウザが自動で開く
4. 修正内容を確認

以上です！

## 注意事項

- バッチファイル実行時にコマンドプロンプトが2つ（バック用、フロント用）で起動します
- 終了するときは各コマンドプロンプトを閉じるか、以下のコマンドで強制終了：
```powershell
# バックエンド停止
taskkill /f /im python.exe /fi "COMMANDLINE eq *uvicorn*"

# フロントエンド停止
taskkill /f /im node.exe
```

- PID を直接指定して停止することもできます：
```powershell
taskkill /pid <PID> /f
```
## 初回セットアップ
`restart-servers.bat` または `start.bat` が、仮想環境と依存関係を自動で準備します。

手動で準備したい場合のみ、`<project-root>` で `setup.bat` を実行してください。
