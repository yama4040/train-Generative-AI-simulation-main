import os
import json
import csv
from typing import Dict, Any, Tuple
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 1. LLM API呼び出し関数（モード別）
# ==========================================

def call_llm_for_weights(prompt_text: str) -> Tuple[str, float, float, float]:
    """① 重み決定モード用（w_surv, w_conf, w_comp を返す）"""
    result_text = _call_openai_api(prompt_text)
    if result_text.startswith("エラー発生") or result_text.startswith("APIキー未設定"):
        return result_text, 1.0, 1.0, 1.0

    try:
        clean_text = result_text.strip().replace("```json", "").replace("```", "")
        weights = json.loads(clean_text)
        reason = weights.get("reason", "理由の出力なし")
        return reason, float(weights.get("w_surv", 1.0)), float(weights.get("w_conf", 1.0)), float(weights.get("w_comp", 1.0))
    except Exception as e:
        print(f"【LLM解析エラー】: {e}")
        return f"解析エラー: {e}", 1.0, 1.0, 1.0


def call_llm_for_eval(prompt_text: str) -> Tuple[str, float]:
    """② 直接評価モード用（-1.0〜1.0 の reward を返す）"""
    result_text = _call_openai_api(prompt_text)
    if result_text.startswith("エラー発生") or result_text.startswith("APIキー未設定"):
        return result_text, 0.0  # エラー時は無評価(0.0)とする

    try:
        clean_text = result_text.strip().replace("```json", "").replace("```", "")
        eval_data = json.loads(clean_text)
        reason = eval_data.get("reason", "理由の出力なし")
        reward = float(eval_data.get("reward", 0.0))
        # -1.0 〜 1.0 の範囲にクリップ
        reward = max(-1.0, min(1.0, reward))
        return reason, reward
    except Exception as e:
        print(f"【LLM解析エラー】: {e}")
        return f"解析エラー: {e}", 0.0

def _call_openai_api(prompt_text: str) -> str:
    """API通信の共通処理"""
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_API_URL")
    if not api_key or not base_url:
        return "APIキー未設定によるダミー"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "あなたは列車の自動運転制御を評価するエキスパートです。必ず指示されたJSONフォーマットのみを出力してください。"},
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.0,
            timeout=60.0,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"エラー発生: {e}"


# ==========================================
# 2. データロガー クラス
# ==========================================

class LLMDataCollector:
    def __init__(self, output_filename: str = "dqn_training_data.csv", llm_mode: str = "weights"):
        """llm_mode: 'weights' (①重み決定) または 'eval' (②直接評価)"""
        self.llm_mode = llm_mode
        base_dir = os.path.dirname(os.path.dirname(__file__))
        log_dir = os.path.join(base_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        self.output_csv = os.path.join(log_dir, output_filename)
        
        # CSVヘッダーに 'time_to_next_station' を追加
        base_headers = [
            "time", "train_id", "phase", "current_notch", "holding_time", 
            "prev_notch", "prev_notch_duration",  
            "speed_limit", "signal_speed", "current_speed",  # <--- signal_speed を追加
            "dist_to_next_station", "time_to_next_station", "req_stop_dist", "delay", 
            "current_gradient", "next_limit_info", "next_gradient_info",
            "forward_info", "backward_info"
        ]
        if self.llm_mode == "weights":
            self.headers = base_headers + ["w_surv", "w_conf", "w_comp", "reason"]
        elif self.llm_mode == "eval":
            self.headers = base_headers + ["reward", "reason"]
        
        if not os.path.exists(self.output_csv):
            with open(self.output_csv, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)

    # extract_features, _determine_phase, _next_limit_target, _next_gradient_target は前回と同じため省略せずに記述してください
    def extract_features(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict, forward_info: dict = None, backward_info: dict = None) -> Dict[str, Any]:
        dist_to_next_station = 0.0
        found_station = False
        current_leg = tr.current_leg()
        
        # ▼▼▼ 【追加】オーバーラン中（未停車）は、本来の停止位置から現在位置を引いてマイナス距離を算出 ▼▼▼
        if getattr(tr, 'is_overrunning', False) and getattr(tr, 'overrun_target_leg_index', None) is not None:
            target_pos = tr.route.cumulative[tr.overrun_target_leg_index + 1]
            dist_to_next_station = target_pos - tr.route_position()
            found_station = True
        # ▲▲▲ 追加ここまで ▲▲▲
        elif current_leg:
            dist_to_next_station += max(0.0, current_leg.length - tr.pos_in_leg)
            # 現在のレグ自体が駅の停止目標（進入レグ）である場合の判定を追加
            if current_leg.stop_station_id is not None:
                found_station = True
            else:
                # 次以降のレグを走査し、進入レグの長さも距離にしっかり足し込む
                for i in range(tr.leg_index + 1, len(tr.route.legs)):
                    leg = tr.route.legs[i]
                    dist_to_next_station += leg.length
                    if leg.stop_station_id is not None:
                        found_station = True
                        break
                    
        if not found_station:
            dist_to_next_station = 9999.0
        
        # ▼▼▼ 【追加】完全に停車した瞬間（just_stopped）は、エンジン側で計算した停止誤差をそのまま代入する ▼▼▼
        if getattr(tr, 'just_stopped', False):
            dist_to_next_station = getattr(tr, 'final_stop_error', 0.0)
        # ▲▲▲ 追加ここまで ▲▲▲
        
        raw_seg_id = current_leg.segment_id.split(':')[-1] if current_leg else ""
        current_seg = segments.get(raw_seg_id)
        current_limit = getattr(current_seg, 'speed_limit', 0.0) if current_seg else 0.0
        current_limit = current_limit if current_limit > 0 else tr.max_speed
        current_gradient = getattr(current_seg, 'gradient', 0.0) if current_seg else 0.0
        
        limit_dist, limit_speed = self._next_limit_target(tr, segments)
        if limit_dist <= 500 and limit_speed > 0:
            next_limit_info = f"{int(limit_dist)}m先に制限速度{limit_speed}km/hあり"
        else:
            next_limit_info = "この先制限速度なし"
            
        grad_dist, grad_val = self._next_gradient_target(tr, segments)
        if grad_dist <= 2000 and grad_val != 0:
            direction = "上り" if grad_val > 0 else "下り"
            next_gradient_info = f"{int(grad_dist)}m先に{direction}勾配{abs(grad_val)}‰あり"
        else:
            next_gradient_info = "この先目立った勾配なし"

        delay = round(getattr(tr, 'delay', 0.0), 1)
        # ▼▼▼ この1行を追加 ▼▼▼
        time_to_next = round(getattr(tr, 'time_to_next_station', 0.0), 1)
        req_stop_dist = getattr(tr, 'current_req_stop_dist', 0.0)
        
        phase = self._determine_phase(tr, time, dist_to_next_station, limit_dist, limit_speed)

        raw_status = getattr(tr, 'run_status', '')
        if raw_status in ["POWER_RUN", "ACCELE"]:
            current_notch = "力行（加速）中"
        elif raw_status == "BRAKE":
            current_notch = "ブレーキ（減速）中"
        elif raw_status in ["COAST", "COASTING"]:
            current_notch = "惰行中"
        else:
            current_notch = "停止・その他"
        
        # 追加: 保持時間の取得
        holding_time = round(getattr(tr, 'holding_time', 0.0), 1)
        
        # ▼▼▼ 追加: 直前のノッチと継続時間の取得 ▼▼▼
        raw_prev = getattr(tr, 'prev_notch', '')
        if raw_prev in ["POWER_RUN", "ACCELE"]:
            prev_notch_str = "力行（加速）"
        elif raw_prev == "BRAKE":
            prev_notch_str = "ブレーキ（減速）"
        elif raw_prev in ["COAST", "COASTING"]:
            prev_notch_str = "惰行"
        else:
            prev_notch_str = "なし（または停止）"
            
        prev_notch_duration = round(getattr(tr, 'prev_notch_duration', 0.0), 1)
        # ▲▲▲ 追加ここまで ▲▲▲
        
        # ▼▼▼ 追加 ▼▼▼
        if forward_info:
            f_str = f"前方 {forward_info['distance']:.1f}m 先を {forward_info['speed']:.1f}km/h で走行中 (列車 {forward_info['id']})"
        else:
            f_str = "先行列車なし"

        if backward_info:
            b_str = f"後方 {backward_info['distance']:.1f}m 後ろを {backward_info['speed']:.1f}km/h で走行中 (列車 {backward_info['id']})"
        else:
            b_str = "後続列車なし"
        # ▲▲▲ 追加ここまで ▲▲▲

        signal_speed = getattr(tr, 'cbtc_speed_limit', current_limit)

        return {
            "time": round(time, 1),
            "train_id": tr.id,
            "phase": phase,
            "current_notch": current_notch,
            "holding_time": holding_time,
            "prev_notch": prev_notch_str,
            "prev_notch_duration": prev_notch_duration,
            "speed_limit": current_limit,
            "signal_speed": round(signal_speed, 1), # <--- 【追加】
            "current_speed": round(tr.speed, 2),
            "dist_to_next_station": round(dist_to_next_station, 1), # CSVに合わせて小数第1位
            "req_stop_dist": round(req_stop_dist, 2), # CSVに合わせて小数第2位
            "time_to_next_station": time_to_next,
            "delay": delay,
            "current_gradient": current_gradient,
            "next_limit_info": next_limit_info,
            "next_gradient_info": next_gradient_info,
            "forward_info": f_str,
            "backward_info": b_str
        }

    #フェーズ名変更必要
    def _determine_phase(self, tr, time, dist_to_station, limit_dist, limit_speed) -> str:
       # 【修正】last_station_departure_time を基準とし、無ければ start_time を使用
        dep_time = getattr(tr, 'last_station_departure_time', getattr(tr, 'start_time', 0.0))
        time_since_departure = time - dep_time 
        
        # ▼▼▼ 【修正】距離ではなく just_stopped フラグで確実に0km/hの瞬間を拾う ▼▼▼
        if getattr(tr, 'just_stopped', False):
            return "駅停車完了（速度0km/h）"
        # ▲▲▲ 修正ここまで ▲▲▲
        # ▼▼▼ 【追加】オーバーラン中（走行中）も減速フェーズとして扱う ▼▼▼
        if getattr(tr, 'is_overrunning', False):
            return "次駅への減速フェーズ（駅手前400m以内）"
        # ▲▲▲ 追加ここまで ▲▲▲

        if dist_to_station <= 400.0: return "次駅への減速フェーズ（駅手前400m以内）"
        elif limit_dist <= 500.0 and limit_speed < tr.speed: return "制限速度区間に接近中（500m以内に制限区間在り）"
        elif getattr(tr, 'run_status', '') == "ACCELE" and time_since_departure <= 20.0: return "駅出発直後の加速フェーズ（20秒以内）"
        else: return "巡航フェーズ（駅間走行中）"

    def _next_limit_target(self, tr, segments) -> Tuple[float, float]:
        if not tr.current_leg(): return float('inf'), 0.0
        accum_dist = tr.current_leg().length - tr.pos_in_leg
        for i in range(tr.leg_index + 1, len(tr.route.legs)):
            leg = tr.route.legs[i]
            raw_seg_id = leg.segment_id.split(':')[-1]
            seg = segments.get(raw_seg_id)
            limit = getattr(seg, 'speed_limit', 0.0) if seg else 0.0
            if limit > 0 and limit < tr.speed: return accum_dist, limit
            accum_dist += leg.length
        return float('inf'), 0.0

    def _next_gradient_target(self, tr, segments) -> Tuple[float, float]:
        if not tr.current_leg(): return float('inf'), 0.0
        current_raw_id = tr.current_leg().segment_id.split(':')[-1]
        current_grad = getattr(segments.get(current_raw_id), 'gradient', 0.0) if segments.get(current_raw_id) else 0.0
        accum_dist = tr.current_leg().length - tr.pos_in_leg
        for i in range(tr.leg_index + 1, len(tr.route.legs)):
            leg = tr.route.legs[i]
            raw_seg_id = leg.segment_id.split(':')[-1]
            seg = segments.get(raw_seg_id)
            grad = getattr(seg, 'gradient', 0.0) if seg else 0.0
            if grad != current_grad and abs(grad) > 0: return accum_dist, grad
            accum_dist += leg.length
        return float('inf'), 0.0


    # ==========================================
    # 3. プロンプト生成（モード別）
    # ==========================================

    def generate_weights_prompt(self, features: Dict[str, Any]) -> str:
        """① 重み決定モード用のプロンプト（既存）"""
        system_instruction = r"""あなたは次世代鉄道自動運転AI（DQNエージェント）の挙動を最適化する「報酬設計エキスパート」です。
現在の列車の走行状況を分析し、エージェントが安全かつ効率的に学習するための「Tri-Drive報酬モデル」の3つの重み（0.0〜1.0の範囲）を決定してください。

# Tri-Drive報酬モデルの定義と計算式
$reward = w_{surv} R_{surv, t} + w_{conf} R_{conf, t} + w_{comp} R_{comp, t}$

1. Survival (安全性/恒常性: $w_{surv}$)
   - 制限速度の超過ペナルティ、計画ダイヤからの遅延などを評価。
2. Confidence (確信度/快適性: $w_{conf}$)
   - ジャーク（加速度の変化量）と、力行・ブレーキの急激な反転によるペナルティを評価。
3. Competence (能力/効率性: $w_{comp}$)
   - 回生ブレーキの活用など、高度な省エネ制御能力を評価。
"""
        current_status = f"""
# 現在の走行状況
- 走行フェーズ: {features['phase']}
- 現在の運転操作: {features['current_notch']}
- 速度情報: 制限速度 {features['speed_limit']} km/h (CBTC信号現示 {features['signal_speed']:.1f} km/h) に対し、現在 {features['current_speed']:.1f} km/h で走行中
- 次駅までの距離: {features['dist_to_next_station']:.1f} m
- 物理的に必要なブレーキ距離: {features['req_stop_dist']:.2f} m
- 運行状況: 計画ダイヤに対し {features['delay']} 秒の遅延
- 現在の勾配: {features['current_gradient']} ‰
- 前方の制限情報: {features['next_limit_info']}
- 前方の勾配情報: {features['next_gradient_info']}
"""
        output_format = """
# 推論と出力の指示
上記の状況から論理的に分析し、それぞれの重み（0.0〜1.0）を決定してください。
{
  "reason": "制限速度に接近しているためSurvivalを最優先(0.9)とする。...",
  "w_surv": 0.9,
  "w_conf": 0.2,
  "w_comp": 0.7
}
"""
        return system_instruction + current_status + output_format


    def generate_eval_prompt(self, features: Dict[str, Any]) -> str:
        """② 直接評価モード用のプロンプト（完全版）"""
        system_instruction = """
あなたは熟練の鉄道運転士として列車の自動運転を評価する「運転監督エキスパート」です。

現在の走行状況とその先の線路状況を分析し、現在の運転操作（current_notch）が適切であったかを0.0（極めて危険・不適切）〜1.0（極めて優秀・適切）の範囲で評価してください。

# 評価対象
重要：rewardは「現在のノッチ操作」が適切かどうかのみを評価すること。運転全体の出来栄えや過去のミス、将来の結果を評価してはならない。

評価対象は以下のみである。
- 現在の走行状況
- 現在の運転操作(current_notch)

# 評価優先順位（絶対）
以下の優先順位で評価すること。
1. 制限速度順守
2. CBTC信号現示順守
3. 停止位置達成可能性
4. フェーズに対するノッチ操作の適切性
5. ノコギリ運転
6. 先行列車と後続列車の運転間隔の適切性
7. 定時性
8. 省エネ性

下位項目が優秀でも、
上位項目に重大な問題がある場合は高評価を与えてはならない。

--------------------------------------------------
# フェーズ別評価基準
--------------------------------------------------
## 加速フェーズ
目的：必要速度に到達するための安定した加速

高評価
- 力行を継続している
- ノコギリ運転がない
- CBTC制限内

減点
- 不必要な惰行
- 不必要なブレーキ
- 頻繁なノッチ切替

大幅減点
- 先行列車も存在しないのに低速で惰行または減速している

--------------------------------------------------
## 巡航フェーズ
目的：定時性と省エネ性の両立

現在速度が必要速度を十分満たしている場合は惰行を高く評価する。
必要速度は「現在の速度」と「残り時間」から平均速度を算出し、その平均速度を1.3～1.4倍した速度とする。

高評価
- 惰行を活用している
- 制限速度に余裕がある
- ノコギリ運転がない
- 先行列車と後続列車の両方が存在する場合、自列車と先行列車、後続列車の車間がある程度同じである。（誤差±400m程度）

減点
- 必要以上の力行
- 惰行不足
- ノコギリ運転

--------------------------------------------------
## 次駅減速フェーズ
目的：所定停止位置への停止

定時性より停止位置精度を優先する。
評価には必ずreq_stop_distを使用すること。
LLM独自の停止距離計算は禁止。

### ブレーキ開始判定
下記の式を用いて評価を行う。
- delta_stop = dist_to_next_station - req_stop_dist

### 評価目安
ブレーキをかけている場合
- 高評価：|delta_stop| ≤ 3m
- やや減点：3m < |delta_stop| ≤ 5m
- 大幅減点：|delta_stop| > 5m

ブレーキをかけていない場合
- 高評価：delta_stop >= 0m（このタイミングでブレーキをかけると駅手前に停車する可能性があるため）
- やや減点：-5m < delta_stop <0m 
- 大幅減点：delta_stop < -5m（オーバーランリスクが高いため）

--------------------------------------------------
## 駅停車完了フェーズ
速度0km/hで停止済み状態。停止位置誤差を評価する。

|dist_to_next_station| ≤ 1m
→ reward = 1.0

|dist_to_next_station| > 10m
→ reward = 0.0

1m〜10mの範囲は誤差に応じて段階的に減点する。

--------------------------------------------------
# CBTCについて
--------------------------------------------------
- CBTC停止限界距離は先行列車の50m手前である。
- 先行列車が存在する場合はまずCBTC制御を優先して評価すること。

## 評価ルール
- current_speed > signal_speed かつ ブレーキをかけていない
  → 信号を無視しているとして大幅減点
- current_speed ≒ signal_speed かつ ブレーキ中
  → 高評価
- current_speed < signal_speed
  → 高評価

--------------------------------------------------
# 先行列車解放時の特例
--------------------------------------------------
先行列車待ちで停止した後、signal_speed > 0となった場合は、次駅減速フェーズ中であっても再加速は適切な操作である。
「減速フェーズだから加速は不適切」と判断してはならない。

--------------------------------------------------
# ノコギリ運転評価
--------------------------------------------------
以下をすべて満たした場合のみノコギリ運転として減点する。

## 条件1
holding_time < 10秒

## 条件2
prev_notch_duration < 10秒

##条件3
以下のような反転操作
- 力行→惰行
- 惰行→力行
- 力行→減速
- 減速→力行

## 評価ルール
- 5秒未満 → 大きな減点
- 5〜10秒 → 小さな減点
- 10秒以上 → 減点なし

--------------------------------------------------
# 即reward0.0ルール
--------------------------------------------------
以下に該当する場合はrewardを必ず0.0とする。

- 制限速度超過
- signal_speed超過
- オーバーラン走行中
- オーバーランほぼ確実
- 駅手前停止ほぼ確実
- 駅停車誤差±10m超

--------------------------------------------------
# 出力ルール
--------------------------------------------------
Step1 制限速度確認
Step2 CBTC確認
Step3 停止位置達成可能性確認
Step4 フェーズ評価
Step5 ノコギリ運転評価
Step6 先行列車と後続列車の運転間隔の適切性
Step7 定時性・省エネ性評価
Step8 総合評価

以上の順序で評価すること。
"""

        current_status = f"""
# 現在の走行状況と運転操作
- 走行フェーズ: {features['phase']}
- **現在の運転操作**: {features['current_notch']} （継続時間: {features['holding_time']} 秒） <-- 【重要】この操作の適切性を評価してください。
- **直前の運転操作**: {features['prev_notch']} （継続時間: {features['prev_notch_duration']} 秒） <-- 【重要】保持時間が共に5秒未満の場合、ノコギリ運転のルールを確認してください。
- 速度情報: 制限速度 {features['speed_limit']} km/h (CBTC信号現示 {features['signal_speed']:.1f} km/h) に対し、現在 {features['current_speed']:.1f} km/h で走行中
- 次駅への情報: 次駅までの残り距離 {features['dist_to_next_station']:.1f} m（マイナスは過走）に対し，定時到着まで残り {features['time_to_next_station']} 秒
- 駅停車に必要なブレーキ距離: {features['req_stop_dist']:.2f} m
- 運行状況: 計画ダイヤに対し {features['delay']} 秒の遅延
- 現在の勾配: {features['current_gradient']} ‰
- 前方の制限情報: {features['next_limit_info']}
- 前方の勾配情報: {features['next_gradient_info']}
- 先行列車の状況: {features['forward_info']}
- 後続列車の状況: {features['backward_info']}
"""
        output_format = """
# 出力指示
reasonでは、Step1〜Step8の分析を踏まえ、現在の運転操作（current_notch）が適切であったかを簡潔に説明してください。
説明は150〜200文字程度とし、rewardは0.0～1.0（0.1刻み）で出力すること。

{
  "reason": "速度は制限内ですが、直前の加速から2.0秒で惰行に切り替えるノコギリ運転が発生しています。また定時到着に余裕があるにもかかわらず加速を続けていたため省エネ性に欠けます。安全性は保たれていますが快適性と効率性の観点から不適切です。",
  "reward": 0.5
}
"""
        return system_instruction + current_status + output_format
        return system_instruction + current_status + output_format


    def process_and_save(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict, forward_info: dict = None, backward_info: dict = None) -> bool:
        # ▼▼▼ 修正: 完全に停車した瞬間（just_stopped）だけは評価をスキップせずに通す ▼▼▼
        is_just_stopped = getattr(tr, 'just_stopped', False)
        if not is_just_stopped and (getattr(tr, 'run_status', '') == "STOPPED" or tr.speed < 1e-3):
            return True
        # ▲▲▲ 修正ここまで ▲▲▲

        features = self.extract_features(tr, segments, time, nominal_times, actual_arrivals, forward_info, backward_info)
        
        # ▼▼▼ 追加: 実際のフェーズ文字列によるフィルタリング ▼▼▼
        if getattr(tr, 'eval_mode', 'normal') == "brake_eval":
            target_phases = [
                "次駅への減速フェーズ（駅手前400m以内）",
                "駅停車完了（速度0km/h）"
            ]
            if features["phase"] not in target_phases:
                return True  # 該当フェーズ以外はAPIを叩かず、正常終了扱いとしてスキップする
        # ▲▲▲ 追加ここまで ▲▲▲
        
        # モードに応じたプロンプトとAPIの呼び出し
        if self.llm_mode == "weights":
            prompt = self.generate_weights_prompt(features)
            reason, w_surv, w_conf, w_comp = call_llm_for_weights(prompt)
            row = [
                round(time, 1), tr.id, features["phase"], features["current_notch"], 
                features["speed_limit"], round(features["current_speed"], 2), 
                round(features["dist_to_next_station"], 1), 
                features["time_to_next_station"],  # <--- 【修正】ここを追加！
                features["delay"], 
                features["current_gradient"], features["next_limit_info"], 
                features["next_gradient_info"], w_surv, w_conf, w_comp, reason
            ]
        elif self.llm_mode == "eval":
            prompt = self.generate_eval_prompt(features)
            reason, reward = call_llm_for_eval(prompt)
            row = [
                features["time"], tr.id, features["phase"], features["current_notch"], 
                features["holding_time"], 
                features["prev_notch"], features["prev_notch_duration"],
                features["speed_limit"], features["signal_speed"], features["current_speed"], 
                features["dist_to_next_station"], 
                features["time_to_next_station"],
                features["req_stop_dist"],  
                features["delay"], 
                features["current_gradient"], features["next_limit_info"], 
                features["next_gradient_info"], 
                features["forward_info"], features["backward_info"],
                reward, reason
            ]
            
        with open(self.output_csv, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(row)
            
        if reason.startswith("APIキー未設定") or reason.startswith("エラー発生"):
            return False
        return True