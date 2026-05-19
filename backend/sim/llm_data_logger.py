import os
import json
import csv
from typing import Dict, Any
from typing import Tuple
from openai import OpenAI

def call_llm_for_weights(prompt_text: str) -> Tuple[float, float, float]:
    # ターミナル（環境変数）からURLとAPIキーを取得
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_API_URL")

    # 設定されていない場合はダミーの値を返してシミュレーションを継続
    if not api_key or not base_url:
        print("【警告】LLM_API_KEY または LLM_API_URL が設定されていません。ダミーの重み(1.0)を使用します。")
        return 1.0, 1.0, 1.0

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
                    "content": "あなたは列車の自動運転制御を評価するエキスパートです。必ず指示されたJSONフォーマットのみを出力してください。余計な解説は不要です。"
                },
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.0, # 決定論的な出力を得るために0に設定
        )

        result_text = response.choices[0].message.content
        
        # LLMがMarkdown記法（```json ... ```）を含めて返してきた場合の対策
        clean_text = result_text.strip().replace("```json", "").replace("```", "")
        weights = json.loads(clean_text)

        w_surv = float(weights.get("w_surv", 1.0))
        w_conf = float(weights.get("w_conf", 1.0))
        w_comp = float(weights.get("w_comp", 1.0))

        return w_surv, w_conf, w_comp

    except Exception as e:
        print(f"【LLMエラー】重みの取得に失敗しました: {e}")
        # APIエラー時もシミュレータが止まらないようフォールバック
        return 1.0, 1.0, 1.0

class LLMDataCollector:
    def __init__(self, output_csv: str = "dqn_training_data.csv"):
        self.output_csv = output_csv
        self.headers = [
            "time", "train_id", "phase", "speed_limit", "current_speed",
            "dist_to_next_station", "delay", "current_gradient", 
            "next_limit_info", "next_gradient_info",
            "w_surv", "w_conf", "w_comp"
        ]
        
        # ファイルが存在しなければヘッダーを作成
        if not os.path.exists(self.output_csv):
            with open(self.output_csv, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)

    def extract_features(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict) -> Dict[str, Any]:
        """シミュレータの状態からLLMに渡す情報を抽出する"""
        
        # 1. 次の駅までの距離を計算
        dist_to_next_station = 0.0
        found_station = False
        current_leg = tr.current_leg()
        
        if current_leg:
            dist_to_next_station += max(0.0, current_leg.length - tr.pos_in_leg)
            for i in range(tr.leg_index + 1, len(tr.route.legs)):
                leg = tr.route.legs[i]
                if leg.stop_station_id is not None: # 次の停車駅
                    found_station = True
                    break
                dist_to_next_station += leg.length
        if not found_station:
            dist_to_next_station = 9999.0 # 終点等の場合
            
        # 2. 現在の物理セグメント情報（制限速度、勾配）
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
        if grad_dist <= 2000 and grad_val != 0: # 2km以内を探索
            direction = "上り" if grad_val > 0 else "下り"
            next_gradient_info = f"{int(grad_dist)}m先に{direction}勾配{abs(grad_val)}‰あり"
        else:
            next_gradient_info = "この先目立った勾配なし"

        # 5. 遅延の計算（前駅の出発遅延ベース）
        delay = 0.0
        # ※ 実際には actual_arrivals と nominal_times を比較して算出します。
        # ここでは簡易的に tr.waiting_since 等を使うか、外部から渡された遅延を使用
        
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
        """4つの走行フェーズを判定する"""
        # 前駅を出発してからの時間（ここではシミュレーション開始時間からの簡易判定）
        # ※厳密には前駅出発時刻をtrオブジェクトに持たせる必要があります
        time_since_departure = time - tr.start_time 
        
        if dist_to_station <= 400.0:
            return "次駅への減速フェーズ（駅手前400m）"
        elif limit_dist <= 500.0 and limit_speed < tr.speed:
            return "制限速度接近に伴い減速中"
        elif getattr(tr, 'run_status', '') == "ACCELE" and time_since_departure <= 20.0:
            # 実際には「前駅出発時刻」からの経過時間を条件にします
            return "駅出発直後の加速フェーズ（20秒）"
        else:
            return "巡航フェーズ（駅間走行中）"

    def _next_limit_target(self, tr, segments) -> Tuple[float, float]:
        """前方にある「現在速度より低い制限速度」までの距離とその制限速度を返す"""
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
        """前方にある勾配変化ポイントまでの距離と勾配を返す"""
        if not tr.current_leg(): return float('inf'), 0.0
        current_raw_id = tr.current_leg().segment_id.split(':')[-1]
        current_grad = getattr(segments.get(current_raw_id), 'gradient', 0.0) if segments.get(current_raw_id) else 0.0
        
        accum_dist = tr.current_leg().length - tr.pos_in_leg
        for i in range(tr.leg_index + 1, len(tr.route.legs)):
            leg = tr.route.legs[i]
            raw_seg_id = leg.segment_id.split(':')[-1]
            seg = segments.get(raw_seg_id)
            grad = getattr(seg, 'gradient', 0.0) if seg else 0.0
            if grad != current_grad and abs(grad) > 0: # 勾配が変化した場所
                return accum_dist, grad
            accum_dist += leg.length
        return float('inf'), 0.0

    def generate_prompt(self, features: Dict[str, Any]) -> str:
        """LLMに渡すプロンプトテキストを生成"""
        return f"""あなたは次世代の鉄道自動運転AI（DQNエージェント）の挙動を最適化する「評価エキスパート」です。
現在の列車の走行状況を分析し、エージェントが安全かつ効率的に学習するための「Tri-Drive報酬モデル」の3つの重みを決定してください。

【Tri-Drive報酬モデルの定義】
以下の3つの指標について、現在の状況下でどれを重視すべきか（0.0〜2.0の範囲）を評価してください。
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
上記の「現在の走行状況」を論理的に解釈し、フェーズや潜在的リスク（急勾配、速度制限の接近など）に最も適した重み（w_surv, w_conf, w_comp）を決定してください。
出力は必ず以下のJSONフォーマットのみとし、理由などのテキストは一切含めないでください。

{{
  "w_surv": 1.2,
  "w_conf": 0.8,
  "w_comp": 1.0
}}
"""

    def process_and_save(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict):
        """シミュレータから呼び出され、LLM推論とCSV保存を行う"""
        # 駅停車中(STOPPED)ならスキップ
        if getattr(tr, 'run_status', '') == "STOPPED" or tr.speed < 1e-3:
            return

        # 1. 状態の特徴量抽出
        features = self.extract_features(tr, segments, time, nominal_times, actual_arrivals)
        
        # 2. プロンプト生成
        prompt = self.generate_prompt(features)
        
        # 3. LLM API呼び出し (ダミー)
        w_surv, w_conf, w_comp = call_llm_for_weights(prompt)
        
        # 4. CSVへ書き込み
        row = [
            round(time, 1), tr.id, features["phase"], features["speed_limit"], 
            round(features["current_speed"], 2), round(features["dist_to_next_station"], 1), 
            features["delay"], features["current_gradient"], features["next_limit_info"], 
            features["next_gradient_info"], w_surv, w_conf, w_comp
        ]
        
        with open(self.output_csv, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row)