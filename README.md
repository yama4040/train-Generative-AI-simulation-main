# Train Generative AI Simulation

列車運行シミュレータの PoC（FastAPI バックエンド + React フロントエンド）

## 概要

このプロジェクトは、複数の列車が駅を経由して移動するシミュレーションシステムです。バックエンドは Python + FastAPI で離散時間シミュレーションエンジンを提供し、フロントエンドは React + Vite + Konva でネットワークと列車を可視化します。

## 必要なツール

クローン後にローカル環境で実行するために以下が必要です：

| ツール | バージョン | 用途 |
|--------|-----------|------|
| Python | 3.10以上 | バックエンド（シミュレーション、API） |
| Node.js | 18以上 | フロントエンド（React、Vite） |
| npm | 8以上 | Node パッケージマネージャ |

Windows では `setup.bat` が Node.js/npm の有無を確認します。Node.js/npm が PATH に無い場合は、管理者権限なしでプロジェクト内の `.tools\node` にローカル Node.js を取得して使用します。

### インストール確認コマンド

```bash
python --version
node -v
npm -v
```

## 環境構築手順

### 1. リポジトリのクローン

```bash
git clone <repository-url>
cd train-Generative-AI-simulation
```

### 2. Windows の自動セットアップ

```bash
setup.bat
```

このスクリプトは以下を実行します。

- Python 3.10 以上の確認
- Python 仮想環境の作成（`.venvs/<入力した名前>` に作成し、名前は `.venv_name` にローカル保存）
- バックエンド依存関係のインストール
- Node.js/npm の確認（未インストール時は `.tools\node` にローカル Node.js を取得）
- フロントエンド依存関係のインストール

セットアップ完了後は `restart-servers.bat` を実行すると、バックエンドとフロントエンドを起動できます。

### 3. 手動セットアップ

以下は手動で環境を作る場合の手順です。

#### バックエンド環境構築

```bash
# Python 仮想環境を作成
python -m venv venv

# 仮想環境を有効化
# Windows の場合:
venv\Scripts\activate
# Mac/Linux の場合:
source venv/bin/activate

# 依存パッケージをインストール
pip install -r backend/requirements.txt
```

**backend/requirements.txt に含まれるパッケージ：**
- `fastapi`: Web フレームワーク
- `uvicorn`: ASGI サーバ
- `python-dotenv`: 環境変数管理

テスト実行時は以下も必要：
```bash
pip install pytest
```

#### フロントエンド環境構築

```bash
cd frontend

# npm パッケージをインストール
npm install
```

**frontend/package.json に含まれる主要パッケージ：**
- `react`: UI フレームワーク
- `react-konva`: Konva.js ラッパー（キャンバス描画）
- `axios`: HTTP クライアント
- `vitest`, `@testing-library/react`: テストツール（オプション）

## 実行手順

### パターン A: 手動で各サーバを起動（推奨）

#### ターミナル 1 - バックエンド起動

```bash
# 仮想環境を有効化（未実行の場合）
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# バックエンドサーバを起動（ポート 8000）
uvicorn backend.main:app --reload --port 8000
```

ログに以下が表示されれば成功：
```
Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

#### ターミナル 2 - フロントエンド起動

```bash
cd frontend

# フロントエンド開発サーバを起動（ポート 5173）
npm run dev
```

ログに以下が表示されれば成功：
```
VITE v5.0.0  ready in 500 ms

➜  Local:   http://localhost:5173/
```

#### ターミナル 3 - ブラウザで確認

```
http://localhost:5173
```

ブラウザを開くと、ネットワークと列車の可視化画面が表示されます。

### パターン B: バッチ / スクリプトで両サーバを自動起動（Windows）

Windows ユーザの場合、以下のバッチファイルで両サーバを同時に起動できます：

```bash
start.bat
```

このスクリプトは以下を実行します：
1. 既存のバックエンド / フロントエンドプロセスを停止
2. 仮想環境や `node_modules` がなければ作成 / インストール
3. バックエンドサーバ（ポート 8000）を起動
4. フロントエンド開発サーバ（ポート 5173）を起動
5. `http://localhost:5173` をデフォルトブラウザで開く

## API とサーバポート

| エンドポイント | ポート | 説明 |
|--------------|--------|------|
| `GET /api/network` | 8000 | 駅と線路のネットワーク情報を取得 |
| `POST /api/simulate` | 8000 | CSV シミュレーション実行（ストリーミング） |
| `WebSocket /ws/sim` | 8000 | リアルタイムシミュレーション結果をストリーミング |
| Frontend（React Vite） | 5173 | UI（ネットワーク表示、列車エディタ） |

## テスト実行

### バックエンド テスト（pytest）

```bash
# 仮想環境を有効化
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# テストを実行
pytest backend/tests -v
```

**テスト項目：**
- `test_engine.py`: シミュレーションエンジンの動作確認
- `test_multitrain.py`: 複数列車シミュレーションの確認

### フロントエンド テスト（Vitest）

```bash
cd frontend

# テストを実行
npm test
```

**テスト項目：**
- `TrainEditor.test.jsx`: プリセット保存/読み込みの確認

## プロジェクト構成

```
train-Generative-AI-simulation/
├── README.md                    # このファイル
├── start.bat                    # Windows ワンクリック起動（オプション）
├── backend/
│   ├── main.py                  # FastAPI アプリケーション
│   ├── requirements.txt          # Python 依存パッケージ
│   ├── sim/
│   │   ├── engine.py            # シミュレーションエンジン
│   │   └── models.py            # データモデル（駅、線路、列車）
│   ├── data/
│   │   ├── sample_network.json  # サンプルネットワーク定義
│   │   └── sample_trains.csv    # サンプル列車データ
│   └── tests/
│       ├── test_engine.py       # エンジンテスト
│       └── test_multitrain.py   # 複数列車テスト
├── frontend/
│   ├── package.json             # npm 依存パッケージ
│   ├── src/
│   │   ├── App.jsx              # メインアプリケーション
│   │   ├── Canvas.jsx           # Konva キャンバス描画
│   │   ├── TrainEditor.jsx      # 列車エディタ（プリセット機能付き）
│   │   ├── main.jsx             # エントリポイント
│   │   └── __tests__/
│   │       └── TrainEditor.test.jsx  # UI テスト
│   └── vitest.config.js         # Vitest 設定
└── venv/                        # Python 仮想環境（初回作成後）
```

## 主な機能

- **ネットワーク表示**: 駅、線路、共有単線区間をキャンバスに描画
- **route ベースの経路設計**: 列車は駅列ではなく `route_id` で走行経路を選択
- **排他区間制御**: 共有区間では gate 手前停止、待機理由出力、優先順によるデッドロック解消を実施
- **シミュレーション実行**: 列車の加速・減速・移動をシミュレート
- **リアルタイム配信**: WebSocket でシミュレーション結果をストリーミング
- **列車エディタ**: 列車パラメータ（速度、加速度）を編集
- **プリセット機能**: 列車構成を localStorage に保存・読み込み
- **CSV エクスポート**: シミュレーション結果を CSV ファイルとして出力

## トラブルシューティング

### Q1: `ModuleNotFoundError: No module named 'backend'`

**原因**: バックエンドの仮想環境が有効になっていない

**解決策**:
```bash
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### Q2: フロントエンドが `http://localhost:5173` に表示されない

**原因**: 依存パッケージが未インストール

**解決策**:
```bash
cd frontend
npm install
npm run dev
```

### Q3: WebSocket 接続に失敗する

**原因**: バックエンドサーバが起動していない

**解決策**: ターミナルでバックエンド起動コマンドを実行
```bash
uvicorn backend.main:app --reload --port 8000
```

### Q4: `setup.bat` 実行時に `npm` が見つからない

**原因**: Node.js/npm がインストールされていない、または PATH に登録されていない。

**確認された事象（2026-05-08）**: この環境では Python 3.11.9 は使用できましたが、`node` と `npm` が見つかりませんでした。旧 `setup.bat` は `npm install` の失敗を検知せずに「セットアップ完了」と表示していました。

**対応策**: 更新後の `setup.bat` は Node.js/npm が無い場合、プロジェクト内の `.tools\node` にローカル Node.js を取得してから `npm install` を実行します。自動取得に失敗する場合は、Node.js 18 以上を手動でインストールしてから `setup.bat` を再実行してください。

## 開発メモ

- バックエンド: Python 3.10、FastAPI、uvicorn で実行
- フロントエンド: React 18、Vite 5、Konva 8 で実行
- テスト: pytest（バックエンド）、Vitest（フロントエンド）
- CI: GitHub Actions（今後追加予定）

## ライセンスと貢献

詳細はプロジェクト管理者に確認してください。

## 参考リンク

- [FastAPI ドキュメント](https://fastapi.tiangolo.com/)
- [React ドキュメント](https://reactjs.org/)
- [Vite ドキュメント](https://vitejs.dev/)
- [Konva.js ドキュメント](https://konvajs.org/)
