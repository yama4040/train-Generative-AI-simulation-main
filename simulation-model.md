# シミュレーションモデル

本書は本プロジェクトのシミュレーションモデルについて、制約・速度決定方法・物理計算の近似を説明します。

## 概要
- 列車は地点間のセグメント上を移動します。地点は停車対象の `station` と、停車しない `waypoint` に分かれます。
- シミュレーションは時間刻み `dt` の離散時間で進行します。
- 位置は「ルート距離（route distance）」と「セグメント内距離（segment distance）」で管理されます。
- 各ステップの状態は WebSocket でフロントへ送信されます。

## 経路モデル
現在の入力モデルは、駅列ではなく `route_id` ベースです。

```json
{
  "stations": [
    { "id": "S1", "name": "Station", "kind": "station", "x": 0, "y": 0 },
    { "id": "W1", "name": "Switch", "kind": "waypoint", "x": 100, "y": 0 }
  ],
  "segments": [],
  "routes": [
    {
      "id": "R_A",
      "legs": [
        { "segment_id": "E1", "from": "S1", "to": "S2" }
      ]
    }
  ],
  "exclusive_sections": [
    {
      "id": "X_SHARED",
      "segment_ids": ["E1"],
      "capacity": 1,
      "priority_route_ids": ["R_A"]
    }
  ]
}
```

- `segments` は物理線路です。
- `stations[].kind` は `station` または `waypoint` です。未指定時は後方互換のため `station` として扱います。
- `routes[].legs` は物理線路をどちら向きに通るかを定義します。
- `trains[].route_id` が走行 route を指定します。
- `exclusive_sections` は共有単線などの排他区間です。

## 排他区間制御
- 排他区間に含まれる segment の入口を gate として扱います。
- 区間内の列車数が `capacity` に達している場合、後続列車は gate 手前で停止します。
- 進入許可は、基本的に先に通行許可を取得した列車を優先します。同時要求時のみ、待ち始め時刻、gate までの距離、列車順で決定します。
- 待機中の状態は `status: "WAITING"`, `wait_reason: "exclusive_section"`, `block_id` として出力されます。
- 衝突・排他区間 capacity 超過が発生した場合、該当列車は `status: "crash"` として出力されます。

## 常に守る制約（必須）
1) **衝突回避は常に必須**
   - 同一路線・同方向の列車は絶対に重ならない。
   - 先行列車の最後尾 + 固定安全距離（現行 20m）を越えて接近しない。

2) **駅停車は必須**
   - 列車は `kind: "station"` の地点に停車する。
   - `kind: "waypoint"` の地点は線路形状・分岐・排他区間境界を表す通過点であり、減速・停車しない。
   - 停車時間は駅ごとの `stop_time` に従う。

3) **最大減速度は厳守**
   - 減速度は列車の `decel` を超えない。

4) **シミュレーション継続条件**
   - 非円環路線: すべての列車が終点到着するまで継続。
   - 円環路線: 停止ボタンが押されるまで継続。

## モード
### 低精度モード（`low_precision`）
- 固定の加速度・減速度で速度を決定。
- 「停止ターゲットまで止まれるか」を満たす速度を選択。
- 停止ターゲットは **駅**、**非円環路線の終端**、**進入不可の排他区間入口**、**先行列車安全距離** のうち近い方。

### 追従IDMモード（`follow_idm`）
- IDM（Intelligent Driver Model）で追従速度を決定。
- ただし以下の制約を追加して運転を安定化:
  - **最低速度 20km/h** を維持（※加速度制限内で段階的に到達）
  - **例外:** 先行列車の安全距離に対する停止パターンに接触した場合は停止を許容
  - **加減速の頻繁な切替禁止:** 5秒以内の加速度符号反転を抑制
- 駅停止・通過点通過・衝突回避はこのモードでも必須。

### 高精度モード（`high_precision`）
- 将来拡張予定（現時点では未使用）。

### 到着間隔制御モード（`headway_control`）
- 駅到着間隔を一定にするための制御モード。
- 先行列車の遅延が発生した場合、後続の目標到着時刻も遅延させる。
- 速度は「許容最高速度 `v_cap`」を調整して制御する（加速度・減速度の物理制約は維持）。
- 衝突回避・駅停止は常に優先。

#### 目標到着時刻の定義（先行遅延を反映）
```
T_base(i,k)   = start_time(i) + Σ_{j=0..k-1} T_nominal(j)
T_target(i,k) = max( T_base(i,k), T_actual(i-1,k) + headway_target_opt )
```
- `T_nominal(j)`: 駅間理想走行時分（停止時間は含めない）
- `T_actual(i-1,k)`: 先行列車の実到着時刻
- `headway_target_opt`: **最適化用の到着間隔目標**（以下で自動計算）

```
headway_target_opt = max(headway_target_opt_min, route_nominal_time / N)
```
- `route_nominal_time`: 路線（駅間）の理想走行時分合計（停止時間は含めない）
- `N`: 同一路線・同方向の列車数

#### v_cap方式の制御
```
Δt = (現在時刻 + 予想到着時間) - T_target

if |Δt| <= headway_epsilon:
  v_cap = v_max
elif Δt < -headway_epsilon:   # 早着
  v_cap = v_max * clamp(1 + headway_k * Δt, headway_vcap_min, 1.0)
else:             # 遅れ
  v_cap = v_max
```
- 早着のみ減速方向に調整し、遅れは可能な範囲で回復。
- `v_cap` は「最高速度の上限」として停止パターン探索に渡す。

#### 係数の意味
- `headway_target`（秒）: **start_time 自動入力の基準**（UIで設定）
- `headway_target_opt`（秒）: **最適化用の到着間隔目標**（自動計算）
- `headway_target_opt_min`（秒）: `headway_target_opt` の下限（初期値 10秒）
- `headway_epsilon`（秒）: 許容幅（±秒、UIで設定）
- `headway_k`: 早着時の補正係数（内部既定値）
- `headway_vcap_min`: v_capの下限倍率（内部既定値）

#### 実装位置（参考）
- 目標時刻・v_cap計算: `backend/sim/engine.py` 315行付近  
- 実到着時刻の記録: `backend/sim/engine.py` 368行付近  

## 速度決定ロジック（概要）
各ステップで以下を評価:
1) **停止ターゲット距離**を決定
   - 駅停止位置（セグメント終端）
   - 先行列車安全距離位置
   - 近い方を停止ターゲットとして採用

2) `follow_idm` かつ **停止ターゲットが安全距離の場合**
   - IDM により加速度を計算
   - 制約適用:
     - `accel` / `decel` 制限
     - 最低速度 20km/h を維持（ただし停止パターン接触時は例外）
     - 5秒以内の加速度符号反転を抑制
   - 安全距離違反が起きる場合は停止パターンへ切替

3) それ以外（駅停止など）は **停止パターン制御**
   - `decel` を超えない範囲で停止ターゲットに止まれる速度を探索

## IDMパラメータ
- `v0`: 目標速度 = 列車 `max_speed`
- `a`: 最大加速度 = 列車 `accel`
- `b`: 快適減速度 = 列車 `decel`
- `T`: 時間ヘッドウェイ（シミュレーションごとに設定、初期値 1.5秒）
- `s0`: 最小距離 = 固定安全距離（現行 20m）
- `delta`: 4（標準指数）

IDMの式:
```
s* = s0 + max(0, v*T + (v*Δv)/(2*sqrt(a*b)))
a_IDM = a * (1 - (v/v0)^delta - (s*/s)^2)
```

## 物理計算の近似
- 各 `dt` で「加速度は一定」とみなす。
- 移動距離は以下で近似:
  `travel = (v_prev + v_next) * 0.5 * dt`
- 速度は 0 以上、`max_speed` 以下に制限。
- 停止パターンは「離散的な制動距離計算」で安全に停止可能か判定。

## 停止パターン探索の詳細
停止パターンは「次のターゲットで必ず止まれる速度」を満たすように速度を選びます。

### 概要
- ターゲット距離 `stop_dist` が与えられたとき、次の時刻の速度 `v1` を決めます。
- `v1` で進んだ後も、**`decel` を超えずに停止可能**であることを保証します。

### 判定式（実装の考え方）
1) 1ステップでの移動距離  
   `travel = (v0 + v1) * 0.5 * dt`
2) 残り距離  
   `remaining = stop_dist - travel`
3) 残り距離で止まれるか  
   `_braking_distance(v1, decel, dt) <= remaining`

### 実装の位置（参考行）
- 制動距離関数: `backend/sim/engine.py` 27行付近  
  `def _braking_distance(...)`
- 停止パターン判定 `feasible`: 235行付近  
  `def feasible(v1: float) -> bool`
- 2分探索で最大の `v1` を求める: 242–252行付近

---

## IDMの安定化パラメータと調整手順
IDMは追従挙動が敏感なので、**安定性を確保するための補正**を入れています。

### 追加の安定化条件
1) **最低速度**  
   - 原則 20 km/h を目標として維持  
   - **即時強制はしない**（加速度制限内で段階的に到達）  
   - 例外: 停止パターン接触時のみ停止許可  

2) **加減速切替の抑制（5秒ルール）**  
   - 加速度の符号（+ / -）が変わる場合、前回切替から 5秒未満なら抑制  
   - 目的: 5秒未満での頻繁な加減速を防止  

### 実装位置（参考行）
- IDM分岐: `backend/sim/engine.py` 265行付近  
  `if simulation_mode != 'follow_idm' or stop_at_station: ... else:`
- IDM式: 282–284行付近  
  `s_star = ...` / `accel = a * (...)`
- 5秒ルール: 286–297行付近  
  `accel_sign_cooldown = 5.0` / 符号反転抑制

### 調整の目安
- `T`（時間ヘッドウェイ）を上げる → より早く減速し安定する  
- `T` を下げる → 接近ギリギリまで加速、揺れが増える  
- `accel_sign_cooldown` を上げる → 切替が減り滑らか  

## 単位
- 速度: km/h
- 加速度・減速度: km/h/s
- 距離: m
- 時間: 秒

## 入力（フロントからのpayload）
- `network`: 駅・線路情報
- `trains`: 列車設定（`route_id` で route を指定）
  - `start_time` 前の列車は線路上に未投入として扱い、衝突判定・安全距離判定・排他区間判定・出力対象から除外する。`start_time` 以降も出発位置の安全距離が確保できるまで投入を待つ。
- `dt`: 時間刻み
- `duration`: シミュレーション期間（円環では無視）
- `output_interval`: WebSocket/CSV に出力する間隔（秒）
- `simulation_mode`: `low_precision` / `follow_idm` / `headway_control`
- `idm_T`: IDMの時間ヘッドウェイ（初期 1.5）
- `headway_target`: start_time 自動入力の基準（秒）
- `headway_target_opt_min`: 最適化用到着間隔の下限（秒）
- `headway_epsilon`: 到着間隔の許容幅（±秒）
- `headway_k`: 早着時の v_cap 補正係数（内部既定値）
- `headway_vcap_min`: v_cap の下限倍率（内部既定値）

## 出力（シミュレーション結果）
出力間隔ごとに以下を送信:
- `time`, `train_id`, `route_id`, `x`, `y`, `speed`
- `status`, `wait_reason`, `block_id`, `control_reason`, `control_block_id`, `in_shared_section`
- `segment_id`, `segment_ids`, `segment_index`, `distance`, `route_distance`, `stop_remaining`
  - `segment_id`: 列車中心点が現在いる代表 segment
  - `segment_ids`: 列車の先頭から最後尾までが在線している全 segment
- `wait_reason`: 停止して待機している理由
- `control_reason`: 走行中に速度を制約している減速ターゲット（`interlocking` / `exclusive_section` / `safety` / `station`）
- 非円環路線で終点に到達した列車は `status: "FINISHED"` を1回だけ出力し、以降のログ・描画対象から除外する。

---
