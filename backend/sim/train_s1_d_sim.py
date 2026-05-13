import math
import csv
from datetime import datetime

# --- 軌道情報と列車仕様 ---
TRACK_LENGTH = 2.4  # 一周 2400 m

# 列車Aの共通区間（表示 1000m - 1600m）
A_GATE = 1.0  # 進入ゲート 1.0km
A_EXIT = 1.6  # 出口 1.6km

# 列車Bの共通区間（表示 1600m - 1000m）
B_GATE = 0.8  # 進入ゲート 0.8km
B_EXIT = 1.4  # 出口 1.4km

# 列車仕様 (VMAX[km/h], ACC[km/h/s], DEC[km/h/s])
A_VMAX, A_ACC, A_DEC = 45.0, 3.3, 3.5
B_VMAX, B_ACC, B_DEC = 30.0, 3.8, 3.5

# シミュレーション設定
TIME_STEP_S = 0.05   
TIME_STEP_H = TIME_STEP_S / 3600.0
MAX_SIM_TIME_S = 1000.0 
LOG_INTERVAL_S = 1.0

class Train:
    def __init__(self, name, loop_id, vmax, acc, dec, start_pos_km, gate, exit_pos):
        self.name = name
        self.loop_id = loop_id 
        self.vmax = vmax
        self.acc = acc
        self.dec = dec
        self.position = start_pos_km 
        self.velocity = 0.0
        self.gate = gate
        self.exit_pos = exit_pos
        self.is_waiting = False

    def update_motion(self, dt_s, dt_h, permission):
        """加速度・減速度を考慮した物理演算"""
        
        # ゲートまでの距離を計算（ループ考慮）
        dist_to_gate = self.gate - self.position
        if dist_to_gate < 0: # すでにゲートを通過している場合
            dist_to_gate += TRACK_LENGTH
            
        # 必要な制動距離 (d = v^2 / 2a) 
        # 単位変換：(km/h)^2 / (2 * km/h/s * 3600s/h) = km
        braking_dist = (self.velocity**2) / (2 * self.dec * 3600)

        # --- 速度決定ロジック ---
        if not permission and dist_to_gate <= braking_dist + 0.001:
            # 【減速中】許可がない、かつ制動距離内に入った場合
            # ※0.001km(1m)は演算誤差によるオーバーラン防止のバッファ
            new_velocity = max(0.0, self.velocity - self.dec * dt_s)
            
            # 停止判定：速度がほぼ0、またはゲートをわずかに超えそうな場合
            if dist_to_gate < 0.0001 or new_velocity <= 0:
                new_velocity = 0.0
                self.position = self.gate
                self.is_waiting = True
            else:
                self.is_waiting = False
        else:
            # 【加速中または巡航中】許可がある、またはゲートまで十分な距離がある
            new_velocity = min(self.vmax, self.velocity + self.acc * dt_s)
            self.is_waiting = False

        # --- 位置更新 (台形近似) ---
        avg_velocity = (self.velocity + new_velocity) / 2
        dist_moved = avg_velocity * dt_h
        
        # 走行中のみ位置を更新（待機中はゲート位置固定）
        if not self.is_waiting:
            self.position = (self.position + dist_moved) % TRACK_LENGTH
            
        self.velocity = new_velocity

    def is_in_shared(self):
        """ゲートを通過してから出口に達するまでの判定"""
        if self.is_waiting:
            return False
        # ゲートと出口の間にいるか（単純比較）
        return self.gate <= self.position < self.exit_pos

    def display_position_m(self):
        pos_m = self.position * 1000.0
        if self.loop_id == 'B':
            display_pos = TRACK_LENGTH * 1000.0 - pos_m
            return display_pos if display_pos >= 0 else display_pos + TRACK_LENGTH * 1000.0
        return pos_m

class Simulation:
    def __init__(self):
        # A_DEC, B_DEC を引数に追加
        self.train_a = Train("A1", 'A', A_VMAX, A_ACC, A_DEC, 0.0, A_GATE, A_EXIT)
        self.train_b = Train("B1", 'B', B_VMAX, B_ACC, B_DEC, 0.0, B_GATE, B_EXIT)
        self.current_time_s = 0.0
        self.log_data = []
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def run(self):
        print(f"シミュレーション開始：減速度 {A_DEC} km/h/s を適用")
        last_log_time = -LOG_INTERVAL_S
        
        while self.current_time_s < MAX_SIM_TIME_S:
            if self.current_time_s >= last_log_time + LOG_INTERVAL_S - (TIME_STEP_S/2):
                self.record_log()
                last_log_time = self.current_time_s
            
            # --- 進入許可の判定 ---
            a_permission = not self.train_b.is_in_shared()
            b_permission = not self.train_a.is_in_shared()
            
            # デッドロック回避（両方が停止位置にきたらA優先）
            if self.train_a.is_waiting and self.train_b.is_waiting:
                b_permission = False

            # 各車両の移動更新
            self.train_a.update_motion(TIME_STEP_S, TIME_STEP_H, a_permission)
            self.train_b.update_motion(TIME_STEP_S, TIME_STEP_H, b_permission)
            
            self.current_time_s += TIME_STEP_S

        self.save_to_csv()

    def record_log(self):
        entry = {'Time_s': f"{self.current_time_s:.1f}"}
        for t in [self.train_a, self.train_b]:
            entry[f'{t.name}_Pos_m'] = f"{t.display_position_m():.0f}"
            entry[f'{t.name}_Vel'] = f"{t.velocity:.1f}"
            entry[f'{t.name}_Status'] = "WAITING" if t.is_waiting else "RUNNING"
            entry[f'{t.name}_InShared'] = "YES" if t.is_in_shared() else "NO"
        self.log_data.append(entry)

    def save_to_csv(self):
        filename = f"deceleration_sim_{self.timestamp}.csv"
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.log_data[0].keys())
            writer.writeheader()
            writer.writerows(self.log_data)
        print(f"✅ 保存完了: {filename}")
        print("(各車両は許可がない場合、ゲート手前で減速を開始し停止します)")

if __name__ == "__main__":
    Simulation().run()