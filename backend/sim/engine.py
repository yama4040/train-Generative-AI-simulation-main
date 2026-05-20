"""Discrete-time train simulation engine.

The current network model is route based:
- stations define coordinates and stop times
- segments define physical track
- routes define ordered directed legs over segments
- exclusive_sections define shared single-track blocks
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
import math
from datetime import datetime # ▼▼▼ この1行を追加 ▼▼▼

from .models import Segment, Station
from .llm_data_logger import LLMDataCollector # 追加


DEFAULT_VEHICLE_PARAMS = {
    "max_speed": 60.0,
    "length": 200.0,
    "weight": 150.0,              # 追加: 車両重量 (トン)
    "factor_of_inertia": 1.1,     # 追加: 慣性係数 (一般的に1.05〜1.1程度)
    "accel": 3.0,
    "decel": 4.0,
    "low_precision_accel": 3.0,
    "low_precision_decel": 4.0,
    "safe_gap": 20.0,
    "min_follow_speed": 20.0,
    "accel_sign_cooldown": 5.0,
    "idm_delta": 4.0,
}

SAFE_GAP_M = DEFAULT_VEHICLE_PARAMS["safe_gap"]
EPS = 1e-9
STOP_EPS = 1e-6
EXCLUSIVE_GATE_EPS = 1e-3


@dataclass(frozen=True)
class RouteLeg:
    segment_id: str
    from_id: str
    to_id: str
    start: Station
    end: Station
    length: float
    section_ids: tuple[str, ...] = ()
    kind: str = "track"
    stop_station_id: Optional[str] = None
    stop_station_index: Optional[int] = None

    @property
    def section_id(self) -> Optional[str]:
        return self.section_ids[0] if self.section_ids else None


@dataclass(frozen=True)
class RoutePlan:
    id: str
    name: str
    legs: List[RouteLeg]
    station_ids: List[str]
    station_cumulative: List[float]
    cumulative: List[float]
    length: float
    circular: bool


@dataclass(frozen=True)
class ExclusiveSection:
    id: str
    name: str
    segment_ids: set[str]
    capacity: int = 1
    priority_route_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InterlockingDevice:
    id: str
    name: str
    route_id: str
    section_id: str
    approach_distance: float
    stop_margin: float
    route_distance: float


@dataclass
class RuntimeTrain:
    id: str
    route_id: str
    route: RoutePlan
    length: float
    weight: float               # 追加
    factor_of_inertia: float    # 追加
    max_speed: float
    accel: float
    decel: float
    start_time: float
    index: int

    speed: float = 0.0
    leg_index: int = 0
    pos_in_leg: float = 0.0
    stop_remaining: float = 0.0
    finished: bool = False
    laps_completed: int = 0
    wait_reason: str = ""
    block_id: str = ""
    control_reason: str = ""
    control_block_id: str = ""
    waiting_since: Optional[float] = None
    crashed: bool = False
    active: bool = True
    run_status: str = "STOPPED"  # <--- この行を追加

    def current_leg(self) -> Optional[RouteLeg]:
        if self.finished or self.leg_index >= len(self.route.legs):
            return None
        return self.route.legs[self.leg_index]

    def route_position(self) -> float:
        if self.finished:
            return self.route.length
        leg_idx = min(self.leg_index, max(0, len(self.route.cumulative) - 1))
        return self.route.cumulative[leg_idx] + self.pos_in_leg

    def absolute_route_distance(self) -> float:
        return self.laps_completed * self.route.length + self.route_position()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _vehicle_param(vehicle_params: Dict[str, Any] | None, key: str, default: float, min_value: float = 0.0) -> float:
    if vehicle_params is None:
        return default
    value = _as_float(vehicle_params.get(key), default)
    return max(min_value, value)


def _segment_length(seg: Dict[str, Any], start: Station, end: Station) -> float:
    explicit = _as_float(seg.get("length"), 0.0)
    if explicit > 0:
        return explicit
    return start.distance_to(end)


def _station_half_length(station: Station) -> float:
    if not getattr(station, "is_stop", True):
        return 0.0
    return max(0.0, _as_float(getattr(station, "length", 0.0), 0.0)) * 0.5


def _point_between(start: Station, end: Station, ratio: float) -> tuple[float, float]:
    ratio = max(0.0, min(1.0, ratio))
    return (
        start.x + (end.x - start.x) * ratio,
        start.y + (end.y - start.y) * ratio,
    )


def _route_point(point_id: str, base: Station, x: float, y: float, kind: str = "waypoint") -> Station:
    return Station(
        id=point_id,
        name=base.name,
        x=x,
        y=y,
        kind=kind,
        length=0.0,
        stop_time=0.0,
    )


def _build_network(network: Dict[str, Any]):
    stations = {s["id"]: Station(**s) for s in network.get("stations", [])}
    segments: Dict[str, Segment] = {}
    segment_raw: Dict[str, Dict[str, Any]] = {}
    for seg in network.get("segments", []):
        start = stations[seg["start"]]
        end = stations[seg["end"]]
        length = _segment_length(seg, start, end)
        seg_id = seg["id"]
        #segments[seg_id] = Segment(id=seg_id, start=start, end=end, length=length)
        # 追加: プロパティの取得
        gradient = _as_float(seg.get("gradient"), 0.0)
        curve_radius = _as_float(seg.get("curve_radius"), 0.0)
        speed_limit = _as_float(seg.get("speed_limit"), 0.0)

        # 修正: Segment初期化に引数を追加
        segments[seg_id] = Segment(
            id=seg_id, start=start, end=end, length=length,
            gradient=gradient, curve_radius=curve_radius, speed_limit=speed_limit
        )
        segment_raw[seg_id] = seg

    sections: Dict[str, ExclusiveSection] = {}
    segment_to_sections: Dict[str, List[str]] = {}
    for raw in network.get("exclusive_sections", []) or []:
        section_id = raw["id"]
        segment_ids = {str(s) for s in raw.get("segment_ids", [])}
        capacity = max(1, int(_as_float(raw.get("capacity"), 1)))
        priority = tuple(str(r) for r in raw.get("priority_route_ids", []) or [])
        section = ExclusiveSection(
            id=section_id,
            name=raw.get("name") or section_id,
            segment_ids=segment_ids,
            capacity=capacity,
            priority_route_ids=priority,
        )
        sections[section_id] = section
        for segment_id in segment_ids:
            segment_to_sections.setdefault(segment_id, []).append(section_id)

    routes: Dict[str, RoutePlan] = {}
    for raw in network.get("routes", []) or []:
        route_id = raw["id"]
        legs: List[RouteLeg] = []
        station_ids: List[str] = []
        station_cumulative: List[float] = []
        cumulative = [0.0]
        for leg_raw in raw.get("legs", []):
            segment_id = leg_raw["segment_id"]
            segment = segments[segment_id]
            from_id = leg_raw["from"]
            to_id = leg_raw["to"]
            valid_forward = from_id == segment.start.id and to_id == segment.end.id
            valid_reverse = from_id == segment.end.id and to_id == segment.start.id
            if not valid_forward and not valid_reverse:
                raise ValueError(f"route {route_id} leg {segment_id} の from/to が segment 端点と一致しません")
            if valid_reverse and segment_raw[segment_id].get("bidirectional", True) is False:
                raise ValueError(f"route {route_id} は片方向 segment {segment_id} を逆走できません")
            start = stations[from_id]
            end = stations[to_id]
            track_length = segments[segment_id].length
            if not station_ids:
                station_ids.append(from_id)
                station_cumulative.append(cumulative[-1])
            elif station_ids[-1] != from_id:
                raise ValueError(f"route {route_id} の leg が連続していません: {station_ids[-1]} -> {from_id}")
            station_ids.append(to_id)
            station_index = len(station_ids) - 1

            from_half = _station_half_length(start)
            to_half = _station_half_length(end)
            visual_total = from_half + track_length + to_half
            if visual_total > EPS:
                start_ratio = from_half / visual_total
                end_ratio = (from_half + track_length) / visual_total
            else:
                start_ratio = 0.0
                end_ratio = 1.0
            start_boundary_x, start_boundary_y = _point_between(start, end, start_ratio)
            end_boundary_x, end_boundary_y = _point_between(start, end, end_ratio)
            start_boundary = _route_point(
                f"{route_id}:{segment_id}:{from_id}:station_exit",
                start,
                start_boundary_x,
                start_boundary_y,
            )
            end_boundary = _route_point(
                f"{route_id}:{segment_id}:{to_id}:station_entrance",
                end,
                end_boundary_x,
                end_boundary_y,
            )

            if from_half > EPS:
                legs.append(RouteLeg(
                    segment_id=f"station:{from_id}:out:{segment_id}",
                    from_id=from_id,
                    to_id=start_boundary.id,
                    start=start,
                    end=start_boundary,
                    length=from_half,
                    kind="station",
                ))
                cumulative.append(cumulative[-1] + from_half)

            track_stop_station_id = to_id if to_half <= EPS and getattr(end, "is_stop", True) else None
            legs.append(RouteLeg(
                segment_id=segment_id,
                from_id=start_boundary.id,
                to_id=end_boundary.id,
                start=start_boundary,
                end=end_boundary,
                length=track_length,
                section_ids=tuple(segment_to_sections.get(segment_id, ())),
                kind="track",
                stop_station_id=track_stop_station_id,
                stop_station_index=station_index if track_stop_station_id else None,
            ))
            cumulative.append(cumulative[-1] + track_length)

            if to_half > EPS:
                is_stop = getattr(end, "is_stop", True)
                legs.append(RouteLeg(
                    segment_id=f"station:{to_id}:in:{segment_id}",
                    from_id=end_boundary.id,
                    to_id=to_id,
                    start=end_boundary,
                    end=end,
                    length=to_half,
                    kind="station",
                    stop_station_id=to_id if is_stop else None,
                    stop_station_index=station_index if is_stop else None,
                ))
                cumulative.append(cumulative[-1] + to_half)

            station_cumulative.append(cumulative[-1])
        if not legs:
            raise ValueError(f"route {route_id} に leg がありません")
        routes[route_id] = RoutePlan(
            id=route_id,
            name=raw.get("name") or route_id,
            legs=legs,
            station_ids=station_ids,
            station_cumulative=station_cumulative,
            cumulative=cumulative,
            length=cumulative[-1],
            circular=station_ids[0] == station_ids[-1],
        )

    return stations, segments, routes, sections


def _route_section_entrance_distance(route: RoutePlan, section_id: str) -> Optional[float]:
    for leg_index, leg in enumerate(route.legs):
        if section_id not in leg.section_ids:
            continue
        previous_index = leg_index - 1 if leg_index > 0 else len(route.legs) - 1
        previous_section_ids = route.legs[previous_index].section_ids if route.legs else ()
        if section_id not in previous_section_ids:
            return route.cumulative[leg_index]
    return None


def _build_interlocking_devices(
    network: Dict[str, Any],
    routes: Dict[str, RoutePlan],
    sections: Dict[str, ExclusiveSection],
) -> Dict[str, List[InterlockingDevice]]:
    devices_by_route: Dict[str, List[InterlockingDevice]] = {}
    for raw in network.get("interlocking_devices", []) or []:
        route_id = str(raw.get("route_id") or "")
        section_id = str(raw.get("section_id") or "")
        route = routes.get(route_id)
        if route is None or section_id not in sections:
            continue
        entrance_distance = _route_section_entrance_distance(route, section_id)
        if entrance_distance is None:
            continue
        approach_distance = max(0.0, _as_float(raw.get("approach_distance"), 0.0))
        stop_margin = max(0.0, _as_float(raw.get("stop_margin"), 0.0))
        route_distance = entrance_distance - approach_distance
        if route.circular and route.length > EPS:
            route_distance %= route.length
        else:
            route_distance = max(0.0, route_distance)
        device = InterlockingDevice(
            id=str(raw.get("id") or f"I_{len(devices_by_route.get(route_id, [])) + 1}"),
            name=str(raw.get("name") or raw.get("id") or "interlocking"),
            route_id=route_id,
            section_id=section_id,
            approach_distance=approach_distance,
            stop_margin=stop_margin,
            route_distance=route_distance,
        )
        devices_by_route.setdefault(route_id, []).append(device)
    for devices in devices_by_route.values():
        devices.sort(key=lambda device: device.route_distance)
    return devices_by_route


def _braking_distance(v0_kmh: float, decel_kmh_s: float, dt: float) -> float:
    """Return discrete braking distance in meters."""
    if v0_kmh <= 0 or decel_kmh_s <= 0 or dt <= 0:
        return 0.0
    step = decel_kmh_s * dt
    dist = 0.0
    v = v0_kmh
    while v > 0:
        v_next = max(0.0, v - step)
        dist += ((v + v_next) / 2.0 / 3.6) * dt
        v = v_next
    return dist


def _travel_time_for_distance(length_m: float, vmax_kmh: float, accel_kmh_s: float, decel_kmh_s: float) -> float | None:
    if length_m <= 0 or vmax_kmh <= 0 or accel_kmh_s <= 0 or decel_kmh_s <= 0:
        return None
    vmax = vmax_kmh / 3.6
    a = accel_kmh_s / 3.6
    b = decel_kmh_s / 3.6
    d_acc = (vmax * vmax) / (2 * a)
    d_dec = (vmax * vmax) / (2 * b)
    if d_acc + d_dec <= length_m:
        return (vmax / a) + (vmax / b) + ((length_m - d_acc - d_dec) / vmax)
    v_peak = math.sqrt((2 * a * b * length_m) / (a + b))
    return (v_peak / a) + (v_peak / b)


def _estimate_time_to_stop(length_m: float, v0_kmh: float, vmax_kmh: float, accel_kmh_s: float, decel_kmh_s: float) -> float:
    if length_m <= 0:
        return 0.0
    vmax = max(1e-6, vmax_kmh / 3.6)
    a = max(1e-6, accel_kmh_s / 3.6)
    b = max(1e-6, decel_kmh_s / 3.6)
    v0 = max(0.0, v0_kmh / 3.6)
    d_acc = max(0.0, (vmax * vmax - v0 * v0) / (2 * a))
    d_dec = (vmax * vmax) / (2 * b)
    if d_acc + d_dec <= length_m:
        return (max(0.0, vmax - v0) / a) + (vmax / b) + ((length_m - d_acc - d_dec) / vmax)
    v_peak_sq = (2 * a * b * length_m + b * v0 * v0) / (a + b)
    v_peak = math.sqrt(max(0.0, v_peak_sq))
    return (max(0.0, v_peak - v0) / a) + (v_peak / b)


def _point_on_leg(leg: RouteLeg, pos_m: float) -> tuple[float, float]:
    ratio = pos_m / leg.length if leg.length > 0 else 0.0
    ratio = max(0.0, min(1.0, ratio))
    x = leg.start.x + (leg.end.x - leg.start.x) * ratio
    y = leg.start.y + (leg.end.y - leg.start.y) * ratio
    return x, y


def _control_reason_for_stop(stop_reason: str, stop_at_node: bool) -> str:
    if stop_reason in ("interlocking", "exclusive_section", "safety"):
        return stop_reason
    if stop_at_node and stop_reason in ("node", "route_end"):
        return "station"
    return ""


def _control_block_id_for_stop(
    tr: RuntimeTrain,
    trains: List[RuntimeTrain],
    control_reason: str,
    forced_block_id: str,
    forced_stop_leg_index: Optional[int],
    lead_index: Optional[int],
) -> str:
    if control_reason in ("interlocking", "exclusive_section"):
        return forced_block_id
    if control_reason == "safety" and lead_index is not None and 0 <= lead_index < len(trains):
        return trains[lead_index].id
    if control_reason == "station" and forced_stop_leg_index is not None:
        leg = tr.route.legs[forced_stop_leg_index]
        if leg.stop_station_id:
            return leg.stop_station_id
        station_index = _leg_stop_station_index(tr.route, forced_stop_leg_index)
        if 0 <= station_index < len(tr.route.station_ids):
            return tr.route.station_ids[station_index]
    return ""


def _state_for_train(time: float, tr: RuntimeTrain) -> Dict[str, Any]:
    leg = tr.current_leg()
    if leg is None:
        if tr.route.legs:
            last = tr.route.legs[-1]
            x, y = last.end.x, last.end.y
            segment_id = last.segment_id
        else:
            x, y, segment_id = 0.0, 0.0, ""
    else:
        x, y = _point_on_leg(leg, tr.pos_in_leg)
        segment_id = leg.segment_id
    segment_ids = _train_occupied_segment_ids(tr)
    if not segment_ids and segment_id:
        segment_ids = [segment_id]
    in_section = bool(_train_occupied_sections(tr))
    if tr.crashed:
        status = "crash"
    elif tr.finished:
        status = "FINISHED"
    elif tr.wait_reason:
        status = "WAITING"
    elif tr.stop_remaining > 0 or tr.speed <= 1e-6:
        status = "STOPPED"
    else:
        #status = "RUNNING"
        status = getattr(tr, 'run_status', "RUNNING")
        
    return {
        "time": round(time, 3),
        "train_id": tr.id,
        "route_id": tr.route_id,
        "x": x,
        "y": y,
        "speed": round(tr.speed, 3),
        "status": status,
        "wait_reason": tr.wait_reason,
        "block_id": tr.block_id,
        "control_reason": tr.control_reason,
        "control_block_id": tr.control_block_id,
        "in_shared_section": in_section,
        "segment_id": segment_id,
        "segment_ids": segment_ids,
        "segment_index": tr.leg_index,
        "distance": round(tr.pos_in_leg, 3),
        "route_distance": round(tr.absolute_route_distance(), 3),
        "stop_remaining": round(tr.stop_remaining, 3),
    }


def _section_occupancy(trains: Iterable[RuntimeTrain], exclude_index: Optional[int] = None) -> Dict[str, List[int]]:
    occupancy: Dict[str, List[int]] = {}
    for tr in trains:
        if exclude_index is not None and tr.index == exclude_index:
            continue
        for section_id in _train_occupied_sections(tr):
            occupancy.setdefault(section_id, []).append(tr.index)
    return occupancy


def _reserved_indices(
    section_id: str,
    section_reservations: Optional[Dict[str, set[int]]] = None,
    exclude_index: Optional[int] = None,
) -> set[int]:
    reserved = set((section_reservations or {}).get(section_id, set()))
    if exclude_index is not None:
        reserved.discard(exclude_index)
    return reserved


def _intervals_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return min(a_end, b_end) - max(a_start, b_start) > EPS


def _train_body_intervals(tr: RuntimeTrain) -> list[tuple[float, float]]:
    """Return occupied route-distance intervals using route_position as the train center."""
    if not tr.active or tr.finished or tr.route.length <= 0:
        return []

    center = tr.route_position()
    half_length = tr.length * 0.5
    rear = center - half_length
    front = center + half_length
    route_length = tr.route.length

    if not tr.route.circular:
        start = max(0.0, rear)
        end = min(route_length, front)
        return [(start, end)] if end - start > EPS else []

    if tr.length >= route_length:
        return [(0.0, route_length)]

    front_mod = front % route_length
    rear_mod = rear % route_length
    if rear_mod < front_mod:
        return [(rear_mod, front_mod)] if front_mod - rear_mod > EPS else []

    intervals = []
    if route_length - rear_mod > EPS:
        intervals.append((rear_mod, route_length))
    if front_mod > EPS:
        intervals.append((0.0, front_mod))
    return intervals


def _train_occupied_segment_ids(tr: RuntimeTrain) -> list[str]:
    """Return route segments touched by the full train body, ordered rear to front."""
    if tr.finished:
        return [tr.route.legs[-1].segment_id] if tr.route.legs else []

    intervals = _train_body_intervals(tr)
    if not intervals:
        return []

    segment_ids: list[str] = []
    for body_start, body_end in intervals:
        for leg_index, leg in enumerate(tr.route.legs):
            leg_start = tr.route.cumulative[leg_index]
            leg_end = tr.route.cumulative[leg_index + 1]
            if not _intervals_overlap(body_start, body_end, leg_start, leg_end):
                continue
            if leg.segment_id not in segment_ids:
                segment_ids.append(leg.segment_id)
    return segment_ids


def _train_occupied_sections(tr: RuntimeTrain) -> set[str]:
    occupied: set[str] = set()
    intervals = _train_body_intervals(tr)
    if not intervals:
        return occupied

    for leg_index, leg in enumerate(tr.route.legs):
        if not leg.section_ids:
            continue
        leg_start = tr.route.cumulative[leg_index]
        leg_end = tr.route.cumulative[leg_index + 1]
        if any(_intervals_overlap(start, end, leg_start, leg_end) for start, end in intervals):
            occupied.update(leg.section_ids)
    return occupied


def _section_requests(trains: Iterable[RuntimeTrain]) -> Dict[str, List[int]]:
    requests: Dict[str, List[int]] = {}
    for tr in trains:
        leg = tr.current_leg()
        if not tr.active or tr.finished or tr.crashed or leg is None or not leg.section_ids:
            continue
        if tr.stop_remaining > 0:
            continue
        if tr.pos_in_leg <= EPS:
            occupied_sections = _train_occupied_sections(tr)
            for section_id in leg.section_ids:
                if section_id not in occupied_sections:
                    requests.setdefault(section_id, []).append(tr.index)
    return requests


def _leg_end_requires_stop(route: RoutePlan, leg_index: int) -> bool:
    leg = route.legs[leg_index]
    is_last_leg = leg_index >= len(route.legs) - 1
    if leg.stop_station_id is not None:
        return True
    return is_last_leg and not route.circular


def _leg_stop_station_index(route: RoutePlan, leg_index: int) -> int:
    leg = route.legs[leg_index]
    if leg.stop_station_index is not None:
        return leg.stop_station_index
    return max(0, len(route.station_ids) - 1)


def _section_is_full(
    section_id: str,
    occupancy: Dict[str, List[int]],
    sections: Dict[str, ExclusiveSection],
    section_reservations: Optional[Dict[str, set[int]]] = None,
    exclude_index: Optional[int] = None,
) -> bool:
    section = sections.get(section_id)
    if section is None:
        return False
    occupied_or_reserved = set(occupancy.get(section_id, [])) | _reserved_indices(section_id, section_reservations)
    if exclude_index is not None:
        occupied_or_reserved.discard(exclude_index)
    return len(occupied_or_reserved) >= section.capacity


def _route_distance_ahead(route: RoutePlan, current: float, target: float) -> Optional[float]:
    if route.circular and route.length > EPS:
        distance = (target - current) % route.length
        return 0.0 if distance <= STOP_EPS else distance
    if target < current - STOP_EPS:
        return None
    return max(0.0, target - current)


def _next_interlocking_device(
    tr: RuntimeTrain,
    devices_by_route: Dict[str, List[InterlockingDevice]],
    section_reservations: Optional[Dict[str, set[int]]] = None,
) -> Optional[tuple[InterlockingDevice, float]]:
    if tr.finished:
        return None
    devices = devices_by_route.get(tr.route_id, [])
    if not devices:
        return None
    occupied_sections = _train_occupied_sections(tr)
    current = tr.route_position()
    best: Optional[tuple[InterlockingDevice, float]] = None
    for device in devices:
        if tr.index in _reserved_indices(device.section_id, section_reservations):
            continue
        if device.section_id in occupied_sections:
            continue
        stop_center = device.route_distance - device.stop_margin - tr.length * 0.5
        if tr.route.circular and tr.route.length > EPS:
            stop_center %= tr.route.length
        else:
            stop_center = max(0.0, stop_center)
        distance = _route_distance_ahead(tr.route, current, stop_center)
        if distance is None:
            continue
        if best is None or distance < best[1]:
            best = (device, distance)
    return best


def _next_forced_stop(
    tr: RuntimeTrain,
    trains: List[RuntimeTrain],
    sections: Dict[str, ExclusiveSection],
    upcoming_section_permissions: Optional[Dict[int, bool]] = None,
    devices_by_route: Optional[Dict[str, List[InterlockingDevice]]] = None,
    section_reservations: Optional[Dict[str, set[int]]] = None,
) -> tuple[float, str, str, Optional[int]]:
    """Return distance to the next mandatory stop target.

    Station nodes and non-circular route ends are stop targets. Waypoints are not.
    A blocked exclusive-section entrance is also a stop target.
    """
    leg = tr.current_leg()
    if leg is None:
        return 0.0, "route_end", "", None

    occupancy = _section_occupancy(trains, exclude_index=tr.index)
    interlocking_target = _next_interlocking_device(
        tr,
        devices_by_route or {},
        section_reservations,
    )

    def interlocking_before(distance_to_target: float) -> Optional[tuple[float, str, str, Optional[int]]]:
        if interlocking_target is None:
            return None
        device, interlocking_distance = interlocking_target
        if interlocking_distance <= distance_to_target + STOP_EPS:
            return interlocking_distance, "interlocking", device.section_id, None
        return None

    distance = 0.0
    leg_index = tr.leg_index
    pos = tr.pos_in_leg
    scanned = 0
    while scanned < len(tr.route.legs):
        current_leg = tr.route.legs[leg_index]
        distance += max(0.0, current_leg.length - pos)
        stop_at_leg_end = _leg_end_requires_stop(tr.route, leg_index)

        if leg_index >= len(tr.route.legs) - 1:
            if not tr.route.circular:
                early = interlocking_before(distance)
                if early is not None:
                    return early
                return distance, "node" if stop_at_leg_end else "route_end", "", leg_index
            next_leg_index = 0
        else:
            next_leg_index = leg_index + 1

        next_leg = tr.route.legs[next_leg_index]
        entering_section_ids = [sid for sid in next_leg.section_ids if sid not in current_leg.section_ids]
        blocked_section_id = None
        for section_id in entering_section_ids:
            denied = (
                upcoming_section_permissions is not None
                and upcoming_section_permissions.get((tr.index, section_id), True) is False
            )
            if _section_is_full(
                section_id,
                occupancy,
                sections,
                section_reservations,
                exclude_index=tr.index,
            ) or denied:
                blocked_section_id = section_id
                break
        if blocked_section_id:
            stop_distance = max(0.0, distance - tr.length * 0.5)
            early = interlocking_before(stop_distance)
            if early is not None:
                return early
            return stop_distance, "exclusive_section", blocked_section_id, leg_index

        if stop_at_leg_end:
            early = interlocking_before(distance)
            if early is not None:
                return early
            return distance, "node", "", leg_index

        leg_index = next_leg_index
        pos = 0.0
        scanned += 1

    if interlocking_target is not None:
        device, interlocking_distance = interlocking_target
        return interlocking_distance, "interlocking", device.section_id, None
    return math.inf, "", "", None


def _next_section_entrances(tr: RuntimeTrain) -> list[tuple[str, float]]:
    leg = tr.current_leg()
    if not tr.active or tr.finished or tr.crashed or leg is None:
        return []

    distance = 0.0
    leg_index = tr.leg_index
    pos = tr.pos_in_leg
    scanned = 0
    while scanned < len(tr.route.legs):
        current_leg = tr.route.legs[leg_index]
        distance += max(0.0, current_leg.length - pos)
        stop_at_leg_end = _leg_end_requires_stop(tr.route, leg_index)

        if leg_index >= len(tr.route.legs) - 1:
            if not tr.route.circular:
                return []
            next_leg_index = 0
        else:
            next_leg_index = leg_index + 1

        next_leg = tr.route.legs[next_leg_index]
        entering_section_ids = [sid for sid in next_leg.section_ids if sid not in current_leg.section_ids]
        if entering_section_ids:
            stop_distance = max(0.0, distance - tr.length * 0.5)
            return [(section_id, stop_distance) for section_id in entering_section_ids]

        if stop_at_leg_end:
            return []

        leg_index = next_leg_index
        pos = 0.0
        scanned += 1

    return []


def _grant_upcoming_section_permissions(
    trains: List[RuntimeTrain],
    sections: Dict[str, ExclusiveSection],
    time: float,
    section_reservations: Optional[Dict[str, set[int]]] = None,
) -> Dict[tuple[int, str], bool]:
    permissions: Dict[tuple[int, str], bool] = {}
    occupancy = _section_occupancy(trains)
    requests: Dict[str, list[tuple[int, float]]] = {}
    for tr in trains:
        for section_id, distance in _next_section_entrances(tr):
            if tr.index in _reserved_indices(section_id, section_reservations):
                permissions[(tr.index, section_id)] = True
                continue
            requests.setdefault(section_id, []).append((tr.index, distance))

    for section_id, candidates in requests.items():
        section = sections[section_id]
        candidate_indices = {idx for idx, _distance in candidates}
        occupied = set(occupancy.get(section_id, [])) | _reserved_indices(section_id, section_reservations)
        occupied = [idx for idx in occupied if idx not in candidate_indices]
        available = max(0, section.capacity - len(occupied))
        if available <= 0:
            for idx, _distance in candidates:
                permissions[(idx, section_id)] = False
            continue

        def sort_key(candidate: tuple[int, float]):
            idx, distance = candidate
            tr = trains[idx]
            return (
                tr.waiting_since if tr.waiting_since is not None else time,
                distance,
                tr.index,
            )

        granted = {idx for idx, _distance in sorted(candidates, key=sort_key)[:available]}
        for idx, _distance in candidates:
            permissions[(idx, section_id)] = idx in granted
        if section_reservations is not None:
            reservation_set = section_reservations.setdefault(section_id, set())
            reservation_set.update(granted)

    return permissions


def _move_along_route(tr: RuntimeTrain, travel: float) -> Optional[int]:
    """Move a train across waypoint boundaries without forcing a stop."""
    reached_leg_index: Optional[int] = None
    remaining_travel = max(0.0, travel)
    while remaining_travel > STOP_EPS and not tr.finished:
        leg = tr.current_leg()
        if leg is None:
            tr.finished = True
            tr.speed = 0.0
            break
        distance_to_end = max(0.0, leg.length - tr.pos_in_leg)
        if remaining_travel < distance_to_end - STOP_EPS:
            tr.pos_in_leg += remaining_travel
            return reached_leg_index

        remaining_travel -= distance_to_end
        reached_leg_index = tr.leg_index
        tr.pos_in_leg = leg.length

        if tr.leg_index >= len(tr.route.legs) - 1:
            if tr.route.circular:
                tr.laps_completed += 1
                tr.leg_index = 0
                tr.pos_in_leg = 0.0
            else:
                tr.finished = True
                tr.speed = 0.0
                return reached_leg_index
        else:
            tr.leg_index += 1
            tr.pos_in_leg = 0.0

    return reached_leg_index


def _refresh_interlocking_reservations(
    trains: List[RuntimeTrain],
    section_reservations: Dict[str, set[int]],
    reservation_entered: set[tuple[str, int]],
) -> None:
    for section_id, indices in list(section_reservations.items()):
        for idx in list(indices):
            if idx < 0 or idx >= len(trains):
                indices.discard(idx)
                reservation_entered.discard((section_id, idx))
                continue
            tr = trains[idx]
            occupied = section_id in _train_occupied_sections(tr)
            key = (section_id, idx)
            if occupied:
                reservation_entered.add(key)
            if tr.finished or tr.crashed or (key in reservation_entered and not occupied):
                indices.discard(idx)
                reservation_entered.discard(key)
        if not indices:
            section_reservations.pop(section_id, None)


def _grant_interlocking_reservations(
    trains: List[RuntimeTrain],
    sections: Dict[str, ExclusiveSection],
    devices_by_route: Dict[str, List[InterlockingDevice]],
    section_reservations: Dict[str, set[int]],
    time: float,
) -> None:
    if not devices_by_route:
        return
    occupancy = _section_occupancy(trains)
    requests: Dict[str, list[tuple[int, float, InterlockingDevice]]] = {}
    for tr in trains:
        if not tr.active or tr.finished or tr.crashed or tr.stop_remaining > 0:
            continue
        target = _next_interlocking_device(tr, devices_by_route, section_reservations)
        if target is None:
            continue
        device, distance = target
        requests.setdefault(device.section_id, []).append((tr.index, distance, device))

    for section_id, candidates in requests.items():
        section = sections.get(section_id)
        if section is None:
            continue
        candidate_indices = {idx for idx, _distance, _device in candidates}
        used_by_others = (
            set(occupancy.get(section_id, []))
            | _reserved_indices(section_id, section_reservations)
        ) - candidate_indices
        available = max(0, section.capacity - len(used_by_others))
        if available <= 0:
            continue

        def sort_key(candidate: tuple[int, float, InterlockingDevice]):
            idx, distance, _device = candidate
            tr = trains[idx]
            return (
                tr.waiting_since if tr.waiting_since is not None else time,
                distance,
                tr.index,
            )

        granted = sorted(candidates, key=sort_key)[:available]
        reservation_set = section_reservations.setdefault(section_id, set())
        for idx, _distance, _device in granted:
            reservation_set.add(idx)


def _grant_section_permissions(
    trains: List[RuntimeTrain],
    sections: Dict[str, ExclusiveSection],
    time: float,
    section_reservations: Optional[Dict[str, set[int]]] = None,
) -> Dict[tuple[int, str], bool]:
    permissions: Dict[tuple[int, str], bool] = {}
    occupancy = _section_occupancy(trains)
    requests = _section_requests(trains)
    for section_id, request_indices in requests.items():
        section = sections[section_id]
        request_set = set(request_indices)
        occupied_or_reserved = set(occupancy.get(section_id, [])) | _reserved_indices(section_id, section_reservations)
        occupied = len([idx for idx in occupied_or_reserved if idx not in request_set])
        available = max(0, section.capacity - occupied)
        if available <= 0:
            for idx in request_indices:
                permissions[(idx, section_id)] = False
            continue

        def sort_key(idx: int):
            tr = trains[idx]
            return (
                tr.waiting_since if tr.waiting_since is not None else time,
                tr.index,
            )

        granted = set(sorted(request_indices, key=sort_key)[:available])
        for idx in request_indices:
            permissions[(idx, section_id)] = idx in granted
        if section_reservations is not None:
            reservation_set = section_reservations.setdefault(section_id, set())
            reservation_set.update(granted)
    return permissions


def _same_route_safety_distances(
    trains: List[RuntimeTrain],
    safe_gap_m: float = SAFE_GAP_M,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[int]]]:
    safety_distances: list[Optional[float]] = [None] * len(trains)
    lead_speeds: list[Optional[float]] = [None] * len(trains)
    lead_indices: list[Optional[int]] = [None] * len(trains)
    groups: Dict[str, List[int]] = {}
    for idx, tr in enumerate(trains):
        if not tr.active or tr.finished or tr.crashed:
            continue
        groups.setdefault(tr.route_id, []).append(idx)

    for indices in groups.values():
        if len(indices) < 2:
            continue
        route = trains[indices[0]].route
        if route.circular and route.length > 0:
            positions = [(idx, trains[idx].route_position() % route.length) for idx in indices]
            positions.sort(key=lambda x: (x[1], x[0]))
            for order, (follow_idx, follow_pos) in enumerate(positions):
                lead_idx, lead_pos = positions[(order + 1) % len(positions)]
                distance_ahead = (lead_pos - follow_pos) % route.length
                safe_center = distance_ahead - trains[lead_idx].length * 0.5 - safe_gap_m - trains[follow_idx].length * 0.5
                safety_distances[follow_idx] = safe_center
                lead_speeds[follow_idx] = trains[lead_idx].speed
                lead_indices[follow_idx] = lead_idx
        else:
            positions = [(idx, trains[idx].route_position()) for idx in indices]
            positions.sort(key=lambda x: (-x[1], x[0]))
            for order in range(len(positions) - 1):
                lead_idx, lead_pos = positions[order]
                follow_idx, follow_pos = positions[order + 1]
                safe_center = lead_pos - trains[lead_idx].length * 0.5 - safe_gap_m - trains[follow_idx].length * 0.5
                safety_distances[follow_idx] = safe_center - follow_pos
                lead_speeds[follow_idx] = trains[lead_idx].speed
                lead_indices[follow_idx] = lead_idx
    return safety_distances, lead_speeds, lead_indices


def _can_activate_train(tr: RuntimeTrain, trains: List[RuntimeTrain], safe_gap_m: float) -> bool:
    if tr.active or tr.finished or tr.crashed:
        return False
    if tr.route.length <= EPS:
        return False

    candidate_half = tr.length * 0.5
    for other in trains:
        if other.index == tr.index or not other.active or other.finished or other.crashed:
            continue
        if other.route_id != tr.route_id:
            continue
        other_half = other.length * 0.5
        if tr.route.circular and tr.route.length > EPS:
            route_length = tr.route.length
            distance_ahead = (other.route_position() - 0.0) % route_length
            distance_behind = (0.0 - other.route_position()) % route_length
            if distance_ahead - candidate_half - other_half < safe_gap_m - EPS:
                return False
            if distance_behind - candidate_half - other_half < safe_gap_m - EPS:
                return False
            continue

        gap_to_lead = (other.route_position() - other_half) - candidate_half
        if gap_to_lead < safe_gap_m - EPS:
            return False
    return True


def _body_intervals_overlap(a: RuntimeTrain, b: RuntimeTrain) -> bool:
    return any(
        _intervals_overlap(a_start, a_end, b_start, b_end)
        for a_start, a_end in _train_body_intervals(a)
        for b_start, b_end in _train_body_intervals(b)
    )


def _mark_crashes(trains: List[RuntimeTrain], sections: Dict[str, ExclusiveSection]) -> None:
    crashed: Dict[int, str] = {}
    occupancy = _section_occupancy(trains)
    for section_id, indices in occupancy.items():
        section = sections.get(section_id)
        capacity = section.capacity if section is not None else 1
        active_indices = [idx for idx in indices if 0 <= idx < len(trains) and not trains[idx].finished]
        if len(active_indices) > capacity:
            for idx in active_indices:
                crashed[idx] = section_id

    for i in range(len(trains)):
        a = trains[i]
        if a.finished or a.crashed:
            continue
        for j in range(i + 1, len(trains)):
            b = trains[j]
            if b.finished or b.crashed or a.route_id != b.route_id:
                continue
            if _body_intervals_overlap(a, b):
                crashed[i] = crashed.get(i, "collision")
                crashed[j] = crashed.get(j, "collision")

    for idx, block_id in crashed.items():
        tr = trains[idx]
        tr.crashed = True
        tr.wait_reason = "crash"
        tr.block_id = block_id


def run_simulation_iter(
    network: Dict[str, Any],
    trains_config: List[Dict[str, Any]],
    dt: float = 0.5,
    duration: float | None = 60.0,
    simulation_mode: str = "low_precision",
    idm_T: float = 1.5,
    headway_target: float = 120.0,
    headway_k: float = 0.005,
    headway_vcap_min: float = 0.7,
    headway_epsilon: float = 10.0,
    headway_target_opt_min: float = 10.0,
    output_interval: float | None = None,
    vehicle_params: Dict[str, Any] | None = None,
):
    stations, segments, routes, sections = _build_network(network)
    devices_by_route = _build_interlocking_devices(network, routes, sections)
    if dt <= 0:
        dt = 0.5
    if output_interval is None or output_interval <= 0:
        output_interval = dt
    output_interval = max(dt, output_interval)
    headway_epsilon = max(0.0, _as_float(headway_epsilon, 10.0))
    headway_target_opt_min = max(0.0, _as_float(headway_target_opt_min, 10.0))
    default_max_speed = _vehicle_param(vehicle_params, "max_speed", DEFAULT_VEHICLE_PARAMS["max_speed"], 1e-6)
    default_length = _vehicle_param(vehicle_params, "length", DEFAULT_VEHICLE_PARAMS["length"], 1e-6)
    # --- 以下を追加 ---
    default_weight = _vehicle_param(vehicle_params, "weight", DEFAULT_VEHICLE_PARAMS["weight"], 1e-6)
    default_factor_of_inertia = _vehicle_param(vehicle_params, "factor_of_inertia", DEFAULT_VEHICLE_PARAMS["factor_of_inertia"], 1.0)
    # --- ここまで ---
    default_accel = _vehicle_param(vehicle_params, "accel", DEFAULT_VEHICLE_PARAMS["accel"], 1e-6)
    default_decel = _vehicle_param(vehicle_params, "decel", DEFAULT_VEHICLE_PARAMS["decel"], 1e-6)
    low_precision_accel = _vehicle_param(vehicle_params, "low_precision_accel", DEFAULT_VEHICLE_PARAMS["low_precision_accel"], 1e-6)
    low_precision_decel = _vehicle_param(vehicle_params, "low_precision_decel", DEFAULT_VEHICLE_PARAMS["low_precision_decel"], 1e-6)
    safe_gap = _vehicle_param(vehicle_params, "safe_gap", DEFAULT_VEHICLE_PARAMS["safe_gap"], 0.0)
    min_follow_speed = _vehicle_param(vehicle_params, "min_follow_speed", DEFAULT_VEHICLE_PARAMS["min_follow_speed"], 0.0)
    accel_sign_cooldown = _vehicle_param(vehicle_params, "accel_sign_cooldown", DEFAULT_VEHICLE_PARAMS["accel_sign_cooldown"], 0.0)
    idm_delta = _vehicle_param(vehicle_params, "idm_delta", DEFAULT_VEHICLE_PARAMS["idm_delta"], 1.0)

    trains: List[RuntimeTrain] = []
    for idx, cfg in enumerate(trains_config):
        route_id = str(cfg.get("route_id") or "")
        if route_id not in routes:
            raise ValueError(f"trains[{idx}] の route_id が network.routes に存在しません: {route_id}")
        if simulation_mode == "low_precision":
            accel = low_precision_accel
            decel = low_precision_decel
        else:
            accel = _vehicle_param(cfg, "accel", default_accel, 1e-6)
            decel = _vehicle_param(cfg, "decel", default_decel, 1e-6)
        start_time = max(0.0, _as_float(cfg.get("start_time"), 0.0))
        trains.append(RuntimeTrain(
            id=str(cfg.get("train_id") or f"T{idx + 1}"),
            route_id=route_id,
            route=routes[route_id],
            length=_vehicle_param(cfg, "length", default_length, 1e-6),
            # --- 以下を追加 ---
            weight=_vehicle_param(cfg, "weight", default_weight, 1e-6),
            factor_of_inertia=_vehicle_param(cfg, "factor_of_inertia", default_factor_of_inertia, 1.0),
            # --- ここまで ---
            max_speed=_vehicle_param(cfg, "max_speed", default_max_speed, 1e-6),
            accel=accel,
            decel=decel,
            start_time=start_time,
            stop_remaining=0.0,
            index=idx,
            active=start_time <= EPS,
        ))

    nominal_times: list[list[float]] = []
    for tr in trains:
        times = [0.0]
        previous_distance = 0.0
        for station_distance in tr.route.station_cumulative[1:]:
            interval = max(0.0, station_distance - previous_distance)
            travel = _travel_time_for_distance(interval, tr.max_speed, tr.accel, tr.decel) or 0.0
            times.append(times[-1] + travel)
            previous_distance = station_distance
        nominal_times.append(times)
    loop_nominal_times = [times[-1] if times else 0.0 for times in nominal_times]
    actual_arrivals = [dict() for _ in trains]
    headway_target_opt = [headway_target] * len(trains)
    route_groups: Dict[str, List[int]] = {}
    for idx, tr in enumerate(trains):
        route_groups.setdefault(tr.route_id, []).append(idx)
    for indices in route_groups.values():
        base = loop_nominal_times[indices[0]]
        if base > 0:
            target = max(headway_target_opt_min, base / max(1, len(indices)))
            for idx in indices:
                headway_target_opt[idx] = target

    any_circular = any(tr.route.circular for tr in trains)
    max_steps = None
    if duration is not None:
        max_steps = int(max(1, math.ceil(duration / dt)))
    
    # --- 以下を追加 ---
    #data_collector = LLMDataCollector("dqn_training_data.csv") # 追加: 初期化
    # ▼▼▼ 修正後：日時を含めたファイル名を生成する ▼▼▼
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"dqn_training_data_{timestamp}.csv"
    data_collector = LLMDataCollector(csv_filename)
    # ▲▲▲ 修正ここまで ▲▲▲
    llm_eval_interval = 30 # 例: 30ステップごとにLLMを呼び出す
    # ここまで ---

    last_accel_sign = [0] * len(trains)
    last_sign_change = [0.0] * len(trains)
    time = 0.0
    step = 0
    next_emit = 0.0
    section_reservations: Dict[str, set[int]] = {}
    reservation_entered: set[tuple[str, int]] = set()
    finished_emitted: set[int] = set()

    while True:
        for tr in trains:
            if not tr.active and time + EPS >= tr.start_time and _can_activate_train(tr, trains, safe_gap):
                tr.active = True
                tr.stop_remaining = 0.0
                tr.wait_reason = ""
                tr.block_id = ""

        _refresh_interlocking_reservations(trains, section_reservations, reservation_entered)
        _grant_interlocking_reservations(trains, sections, devices_by_route, section_reservations, time)
        permissions = _grant_section_permissions(trains, sections, time, section_reservations)
        upcoming_section_permissions = _grant_upcoming_section_permissions(trains, sections, time, section_reservations)
        safety_distances, lead_speeds, lead_indices = _same_route_safety_distances(trains, safe_gap_m=safe_gap)

        for idx, tr in enumerate(trains):
            if not tr.active or tr.crashed:
                continue

            tr.wait_reason = ""
            tr.block_id = ""
            tr.control_reason = ""
            tr.control_block_id = ""

            if tr.finished:
                continue

            # === ここから改修 ===
            if tr.stop_remaining > 0:
                prev_stop_remaining = tr.stop_remaining
                tr.stop_remaining = max(0.0, tr.stop_remaining - dt)
                
                # 【追加】停車時間がちょうどゼロになった瞬間（＝駅出発時刻）を記録
                if prev_stop_remaining > 0 and tr.stop_remaining == 0.0:
                    tr.last_station_departure_time = time
                
                tr.speed = 0.0
                continue
            # === ここまで改修 ===

            leg = tr.current_leg()
            if leg is None:
                tr.finished = True
                tr.speed = 0.0
                continue

            occupied_sections = _train_occupied_sections(tr)
            blocked_current_section = next((
                section_id for section_id in leg.section_ids
                if tr.pos_in_leg <= EPS
                and section_id not in occupied_sections
                and idx not in _reserved_indices(section_id, section_reservations)
                and permissions.get((idx, section_id), True) is False
            ), None)
            if blocked_current_section and tr.speed <= 1e-6:
                tr.speed = 0.0
                tr.wait_reason = "exclusive_section"
                tr.block_id = blocked_current_section
                tr.control_reason = "exclusive_section"
                tr.control_block_id = blocked_current_section
                if tr.waiting_since is None:
                    tr.waiting_since = time
                continue
            tr.waiting_since = None

            distance_to_end = max(0.0, leg.length - tr.pos_in_leg)
            if distance_to_end <= STOP_EPS:
                distance_to_end = 0.0
            forced_stop_distance, forced_stop_reason, forced_block_id, forced_stop_leg_index = _next_forced_stop(
                tr,
                trains,
                sections,
                upcoming_section_permissions,
                devices_by_route,
                section_reservations,
            )
            safety_distance = safety_distances[idx]
            stop_distance = forced_stop_distance
            stop_reason = forced_stop_reason
            stop_at_node = forced_stop_reason in ("node", "route_end")
            if safety_distance is not None and max(0.0, safety_distance) < stop_distance:
                stop_distance = max(0.0, safety_distance)
                stop_reason = "safety"
                stop_at_node = False
            selected_control_reason = _control_reason_for_stop(stop_reason, stop_at_node)
            selected_control_block_id = _control_block_id_for_stop(
                tr,
                trains,
                selected_control_reason,
                forced_block_id,
                forced_stop_leg_index,
                lead_indices[idx],
            )

            prev_speed = tr.speed
            decel_limit = max(1e-6, tr.decel)
            accel_limit = max(0.0, tr.accel)
            reached_target = False

            def apply_stop_pattern(stop_dist: float, max_speed_cap: float | None = None):
                nonlocal reached_target
                if stop_dist <= STOP_EPS:
                    new_speed_local = max(0.0, prev_speed - decel_limit * dt)
                    travel_local = ((prev_speed + new_speed_local) / 2.0 / 3.6) * dt
                    reached_target = new_speed_local <= 1e-6
                    return travel_local, new_speed_local
                min_speed = max(0.0, prev_speed - decel_limit * dt)
                cap = tr.max_speed if max_speed_cap is None else max(0.0, max_speed_cap)
                max_speed_allowed = min(cap, prev_speed + accel_limit * dt)

                def feasible(v1: float) -> bool:
                    travel_local = ((prev_speed + v1) / 2.0 / 3.6) * dt
                    if travel_local > stop_dist + 1e-9:
                        return False
                    remaining = stop_dist - travel_local
                    return _braking_distance(v1, decel_limit, dt) <= remaining + 1e-9

                if feasible(max_speed_allowed):
                    new_speed_local = max_speed_allowed
                elif feasible(min_speed):
                    lo, hi = min_speed, max_speed_allowed
                    for _ in range(24):
                        mid = (lo + hi) * 0.5
                        if feasible(mid):
                            lo = mid
                        else:
                            hi = mid
                    new_speed_local = lo
                else:
                    new_speed_local = min_speed

                travel_local = ((prev_speed + new_speed_local) / 2.0 / 3.6) * dt
                reached_target_local = travel_local >= stop_dist - STOP_EPS and new_speed_local <= 1e-6
                if reached_target_local:
                    travel_local = stop_dist
                    new_speed_local = 0.0
                reached_target = reached_target_local
                return travel_local, new_speed_local

            if simulation_mode == "headway_control" and stop_at_node and forced_stop_leg_index is not None:
                next_station_index = _leg_stop_station_index(tr.route, forced_stop_leg_index)
                lap_index = tr.laps_completed
                if tr.route.circular and next_station_index >= len(tr.route.station_ids):
                    next_station_index = 0
                    lap_index += 1
                v_cap = tr.max_speed
                nominal = nominal_times[idx]
                if next_station_index < len(nominal):
                    target_base = tr.start_time + lap_index * loop_nominal_times[idx] + nominal[next_station_index]
                    target_time = target_base
                    lead_idx = lead_indices[idx]
                    if lead_idx is not None:
                        lead_actual = actual_arrivals[lead_idx].get((lap_index, next_station_index))
                        if lead_actual is not None:
                            target_time = max(target_base, lead_actual + headway_target_opt[idx])
                    t_est = _estimate_time_to_stop(distance_to_end, prev_speed, tr.max_speed, tr.accel, tr.decel)
                    delta_t = (time + t_est) - target_time
                    if delta_t < -abs(headway_epsilon):
                        factor = max(headway_vcap_min, min(1.0, 1.0 + headway_k * delta_t))
                        v_cap = tr.max_speed * factor
                travel, new_speed = apply_stop_pattern(stop_distance, v_cap)
            elif simulation_mode == "follow_idm" and not stop_at_node:
                safe_dist = max(0.0, safety_distance or 0.0)
                min_speed_target = min(min_follow_speed, tr.max_speed)
                stop_contact = _braking_distance(max(prev_speed, min_speed_target), decel_limit, dt) >= safe_dist - 1e-9
                if safe_dist <= 0.0 or stop_contact:
                    travel, new_speed = apply_stop_pattern(safe_dist)
                else:
                    v = max(0.0, prev_speed)
                    v_lead = lead_speeds[idx] if lead_speeds[idx] is not None else v
                    dv_mps = (v - v_lead) / 3.6
                    v_mps = v / 3.6
                    v0_mps = max(1e-6, tr.max_speed / 3.6)
                    a = max(1e-6, accel_limit / 3.6)
                    b = max(1e-6, decel_limit / 3.6)
                    s = max(0.1, safe_dist)
                    s_star = max(0.0, v_mps * idm_T + (v_mps * dv_mps) / (2.0 * math.sqrt(a * b)))
                    accel_mps2 = a * (1.0 - (v_mps / v0_mps) ** idm_delta - (s_star / s) ** 2)
                    accel_kmh_s = max(-decel_limit, min(accel_limit, accel_mps2 * 3.6))
                    sign = 1 if accel_kmh_s > 1e-6 else -1 if accel_kmh_s < -1e-6 else 0
                    last_sign = last_accel_sign[idx]
                    if sign != 0 and last_sign != 0 and sign != last_sign and time - last_sign_change[idx] < accel_sign_cooldown:
                        accel_kmh_s = 0.0
                        sign = 0
                    if sign != 0 and sign != last_sign:
                        last_accel_sign[idx] = sign
                        last_sign_change[idx] = time
                    elif sign == 0:
                        last_accel_sign[idx] = 0
                    new_speed = max(0.0, min(tr.max_speed, prev_speed + accel_kmh_s * dt))
                    if new_speed < min_speed_target:
                        new_speed = min(min_speed_target, prev_speed + accel_limit * dt)
                    travel = ((prev_speed + new_speed) / 2.0 / 3.6) * dt
                    if travel > safe_dist + 1e-9:
                        travel, new_speed = apply_stop_pattern(safe_dist)
                    reached_target = False
            # === ここから修正: 物理演算と惰行を考慮した高精度モードのロジック ===
            elif simulation_mode in ("high_precision", "high_precision_llm"):
                current_leg = tr.current_leg()
                if current_leg is None:
                    travel, new_speed = 0.0, 0.0
                    tr.run_status = "STOPPED"
                else:
                    # 【修正】物理セグメントIDを抽出して属性を取得する
                    raw_seg_id = current_leg.segment_id.split(':')[-1]
                    current_seg = segments.get(raw_seg_id)
                    
                    # 現在の区間の制限速度を取得（未設定なら列車の最高速度）
                    seg_limit = getattr(current_seg, 'speed_limit', 0.0) if current_seg else 0.0
                    current_limit = seg_limit if seg_limit > 0 else tr.max_speed
                    current_limit = min(current_limit, tr.max_speed)
                    
                    # 停止位置・前方制限速度までの距離を取得
                    limit_dist, limit_speed = _next_speed_limit_target(tr, segments)
                    
                    # 減速開始距離の計算 (余裕を持たせるためのマージンを追加)
                    margin = (tr.speed / 3.6) * dt
                    req_stop_dist = _braking_distance(tr.speed, tr.decel, dt, 0.0) + margin
                    req_limit_dist = 0.0
                    if limit_speed > 0 and limit_speed < tr.speed:
                        req_limit_dist = _braking_distance(tr.speed, tr.decel, dt, limit_speed) + margin
                    
                    # === 修正後：ヒステリシス（遊び）を設けた状態判定 ===
                    if stop_distance <= req_stop_dist:
                        calc_status = "BRAKE"
                    elif limit_dist <= req_limit_dist:
                        calc_status = "BRAKE"
                    else:
                        # 前回（1ステップ前）のステータスを取得
                        prev_status = getattr(tr, 'run_status', "STOPPED")
                        
                        if prev_status == "COAST":
                            # 現在が惰行中の場合、制限速度より 15.0 km/h 落ちるまでは惰行を維持する
                            if tr.speed < current_limit - 15.0:
                                calc_status = "ACCELE"
                            else:
                                calc_status = "COAST"
                        else:
                            # 現在が加速中（または停止等）の場合、制限速度に達したら惰行に切り替える
                            if tr.speed >= current_limit:
                                calc_status = "COAST"
                            else:
                                calc_status = "ACCELE"
                    # === 修正ここまで ===
                    
                    
                    # 物理パラメータの取得
                    train_weight = getattr(tr, 'weight', 30.0)
                    inertia = getattr(tr, 'factor_of_inertia', 1.1)
                    gradient = getattr(current_seg, 'gradient', 0.0) if current_seg else 0.0
                    curve_radius = getattr(current_seg, 'curve_radius', 0.0) if current_seg else 0.0
                    
                    # 各種抵抗の算出
                    run_resist = 0.0
                    if train_weight > 0:
                        run_resist = ((2.089 + 0.0394 * tr.speed + 0.000675 * tr.speed**2) / train_weight) * 150.0
                    route_resist = gradient + (800.0 / curve_radius if curve_radius > 0 else 0.0)
                    
                    # 引張力の算出
                    tractive_effort = 0.0
                    if calc_status == "ACCELE" and train_weight > 0:
                        if tr.speed <= 50:
                            tractive_effort = 374752.0 / 9.8 / train_weight
                        else:
                            tractive_effort = (76.513 * tr.speed**2.0 - 16401.0 * tr.speed + 949827.0) / 9.8 / train_weight
                            
                    KGF_T_TO_KMHS = 0.03528 # kgf/t -> km/h/s 変換係数
                    
                    # 加速度の決定
                    if calc_status == "BRAKE":
                        acceleration = -tr.decel
                    elif calc_status == "ACCELE":
                        acceleration = ((tractive_effort - route_resist - run_resist) * KGF_T_TO_KMHS) / inertia
                        # 【追加・安全装置】計算上の加速度が異常値にならないようキャップする
                        acceleration = min(acceleration, tr.accel)
                    elif calc_status == "COAST":
                        acceleration = ((0.0 - route_resist - run_resist) * KGF_T_TO_KMHS) / inertia
                    
                    # フェイルセーフ: 惰行中に下り坂等で制限速度を2km/h以上超過した場合は強制ブレーキ
                    if calc_status == "COAST" and tr.speed > current_limit + 2.0:
                        calc_status = "BRAKE"
                        acceleration = -tr.decel
                    
                    # 速度の更新
                    new_speed = tr.speed + (acceleration * dt)
                    
                    # 加速時に制限速度を飛び越えないようキャップ（頭打ち）
                    if calc_status == "ACCELE" and new_speed > current_limit:
                        new_speed = current_limit
                        
                    new_speed = max(0.0, new_speed)
                    travel = ((tr.speed + new_speed) / 2.0 / 3.6) * dt
                    
                    # 停止位置の行き過ぎ防止
                    reached_target_local = False
                    if travel >= stop_distance - 1e-6:
                        travel = stop_distance
                        new_speed = 0.0
                        reached_target_local = True
                        calc_status = "STOPPED"
                    reached_target = reached_target_local
                    
                    # UI表示用ステータスの保存
                    tr.run_status = calc_status
            # === 追加ここまで ===
                
                

            free_speed = min(tr.max_speed, prev_speed + accel_limit * dt)
            if selected_control_reason and (new_speed < free_speed - 1e-6 or reached_target):
                tr.control_reason = selected_control_reason
                tr.control_block_id = selected_control_block_id

            tr.speed = new_speed
            if reached_target and stop_at_node and forced_stop_leg_index is not None:
                target_leg = tr.route.legs[forced_stop_leg_index]
                arrival_station_index = _leg_stop_station_index(tr.route, forced_stop_leg_index)
                target_station_id = target_leg.stop_station_id or tr.route.station_ids[arrival_station_index]
                target_station = stations.get(target_station_id)
                actual_arrivals[idx][(tr.laps_completed, arrival_station_index)] = time
                _move_along_route(tr, travel)
                if target_station is not None and not tr.finished:
                    tr.stop_remaining = max(0.0, _as_float(getattr(target_station, "stop_time", 0.0), 0.0))
            elif reached_target and stop_reason in ("exclusive_section", "interlocking"):
                # The train has reached a blocked gate after decelerating within limits.
                # If the gate was already overrun, crash detection below reports it.
                _move_along_route(tr, max(0.0, travel - EXCLUSIVE_GATE_EPS))
                tr.speed = 0.0
                tr.wait_reason = stop_reason
                tr.block_id = forced_block_id
                if tr.waiting_since is None:
                    tr.waiting_since = time
            else:
                _move_along_route(tr, travel)

        _mark_crashes(trains, sections)
        
       # === 追加: LLMデータ収集の呼び出し ===
        if simulation_mode == "high_precision_llm" and step % llm_eval_interval == 0:
            
            # フロントエンドに推論開始を知らせる
            yield {"type": "llm_status", "status": "thinking"}

            all_success = True
            called_any = False
            
            for tr in trains:
                if tr.active and not tr.finished and not tr.crashed:
                    # process_and_save の戻り値(True/False)を受け取る
                    success = data_collector.process_and_save(
                        tr=tr,
                        segments=segments,
                        time=time,
                        nominal_times=nominal_times[tr.index],
                        actual_arrivals=actual_arrivals[tr.index]
                    )
                    # 1つでもエラーがあれば全体をエラー扱いにする
                    if success is False:
                        all_success = False
                    called_any = True
            
            # フロントエンドに結果を知らせる
            if called_any:
                if all_success:
                    yield {"type": "llm_status", "status": "success"}
                else:
                    yield {"type": "llm_status", "status": "error"}
            else:
                yield {"type": "llm_status", "status": "idle"}

        all_finished = all(tr.finished or tr.crashed for tr in trains)
        should_emit = time + 1e-9 >= next_emit or all_finished
        if should_emit:
            for tr in trains:
                if not tr.active:
                    continue
                if tr.finished and tr.index in finished_emitted:
                    continue
                yield _state_for_train(time, tr)
                if tr.finished:
                    finished_emitted.add(tr.index)
            while next_emit <= time + 1e-9:
                next_emit += output_interval

        step += 1
        if not any_circular and all_finished:
            break
        if max_steps is not None and step >= max_steps:
            break
        time += dt


def run_simulation(
    network: Dict[str, Any],
    trains_config: List[Dict[str, Any]],
    dt: float = 0.5,
    duration: float | None = 60.0,
    simulation_mode: str = "low_precision",
    idm_T: float = 1.5,
    headway_target: float = 120.0,
    headway_k: float = 0.005,
    headway_vcap_min: float = 0.7,
    headway_epsilon: float = 10.0,
    headway_target_opt_min: float = 10.0,
    output_interval: float | None = None,
    vehicle_params: Dict[str, Any] | None = None,
):
    return list(run_simulation_iter(
        network,
        trains_config,
        dt=dt,
        duration=duration,
        simulation_mode=simulation_mode,
        idm_T=idm_T,
        headway_target=headway_target,
        headway_k=headway_k,
        headway_vcap_min=headway_vcap_min,
        headway_epsilon=headway_epsilon,
        headway_target_opt_min=headway_target_opt_min,
        output_interval=output_interval,
        vehicle_params=vehicle_params,
    ))


import math
from typing import Dict, Tuple, Any, List

def _get_run_resistance(velocity_kmh: float, weight: float) -> float:
    if weight <= 0:
        return 0.0
    v = velocity_kmh
    return ((2.089 + 0.0394 * v + 0.000675 * v**2) / weight) * 150.0

def _calc_resistance(gradient: float, curve_radius: float) -> float:
    grade_resistance = gradient
    curve_resistance = 800.0 / curve_radius if curve_radius > 0 else 0.0
    return grade_resistance + curve_resistance

def _calc_tractive_effort(velocity_kmh: float, weight: float) -> float:
    if weight <= 0:
        return 0.0
    v = velocity_kmh
    if v <= 50:
        result = 374752.0
    else:
        result = 76.513 * v**2.0 - 16401.0 * v + 949827.0
    return result / 9.8 / weight
"""
def _braking_distance(v0_kmh: float, target_v_kmh: float, decel_kmh_s: float, dt: float) -> float:
    if v0_kmh <= target_v_kmh or decel_kmh_s <= 0 or dt <= 0:
        return 0.0
    v0_ms = v0_kmh / 3.6
    vt_ms = target_v_kmh / 3.6
    decel_ms2 = decel_kmh_s / 3.6
    dist = (v0_ms**2 - vt_ms**2) / (2 * decel_ms2)
    return max(0.0, dist)
"""

def _braking_distance(v0_kmh: float, decel_kmh_s: float, dt: float, target_v_kmh: float = 0.0) -> float:
    """Return discrete braking distance in meters."""
    if v0_kmh <= target_v_kmh or decel_kmh_s <= 0 or dt <= 0:
        return 0.0
    step = decel_kmh_s * dt
    dist = 0.0
    v = v0_kmh
    while v > target_v_kmh:
        v_next = max(target_v_kmh, v - step)
        dist += ((v + v_next) / 2.0 / 3.6) * dt
        v = v_next
    return dist

def _next_speed_limit_target(tr, segments) -> tuple[float, float]:
    """前方にある「現在速度より低い制限速度」までの距離とその制限速度を返す"""
    if tr.leg_index >= len(tr.route.legs):
        return float('inf'), 0.0
        
    accum_dist = 0.0
    
    # 【修正】現在のlegの残り距離は、RouteLeg自身の length を使う
    current_leg = tr.route.legs[tr.leg_index]
    accum_dist += current_leg.length - tr.pos_in_leg

    # 次以降のセグメントを走査
    for i in range(tr.leg_index + 1, len(tr.route.legs)):
        leg = tr.route.legs[i]
        
        # 【修正】擬似セグメント(station:out:seg1等)から物理セグメントIDを抽出する
        raw_seg_id = leg.segment_id.split(':')[-1]
        seg = segments.get(raw_seg_id)
        
        limit = getattr(seg, 'speed_limit', 0.0) if seg else 0.0
        # 制限速度が設定されている場合のみ対象とする
        if limit > 0:
            return accum_dist, limit
            
        # 【修正】距離の累計にも RouteLeg自身の length を使う
        accum_dist += leg.length
        
    return float('inf'), 0.0