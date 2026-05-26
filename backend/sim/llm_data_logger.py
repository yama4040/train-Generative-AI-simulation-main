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
        
        # モードに応じてCSVのヘッダーを切り替え
        base_headers = [
            "time", "train_id", "phase", "current_notch", "speed_limit", "current_speed",
            "dist_to_next_station", "delay", "current_gradient", 
            "next_limit_info", "next_gradient_info"
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
    def extract_features(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict) -> Dict[str, Any]:
        dist_to_next_station = 0.0
        found_station = False
        current_leg = tr.current_leg()
        
        if current_leg:
            dist_to_next_station += max(0.0, current_leg.length - tr.pos_in_leg)
            for i in range(tr.leg_index + 1, len(tr.route.legs)):
                leg = tr.route.legs[i]
                if leg.stop_station_id is not None:
                    found_station = True
                    break
                dist_to_next_station += leg.length
        if not found_station:
            dist_to_next_station = 9999.0
            
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

        delay = 0.0
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

        return {
            "time": time, "train_id": tr.id, "phase": phase, "current_notch": current_notch,
            "speed_limit": current_limit, "current_speed": tr.speed,
            "dist_to_next_station": dist_to_next_station, "delay": delay,
            "current_gradient": current_gradient, "next_limit_info": next_limit_info,
            "next_gradient_info": next_gradient_info
        }

    #フェーズ名変更必要
    def _determine_phase(self, tr, time, dist_to_station, limit_dist, limit_speed) -> str:
        time_since_departure = time - getattr(tr, 'last_station_departure_time', 0.0)
        if dist_to_station <= 400.0: return "次駅への減速フェーズ（駅手前400m以内）"
        elif limit_dist <= 500.0 and limit_speed < tr.speed: return "制限速度接近に伴い減速中"
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
- 速度情報: 制限速度 {features['speed_limit']} km/h に対し、現在 {features['current_speed']:.1f} km/h で走行中
- 次駅までの距離: {features['dist_to_next_station']:.1f} m
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
        """② 直接評価モード用のプロンプト（新規）"""
        system_instruction = """あなたは列車の自動運転を評価する「運転監督エキスパート」です。
現在の走行状況と直前の「運転操作（ノッチ）」，その先の線路状況（制限速度や勾配，次駅までの残り距離）を分析し、その操作が適切であったかを -1.0（極めて危険・不適切）から 1.0（極めて優秀・適切）の範囲で総合評価（reward）してください。

#評価の観点（Tri-Drive原則）
1. 安全性 (Survival): 制限速度を超過するような操作は重大なペナルティ（マイナス評価）。
2. 快適性 (Confidence): 制限速度や勾配が安定している区間で、不必要な加減速を繰り返していないか。
3. 効率性 (Competence): 減速が必要な場面で適切にブレーキ（回生）をかけられているか、または惰行でエネルギーを節約できているか。
"""
        current_status = f"""
# 現在の走行状況と運転操作
- 走行フェーズ: {features['phase']}
- **現在の運転操作**: {features['current_notch']}  <-- 【重要】この操作が今の状況に合っているかを評価してください。
- 速度情報: 制限速度 {features['speed_limit']} km/h に対し、現在 {features['current_speed']:.1f} km/h で走行中
- 次駅までの距離: {features['dist_to_next_station']:.1f} m
- 現在の勾配: {features['current_gradient']} ‰
- 前方の制限情報: {features['next_limit_info']}
- 前方の勾配情報: {features['next_gradient_info']}
"""
        output_format = """
# 推論と出力の指示
現在の状況に対して路線全体や，駅間の走行を考えた時に「その運転操作」が安全か、快適か、効率的かを分析してください。その後、-1.0〜1.0の数値で評価を下してください。
理由は100文字程度で簡潔に説明してください。
{
  "reason": "制限速度に接近しており、かつ下り勾配であるにも関わらず「力行中」であるため、速度超過リスクが極めて高い危険な操作です。",
  "reward": -0.85
}
"""
        return system_instruction + current_status + output_format


    def process_and_save(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict) -> bool:
        if getattr(tr, 'run_status', '') == "STOPPED" or tr.speed < 1e-3:
            return True

        features = self.extract_features(tr, segments, time, nominal_times, actual_arrivals)
        
        # モードに応じたプロンプトとAPIの呼び出し
        if self.llm_mode == "weights":
            prompt = self.generate_weights_prompt(features)
            reason, w_surv, w_conf, w_comp = call_llm_for_weights(prompt)
            row = [
                round(time, 1), tr.id, features["phase"], features["current_notch"], 
                features["speed_limit"], round(features["current_speed"], 2), 
                round(features["dist_to_next_station"], 1), features["delay"], 
                features["current_gradient"], features["next_limit_info"], 
                features["next_gradient_info"], w_surv, w_conf, w_comp, reason
            ]
        elif self.llm_mode == "eval":
            prompt = self.generate_eval_prompt(features)
            reason, reward = call_llm_for_eval(prompt)
            row = [
                round(time, 1), tr.id, features["phase"], features["current_notch"], 
                features["speed_limit"], round(features["current_speed"], 2), 
                round(features["dist_to_next_station"], 1), features["delay"], 
                features["current_gradient"], features["next_limit_info"], 
                features["next_gradient_info"], reward, reason
            ]
        
        with open(self.output_csv, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(row)
            
        if reason.startswith("APIキー未設定") or reason.startswith("エラー発生"):
            return False
        return True