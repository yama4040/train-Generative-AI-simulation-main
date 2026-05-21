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
        if grad_dist <= 500 and grad_val != 0:
            direction = "上り" if grad_val > 0 else "下り"
            next_gradient_info = f"{int(grad_dist)}m先に{direction}勾配{abs(grad_val)}‰あり"
        else:
            next_gradient_info = "この先目立った勾配なし"

        # 5. 遅延の計算
        delay = 0.0
        
        # 6. フェーズの判定
        phase = self._determine_phase(tr, time, dist_to_next_station, limit_dist, limit_speed)
        
        # 6. フェーズの判定の下あたりに、現在の運転状態（ノッチ）を取得・翻訳する処理を追加
        raw_status = getattr(tr, 'run_status', '')
        if raw_status == "POWER_RUN" or raw_status == "ACCELE":
            current_notch = "力行（加速）中"
        elif raw_status == "BRAKE":
            current_notch = "ブレーキ（減速）中"
        elif raw_status == "COAST" or raw_status == "COASTING":
            current_notch = "惰行中"
        else:
            current_notch = "停止・その他"

        return {
            "time": time,
            "train_id": tr.id,
            "phase": phase,
            "current_notch": current_notch, # ←★これを追加
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
            return "次駅への減速フェーズ（駅手前400m以内）"
        elif limit_dist <= 500.0 and limit_speed < tr.speed:
            return "制限速度接近フェーズ（500m以内に制限速度があり、かつ現在速度が制限速度を超えている）"
        elif getattr(tr, 'run_status', '') == "ACCELE" and time_since_departure <= 20.0:
            return "駅出発直後の加速フェーズ（駅発車20秒以内）"
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
        # 1. 静的な指示と数式ブロック
        # raw文字列（r""" """）を使用することで、バックスラッシュ(\)や波括弧({})を
        # エスケープなしでそのままLaTeXの数式として記述できます。
        system_instruction = r"""あなたは次世代鉄道自動運転AI（DQNエージェント）の挙動を最適化する「報酬設計エキスパート」です。
現在の列車の走行状況を分析し、エージェントが安全かつ効率的に学習するための「Tri-Drive報酬モデル」の3つの重み（0.0〜1.0の範囲）を決定してください。
あなたの最終的な目標は，これらの重みを調節することで，遅延時には回復運転をする，先行列車接近時には速度を抑えるなど，各列車の運転曲線を最適化することです．

# Tri-Drive報酬モデルの定義と計算式
強化学習エージェントは以下の式で算出される総合報酬 $reward$ を最大化するように学習します。
$$reward = w_{surv} R_{surv, t} + w_{conf} R_{conf, t} + w_{comp} R_{comp, t}$$

あなたの役割は、状況に応じて各ペナルティ（$R$）への感度を決める重み（$w$）を動的に調整することです。

1. Survival (安全性/恒常性: $w_{surv}$)
   - 【計算式】 $R_{surv, t} = - \alpha_1 \max(0, v_t - v_{limit, t}) - \alpha_2 |\Delta T| - \alpha_3 \cdot \mathbb{I}(\text{done})|p_t - p_{target}|$
   - 【意図の厳格化】 制限速度の超過ペナルティ、計画ダイヤからの遅延、目標停止位置との誤差を評価します。安全限界に近づくほど、絶対的な最優先事項となります。
   - 【重視すべき状況】 制限速度への接近・超過時、急勾配による加速リスクがある時、遅延回復が急務な時。

2. Confidence (確信度/快適性: $w_{conf}$)
   - 【計算式】 $R_{conf, t} = - \beta_1 |a_t - a_{t-1}| - \beta_2 \cdot \mathbb{I}(a_t \neq 0 \land a_t \cdot a_{t-1} < 0)$
   - 【意図の厳格化】 ジャーク（加速度の変化量）と、力行・ブレーキの急激な反転によるペナルティです。乗客の乗り心地と、制御システムの不確実性（無駄なハンチング）の抑制を評価します。
   - 【重視すべき状況】 巡航フェーズや、制限速度・勾配に変化がない安定区間。安全とダイヤに余裕があり、乗り心地を最大化できる時。

3. Competence (能力/効率性: $w_{comp}$)
   - 【計算式】 $R_{comp, t} = - \eta (P_{consume, t} - P_{regen, t}) - \rho \exp\left( - \frac{p_{forward} - p_t}{\sigma} \right)$
   - 【意図の厳格化】 消費電力から回生ブレーキ電力を差し引いた正味のエネルギーコストと、先行列車への接近リスクを評価します。高度な省エネ制御能力を促します。
   - 【重視すべき状況】 減速フェーズ（回生ブレーキを最大限活用すべき場面）や、先行列車が接近している時。
"""

        # 2. 動的なデータブロック
        # ここだけ f文字列（f""" """）を使用し、featuresのデータを埋め込みます。
        current_status = f"""
# 現在の走行状況
- 走行フェーズ: {features['phase']}
- 現在の運転操作: {features['current_notch']}  # ←★これを追加
- 速度情報: 制限速度 {features['speed_limit']} km/h に対し、現在 {features['current_speed']:.1f} km/h で走行中
- 次駅までの距離: {features['dist_to_next_station']:.1f} m
- 運行状況: 計画ダイヤに対し {features['delay']} 秒の遅延
- 現在の勾配: {features['current_gradient']} ‰
- 前方の制限情報: {features['next_limit_info']}
- 前方の勾配情報: {features['next_gradient_info']}
"""

        # 3. 静的な出力指示とJSONフォーマットブロック
        # 通常の文字列（""" """）を使用することで、JSONの波括弧({})をそのまま記述できます。
        output_format = """
# 推論と出力の指示
まず「現在の走行状況」から、安全性リスク、乗り心地の優先度、省エネの機会をステップ・バイ・ステップで論理的に分析してください。その後、それぞれの重み（0.0〜1.0）を決定してください。理由（reason）は100文字程度の日本語で簡潔に説明してください。
出力は必ず以下のJSONフォーマットのみとし、マークダウンの装飾（```jsonなど）やJSON外のテキストは一切含めないでください。

{
  "reason": "制限速度に接近しており、かつ下り勾配であるため速度超過リスクが極めて高い。したがってSurvivalを最優先(0.9)とする。同時に、減速フェーズに入るため回生電力を期待してCompetenceも高め(0.7)に設定し、急ブレーキによるジャークはやむを得ないためConfidenceは低め(0.2)とする。",
  "w_surv": 0.9,
  "w_conf": 0.2,
  "w_comp": 0.7
}
"""

        # 3つの文字列ブロックを結合して返します
        return system_instruction + current_status + output_format

    def process_and_save(self, tr, segments, time: float, nominal_times: list, actual_arrivals: dict):
        if getattr(tr, 'run_status', '') == "STOPPED" or tr.speed < 1e-3:
            return

        features = self.extract_features(tr, segments, time, nominal_times, actual_arrivals)
        prompt = self.generate_prompt(features)
        
        reason, w_surv, w_conf, w_comp = call_llm_for_weights(prompt)
        
        # ▼▼▼ 修正: rowリストの先頭付近に features["current_notch"] を追加 ▼▼▼
        row = [
            round(time, 1), tr.id, features["phase"], features["current_notch"], 
            features["speed_limit"], round(features["current_speed"], 2), 
            round(features["dist_to_next_station"], 1), features["delay"], 
            features["current_gradient"], features["next_limit_info"], 
            features["next_gradient_info"], w_surv, w_conf, w_comp, reason
        ]
        
        with open(self.output_csv, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(row)
        
        # ▼▼▼ 追加：エラーメッセージを含んでいれば False、それ以外は True を返す ▼▼▼
        if reason.startswith("APIキー未設定") or reason.startswith("エラー発生"):
            return False
        return True
