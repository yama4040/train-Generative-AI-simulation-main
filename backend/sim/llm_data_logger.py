import os
import json
import csv
from typing import Dict, Any, Tuple
from openai import OpenAI

# ▼▼▼ ここから2行追加 ▼▼▼

from dotenv import load_dotenv
load_dotenv() 
# ▲▲▲ ここまで ▲▲▲

def call_llm_for_weights(prompt_text: str) -> Tuple[str, float, float, float]:
    # ターミナル（環境変数）からURLとAPIキーを取得
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_API_URL")

    # 設定されていない場合はダミーの値を返してシミュレーションを継続
    if not api_key or not base_url:
        print("【警告】LLM_API_KEY または LLM_API_URL が設定されていません。ダミーの重みを使用します。")
        return "APIキー未設定によるダミー", 1.0, 1.0, 1.0

    try:
        # OpenAI互換クライアントの初期化
        client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system", 
                    "content": "あなたは列車の自動運転制御を評価するエキスパートです。必ず指示されたJSONフォーマットのみを出力してください。JSON以外のテキスト（マークダウンの装飾や挨拶など）は絶対に含めないでください。"
                },
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.0, # 決定論的な出力を得るために0に設定
        )

        result_text = response.choices[0].message.content
        
        # LLMがMarkdown記法（```json ... ```）を含めて返してきた場合の対策
        clean_text = result_text.strip().replace("```json", "").replace("```", "")
        weights = json.loads(clean_text)

        # 理由と重みを抽出
        reason = weights.get("reason", "理由の出力なし")
        w_surv = float(weights.get("w_surv", 1.0))
        w_conf = float(weights.get("w_conf", 1.0))
        w_comp = float(weights.get("w_comp", 1.0))

        return reason, w_surv, w_conf, w_comp

    except Exception as e:
        print(f"【LLMエラー】重みの取得に失敗しました: {e}")
        # APIエラー時もシミュレータが止まらないようフォールバック
        return f"エラー発生: {e}", 1.0, 1.0, 1.0


class LLMDataCollector:
    def __init__(self, output_filename: str = "dqn_training_data.csv"):
        # backend/logs フォルダ内に保存するようにパスを変更
        base_dir = os.path.dirname(os.path.dirname(__file__)) # backendフォルダ
        log_dir = os.path.join(base_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        self.output_csv = os.path.join(log_dir, output_filename)
        
        # CSVのヘッダーに 'reason' を追加
        self.headers = [
            "time", "train_id", "phase", "speed_limit", "current_speed",
            "dist_to_next_station", "delay", "current_gradient", 
            "next_limit_info", "next_gradient_info",
            "w_surv", "w_conf", "w_comp", "reason"
        ]
        
        if not os.path.exists(self.output_csv):
            with open(self.output_csv, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)

    def extract_features(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict) -> Dict[str, Any]:
        # 1. 次の駅までの距離を計算
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
            
        # 2. 現在の物理セグメント情報
        raw_seg_id = current_leg.segment_id.split(':')[-1] if current_leg else ""
        current_seg = segments.get(raw_seg_id)
        current_limit = getattr(current_seg, 'speed_limit', 0.0) if current_seg else 0.0
        current_limit = current_limit if current_limit > 0 else tr.max_speed
        current_gradient = getattr(current_seg, 'gradient', 0.0) if current_seg else 0.0
        
        # 3. 先の制限速度情報
        limit_dist, limit_speed = self._next_limit_target(tr, segments)
        if limit_dist <= 500 and limit_speed > 0:
            next_limit_info = f"{int(limit_dist)}m先に制限速度{limit_speed}km/hあり"
        else:
            next_limit_info = "この先制限速度なし"
            
        # 4. 先の勾配情報
        grad_dist, grad_val = self._next_gradient_target(tr, segments)
        if grad_dist <= 2000 and grad_val != 0:
            direction = "上り" if grad_val > 0 else "下り"
            next_gradient_info = f"{int(grad_dist)}m先に{direction}勾配{abs(grad_val)}‰あり"
        else:
            next_gradient_info = "この先目立った勾配なし"

        # 5. 遅延の計算
        delay = 0.0
        
        # 6. フェーズの判定
        phase = self._determine_phase(tr, time, dist_to_next_station, limit_dist, limit_speed)

        return {
            "time": time,
            "train_id": tr.id,
            "phase": phase,
            "speed_limit": current_limit,
            "current_speed": tr.speed,
            "dist_to_next_station": dist_to_next_station,
            "delay": delay,
            "current_gradient": current_gradient,
            "next_limit_info": next_limit_info,
            "next_gradient_info": next_gradient_info
        }

    def _determine_phase(self, tr, time: float, dist_to_station: float, limit_dist: float, limit_speed: float) -> str:
        time_since_departure = time - getattr(tr, 'last_station_departure_time', 0.0)
        
        if dist_to_station <= 400.0:
            return "次駅への減速フェーズ（駅手前400m）"
        elif limit_dist <= 500.0 and limit_speed < tr.speed:
            return "制限速度接近に伴い減速中"
        elif getattr(tr, 'run_status', '') == "ACCELE" and time_since_departure <= 20.0:
            return "駅出発直後の加速フェーズ（20秒）"
        else:
            return "巡航フェーズ（駅間走行中）"

    def _next_limit_target(self, tr, segments) -> Tuple[float, float]:
        if not tr.current_leg(): return float('inf'), 0.0
        accum_dist = tr.current_leg().length - tr.pos_in_leg
        for i in range(tr.leg_index + 1, len(tr.route.legs)):
            leg = tr.route.legs[i]
            raw_seg_id = leg.segment_id.split(':')[-1]
            seg = segments.get(raw_seg_id)
            limit = getattr(seg, 'speed_limit', 0.0) if seg else 0.0
            if limit > 0 and limit < tr.speed:
                return accum_dist, limit
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
            if grad != current_grad and abs(grad) > 0:
                return accum_dist, grad
            accum_dist += leg.length
        return float('inf'), 0.0

    def generate_prompt(self, features: Dict[str, Any]) -> str:
        return f"""あなたは次世代の鉄道自動運転AI（DQNエージェント）の挙動を最適化する「評価エキスパート」です。
現在の列車の走行状況を分析し、エージェントが安全かつ効率的に学習するための「Tri-Drive報酬モデル」の3つの重みを決定してください。

【Tri-Drive報酬モデルの定義】
以下の3つの指標について、現在の状況下でどれを重視すべきか（0.0〜1.0の範囲）を評価してください。
1. Survival (安全性/恒常性)
   - 制限速度の厳格な遵守や、目的地点への確実で安全な到着を評価します。
   - 重視すべき状況：制限速度に接近している時、速度超過の危険がある時、大幅な遅延を回復する必要がある時。
2. Confidence (確信度/快適性)
   - 制御の不確実性の抑制を評価します。急激な出力変動（ジャーク）や、無駄な力行・ブレーキの反転を抑えます。
   - 重視すべき状況：巡航フェーズなど、速度が安定しており乗り心地を最優先すべき時。
3. Competence (能力/効率性)
   - 回生ブレーキを考慮したエネルギー消費効率や、先行列車との適切な車間距離の維持など、高度な制御能力を評価します。
   - 重視すべき状況：次駅への減速フェーズ（回生ブレーキを活用する場面）や、安全・快適性に余裕があり省エネを追求できる時。

【現在の走行状況】
- 走行フェーズ: {features['phase']}
- 速度情報: 制限速度 {features['speed_limit']} km/h に対し、現在 {features['current_speed']:.1f} km/h で走行中
- 次駅までの距離: {features['dist_to_next_station']:.1f} m
- 運行状況: 計画ダイヤに対し {features['delay']} 秒の遅延
- 現在の勾配: {features['current_gradient']} ‰
- 前方の制限情報: {features['next_limit_info']}
- 前方の勾配情報: {features['next_gradient_info']}

【推論と出力の指示】
上記の「現在の走行状況」を論理的に解釈し、フェーズや潜在的リスク（急勾配、速度制限の接近など）に最も適した重み（w_surv, w_conf, w_comp）と、
その重みとした評価理由（reason）を決定してください。
出力は必ず以下のJSONフォーマットのみとし、理由などのテキストをJSONの外に一切含めないでください。

{{
  "reason": "制限速度に接近しており、かつ下り勾配であるため、速度超過リスクが高い状況です。そのためSurvivalを最も高く設定し、
            次いで回生ブレーキを活用できる状況であるためCompetenceをやや高めに設定します。",
  "w_surv": 1.0,
  "w_conf": 0.8,
  "w_comp": 0.9
}}
"""

    def process_and_save(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict):
        if getattr(tr, 'run_status', '') == "STOPPED" or tr.speed < 1e-3:
            return

        features = self.extract_features(tr, segments, time, nominal_times, actual_arrivals)
        prompt = self.generate_prompt(features)
        
        reason, w_surv, w_conf, w_comp = call_llm_for_weights(prompt)
        
        row = [
            round(time, 1), tr.id, features["phase"], features["speed_limit"], 
            round(features["current_speed"], 2), round(features["dist_to_next_station"], 1), 
            features["delay"], features["current_gradient"], features["next_limit_info"], 
            features["next_gradient_info"], w_surv, w_conf, w_comp, reason
        ]
        
        with open(self.output_csv, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(row)
        
        # ▼▼▼ 追加：エラーメッセージを含んでいれば False、それ以外は True を返す ▼▼▼
        if reason.startswith("APIキー未設定") or reason.startswith("エラー発生"):
            return False
        return True