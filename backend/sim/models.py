from dataclasses import dataclass
from typing import List, Tuple
import math

@dataclass
class Station:
    id: str
    name: str
    x: float
    y: float
    kind: str = "station"
    length: float = 0.0
    stop_time: float = 0.0

    def distance_to(self, other: "Station") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    @property
    def is_stop(self) -> bool:
        return self.kind != "waypoint"

@dataclass
class Segment:
    id: str
    start: Station
    end: Station
    length: float

@dataclass
class Train:
    """
    列車オブジェクト：位置、速度、加速度の計算に使用
    - id: 列車ID
    - length: 列車の長さ（м）
    - max_speed: 最大速度（km/h）
    - accel: 加速度（km/h/s） - 低精度モード用固定値
    - decel: 減速度（km/h/s） - 低精度モード用固定値
    - route: 経由駅のリスト（Station, Station）タプル
    - mode: シミュレーションモード 'low_precision'（固定値）または 'high_precision'（制動曲線）
    """
    id: str
    length: float
    max_speed: float
    accel: float
    decel: float
    route: List[Tuple[Station, Station]]
    route_ids: List[str]
    mode: str = 'low_precision'  # 'low_precision' | 'high_precision'

    speed: float = 0.0
    segment_index: int = 0
    pos_in_segment: float = 0.0
    current_station_index: int = 0
    stop_remaining: float = 0.0
    stop_pending: bool = False
    finished: bool = False
    laps_completed: int = 0

    def current_segment(self):
        """現在のセグメント（線路）を取得"""
        if self.segment_index >= len(self.route):
            return None
        return self.route[self.segment_index]
