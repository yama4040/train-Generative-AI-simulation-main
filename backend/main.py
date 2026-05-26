from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from starlette.websockets import WebSocketState
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Any, Dict, List, Tuple
import json
import asyncio
import io
import csv
import os
from datetime import datetime
import hashlib
import time
from .sim.engine import DEFAULT_VEHICLE_PARAMS, run_simulation, run_simulation_iter

app = FastAPI(title="Train Simulation PoC")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load sample network at startup
data_path = os.path.join(os.path.dirname(__file__), "data", "sample_network.json")
with open(data_path, "r", encoding="utf-8") as f:
    SAMPLE_NETWORK = json.load(f)

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
ROUTE_DESIGNS_PATH = os.path.join(os.path.dirname(__file__), "data", "route_designs.json")
DEFAULT_WS_BATCH_FRAMES = 60
MAX_WS_BATCH_FRAMES = 180

def _log_paths(prefix: str) -> Tuple[str, str]:
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(LOG_DIR, f"{prefix}_{ts}")
    return f"{base}_params.json", f"{base}_states.jsonl"

def _run_datetime_name() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

def _network_signature(network: Dict[str, Any]) -> str:
    canonical = json.dumps(network, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _load_route_designs() -> List[Dict[str, Any]]:
    if not os.path.exists(ROUTE_DESIGNS_PATH):
        return []
    try:
        with open(ROUTE_DESIGNS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    designs = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("network"), dict):
            continue
        try:
            network, _ = _validate_network(item["network"])
        except ValueError:
            continue
        signature = item.get("signature") or _network_signature(network)
        design_id = item.get("id") or f"design_{signature[:16]}"
        designs.append({
            "id": str(design_id),
            "name": str(item.get("name") or design_id),
            "savedAt": str(item.get("savedAt") or ""),
            "signature": str(signature),
            "network": network,
        })
    return designs

def _save_route_designs(designs: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(ROUTE_DESIGNS_PATH), exist_ok=True)
    with open(ROUTE_DESIGNS_PATH, "w", encoding="utf-8") as f:
        json.dump(designs, f, ensure_ascii=False, indent=2)

def _normalize_vehicle_params(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        raw = {}
    minimums = {
        "max_speed": 1e-6,
        "length": 1e-6,
        "weight": 1e-6,               # 追加
        "factor_of_inertia": 1.0,     # 追加 (1.0未満にはならない)
        "accel": 1e-6,
        "decel": 1e-6,
        "low_precision_accel": 1e-6,
        "low_precision_decel": 1e-6,
        "safe_gap": 0.0,
        "min_follow_speed": 0.0,
        "accel_sign_cooldown": 0.0,
        "idm_delta": 1.0,
    }
    normalized = {}
    for key, default in DEFAULT_VEHICLE_PARAMS.items():
        try:
            value = float(raw.get(key, default))
        except (TypeError, ValueError):
            value = default
        if value != value or value in (float("inf"), float("-inf")):
            value = default
        normalized[key] = max(minimums.get(key, 0.0), value)
    return normalized

def _validate_network(raw_network: Any) -> Tuple[Dict[str, Any], set]:
    if not isinstance(raw_network, dict):
        raise ValueError("network はオブジェクトである必要があります")

    stations_raw = raw_network.get("stations")
    segments_raw = raw_network.get("segments")
    routes_raw = raw_network.get("routes")
    sections_raw = raw_network.get("exclusive_sections", [])
    interlocking_raw = raw_network.get("interlocking_devices", [])
    if not isinstance(stations_raw, list) or not isinstance(segments_raw, list) or not isinstance(routes_raw, list):
        raise ValueError("network.stations / network.segments / network.routes は配列である必要があります")

    station_ids = set()
    stations = []
    for s in stations_raw:
        if not isinstance(s, dict):
            raise ValueError("station はオブジェクトである必要があります")
        sid = s.get("id")
        if not sid:
            raise ValueError("station.id が必須です")
        if "x" not in s or "y" not in s:
            raise ValueError(f"station {sid} の x/y が必須です")
        name = s.get("name") or sid
        kind = s.get("kind", "station")
        if kind not in ("station", "waypoint"):
            raise ValueError(f"station {sid} の kind は station または waypoint である必要があります")
        station_obj = {
            "id": sid,
            "name": name,
            "kind": kind,
            "x": float(s["x"]),
            "y": float(s["y"])
        }
        if "length" in s and s["length"] is not None:
            try:
                station_length = float(s["length"])
            except (TypeError, ValueError):
                raise ValueError(f"station {sid} の length は数値である必要があります")
            if station_length < 0:
                raise ValueError(f"station {sid} の length は0以上である必要があります")
            station_obj["length"] = station_length
        if "stop_time" in s and s["stop_time"] is not None:
            try:
                stop_time = float(s["stop_time"])
            except (TypeError, ValueError):
                raise ValueError(f"station {sid} の stop_time は数値である必要があります")
            if stop_time < 0:
                raise ValueError(f"station {sid} の stop_time は0以上である必要があります")
            station_obj["stop_time"] = stop_time
        stations.append(station_obj)
        station_ids.add(sid)

    segments = []
    segment_ids = set()
    segment_endpoints = {}
    for seg in segments_raw:
        if not isinstance(seg, dict):
            raise ValueError("segment はオブジェクトである必要があります")
        seg_id = seg.get("id")
        if not seg_id:
            raise ValueError("segment.id が必須です")
        if seg_id in segment_ids:
            raise ValueError(f"segment.id が重複しています: {seg_id}")
        start = seg.get("start")
        end = seg.get("end")
        if start not in station_ids or end not in station_ids:
            raise ValueError(f"segment の start/end が station に存在しません: {start}-{end}")
        segment_obj = {
            "id": seg_id,
            "start": start,
            "end": end,
            "bidirectional": bool(seg.get("bidirectional", True))
        }
        
        # --- 以下を追加 ---
        # 勾配 (gradient)
        if "gradient" in seg and seg["gradient"] is not None:
            try:
                segment_obj["gradient"] = float(seg["gradient"])
            except (TypeError, ValueError):
                raise ValueError(f"segment {seg_id} の gradient は数値である必要があります")

        # 曲線半径 (curve_radius)
        if "curve_radius" in seg and seg["curve_radius"] is not None:
            try:
                segment_obj["curve_radius"] = float(seg["curve_radius"])
            except (TypeError, ValueError):
                raise ValueError(f"segment {seg_id} の curve_radius は数値である必要があります")

        # 制限速度 (speed_limit)
        if "speed_limit" in seg and seg["speed_limit"] is not None:
            try:
                segment_obj["speed_limit"] = float(seg["speed_limit"])
            except (TypeError, ValueError):
                raise ValueError(f"segment {seg_id} の speed_limit は数値である必要があります")
        # --- ここまで追加 ---
        
        if "length" in seg and seg["length"] is not None:
            try:
                seg_length = float(seg["length"])
            except (TypeError, ValueError):
                raise ValueError(f"segment {seg_id} の length は数値である必要があります")
            if seg_length <= 0:
                raise ValueError(f"segment {seg_id} の length は0より大きい必要があります")
            segment_obj["length"] = seg_length
        if "travel_time" in seg and seg["travel_time"] is not None:
            try:
                travel_time = float(seg["travel_time"])
            except (TypeError, ValueError):
                raise ValueError(f"segment {seg_id} の travel_time は数値である必要があります")
            if travel_time <= 0:
                raise ValueError(f"segment {seg_id} の travel_time は0より大きい必要があります")
            segment_obj["travel_time"] = travel_time
        segments.append(segment_obj)
        segment_ids.add(seg_id)
        segment_endpoints[seg_id] = (start, end, segment_obj["bidirectional"])

    if len(segments) == 0:
        raise ValueError("network.segments が空です（線路が定義されていません）")

    routes = []
    route_ids = set()
    for route in routes_raw:
        if not isinstance(route, dict):
            raise ValueError("route はオブジェクトである必要があります")
        route_id = route.get("id")
        if not route_id:
            raise ValueError("route.id が必須です")
        if route_id in route_ids:
            raise ValueError(f"route.id が重複しています: {route_id}")
        legs_raw = route.get("legs")
        if not isinstance(legs_raw, list) or len(legs_raw) == 0:
            raise ValueError(f"route {route_id} の legs は1件以上必要です")
        legs = []
        last_to = None
        for leg in legs_raw:
            if not isinstance(leg, dict):
                raise ValueError(f"route {route_id} の leg はオブジェクトである必要があります")
            segment_id = leg.get("segment_id")
            from_id = leg.get("from")
            to_id = leg.get("to")
            if segment_id not in segment_ids:
                raise ValueError(f"route {route_id} の segment_id が存在しません: {segment_id}")
            if from_id not in station_ids or to_id not in station_ids:
                raise ValueError(f"route {route_id} の from/to が station に存在しません: {from_id}-{to_id}")
            start, end, bidirectional = segment_endpoints[segment_id]
            forward = from_id == start and to_id == end
            reverse = from_id == end and to_id == start
            if not forward and not reverse:
                raise ValueError(f"route {route_id} の leg が segment {segment_id} の端点と一致しません")
            if reverse and not bidirectional:
                raise ValueError(f"route {route_id} は片方向 segment {segment_id} を逆走できません")
            if last_to is not None and last_to != from_id:
                raise ValueError(f"route {route_id} の legs が連続していません: {last_to} -> {from_id}")
            legs.append({"segment_id": segment_id, "from": from_id, "to": to_id})
            last_to = to_id
        routes.append({"id": route_id, "name": route.get("name") or route_id, "legs": legs})
        route_ids.add(route_id)

    exclusive_sections = []
    if not isinstance(sections_raw, list):
        raise ValueError("network.exclusive_sections は配列である必要があります")
    section_ids = set()
    for section in sections_raw:
        if not isinstance(section, dict):
            raise ValueError("exclusive_section はオブジェクトである必要があります")
        section_id = section.get("id")
        if not section_id:
            raise ValueError("exclusive_section.id が必須です")
        if section_id in section_ids:
            raise ValueError(f"exclusive_section.id が重複しています: {section_id}")
        raw_segment_ids = section.get("segment_ids")
        if not isinstance(raw_segment_ids, list) or len(raw_segment_ids) == 0:
            raise ValueError(f"exclusive_section {section_id} の segment_ids は1件以上必要です")
        missing = [sid for sid in raw_segment_ids if sid not in segment_ids]
        if missing:
            raise ValueError(f"exclusive_section {section_id} の segment_ids が存在しません: {', '.join(missing)}")
        capacity = section.get("capacity", 1)
        try:
            capacity = int(capacity)
        except (TypeError, ValueError):
            raise ValueError(f"exclusive_section {section_id} の capacity は整数である必要があります")
        if capacity <= 0:
            raise ValueError(f"exclusive_section {section_id} の capacity は1以上である必要があります")
        priority_route_ids = section.get("priority_route_ids", [])
        if priority_route_ids is None:
            priority_route_ids = []
        if not isinstance(priority_route_ids, list):
            raise ValueError(f"exclusive_section {section_id} の priority_route_ids は配列である必要があります")
        unknown_routes = [rid for rid in priority_route_ids if rid not in route_ids]
        if unknown_routes:
            raise ValueError(f"exclusive_section {section_id} の priority_route_ids が存在しません: {', '.join(unknown_routes)}")
        exclusive_sections.append({
            "id": section_id,
            "name": section.get("name") or section_id,
            "segment_ids": [str(sid) for sid in raw_segment_ids],
            "capacity": capacity,
            "priority_route_ids": [str(rid) for rid in priority_route_ids]
        })
        section_ids.add(section_id)

    if interlocking_raw is None:
        interlocking_raw = []
    if not isinstance(interlocking_raw, list):
        raise ValueError("network.interlocking_devices は配列である必要があります")
    interlocking_devices = []
    interlocking_ids = set()
    routes_by_id = {route["id"]: route for route in routes}
    sections_by_id = {section["id"]: section for section in exclusive_sections}
    for device in interlocking_raw:
        if not isinstance(device, dict):
            raise ValueError("interlocking_device はオブジェクトである必要があります")
        device_id = device.get("id")
        if not device_id:
            raise ValueError("interlocking_device.id が必須です")
        if device_id in interlocking_ids:
            raise ValueError(f"interlocking_device.id が重複しています: {device_id}")
        route_id = device.get("route_id")
        section_id = device.get("section_id")
        if route_id not in route_ids:
            raise ValueError(f"interlocking_device {device_id} の route_id が存在しません: {route_id}")
        if section_id not in section_ids:
            raise ValueError(f"interlocking_device {device_id} の section_id が存在しません: {section_id}")
        route_segment_ids = {leg["segment_id"] for leg in routes_by_id[route_id]["legs"]}
        section_segment_ids = set(sections_by_id[section_id]["segment_ids"])
        if route_segment_ids.isdisjoint(section_segment_ids):
            raise ValueError(f"interlocking_device {device_id} の route_id は対象排他区間を通過しません: {route_id} / {section_id}")
        approach_distance = device.get("approach_distance", 0)
        stop_margin = device.get("stop_margin", 0)
        try:
            approach_distance = float(approach_distance)
            stop_margin = float(stop_margin)
        except (TypeError, ValueError):
            raise ValueError(f"interlocking_device {device_id} の approach_distance / stop_margin は数値である必要があります")
        if approach_distance < 0 or stop_margin < 0:
            raise ValueError(f"interlocking_device {device_id} の approach_distance / stop_margin は0以上である必要があります")
        interlocking_devices.append({
            "id": str(device_id),
            "name": device.get("name") or str(device_id),
            "route_id": str(route_id),
            "section_id": str(section_id),
            "approach_distance": approach_distance,
            "stop_margin": stop_margin,
        })
        interlocking_ids.add(device_id)

    return {
        "stations": stations,
        "segments": segments,
        "routes": routes,
        "exclusive_sections": exclusive_sections,
        "interlocking_devices": interlocking_devices,
    }, route_ids

def _normalize_trains(raw_trains: Any, route_ids: set) -> List[Dict[str, Any]]:
    if raw_trains is None:
        return []
    if not isinstance(raw_trains, list):
        raise ValueError("trains は配列である必要があります")

    trains = []
    for i, t in enumerate(raw_trains):
        if not isinstance(t, dict):
            raise ValueError(f"trains[{i}] はオブジェクトである必要があります")
        route_id = t.get("route_id")
        if route_id not in route_ids:
            raise ValueError(f"trains[{i}] の route_id が network.routes に存在しません: {route_id}")
        if "length" not in t:
            raise ValueError(f"trains[{i}] の length が必須です")
        try:
            length = float(t.get("length"))
        except (TypeError, ValueError):
            raise ValueError(f"trains[{i}] の length は数値である必要があります")
        if length <= 0:
            raise ValueError(f"trains[{i}] の length は0より大きい必要があります")
        start_time = t.get("start_time", 0.0)
        try:
            start_time = float(start_time)
        except (TypeError, ValueError):
            raise ValueError(f"trains[{i}] の start_time は数値である必要があります")
        if start_time < 0:
            raise ValueError(f"trains[{i}] の start_time は0以上である必要があります")
        train = dict(t)
        train["route_id"] = str(route_id)
        train["length"] = length
        train["start_time"] = start_time
        trains.append(train)

    return trains

def _route_station_ids(route: Dict[str, Any]) -> List[str]:
    station_ids = []
    for leg in route.get("legs", []):
        if not station_ids:
            station_ids.append(leg.get("from"))
        station_ids.append(leg.get("to"))
    return station_ids

def _has_circular_trains(network: Dict[str, Any], trains: List[Dict[str, Any]]) -> bool:
    routes = {route["id"]: route for route in network.get("routes", [])}
    for train in trains:
        route = routes.get(train.get("route_id"))
        if not route:
            continue
        station_ids = _route_station_ids(route)
        if len(station_ids) >= 2 and station_ids[0] == station_ids[-1]:
            return True
    return False

def _get_network_and_trains(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    raw_network = payload.get("network")
    if raw_network is None:
        network, route_ids = _validate_network(SAMPLE_NETWORK)
    else:
        network, route_ids = _validate_network(raw_network)

    trains = _normalize_trains(payload.get("trains", []), route_ids)
    return network, trains

@app.get("/api/network")
def get_network():
    return JSONResponse(SAMPLE_NETWORK)

@app.get("/api/route-designs")
def get_route_designs():
    return JSONResponse({"designs": _load_route_designs()})

@app.post("/api/route-designs")
async def save_route_design(request: Request):
    payload = await request.json()
    try:
        network, _ = _validate_network(payload.get("network"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    signature = _network_signature(network)
    designs = _load_route_designs()
    existing = next((item for item in designs if item.get("signature") == signature), None)
    saved_at = _run_datetime_name()
    requested_name = str(payload.get("name") or "").strip()
    name = requested_name or (existing.get("name") if existing else saved_at)
    design = {
        "id": existing.get("id") if existing else f"design_{signature[:16]}",
        "name": name,
        "savedAt": saved_at,
        "signature": signature,
        "network": network,
    }
    next_designs = [
        design,
        *[item for item in designs if item.get("id") != design["id"] and item.get("signature") != signature],
    ]
    _save_route_designs(next_designs)
    return JSONResponse({"design": design, "designs": next_designs})

@app.post("/api/simulate")
async def simulate(request: Request):
    """
    シミュレーション実行 API
    リクエスト: {"trains": [...], "dt": 0.5, "duration": 60, "simulation_mode": "low_precision"}
    レスポンス: CSV ファイル（時系列データをストリーミング）
    """
    payload = await request.json()
    try:
        network, trains = _get_network_and_trains(payload)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    dt = float(payload.get("dt", 0.5))
    duration = float(payload.get("duration", 60.0))
    output_interval = payload.get("output_interval", dt)
    simulation_mode = payload.get("simulation_mode", "low_precision")
    
    # ▼▼▼ これを追加 ▼▼▼
    llm_interval = float(payload.get("llm_interval", 30.0))
    # ▲▲▲ 追加ここまで ▲▲▲
    
    idm_T = payload.get("idm_T", 1.5)
    headway_target = payload.get("headway_target", 120.0)
    headway_k = payload.get("headway_k", 0.005)
    headway_vcap_min = payload.get("headway_vcap_min", 0.7)
    headway_epsilon = payload.get("headway_epsilon", 10.0)
    headway_target_opt_min = payload.get("headway_target_opt_min", 10.0)
    vehicle_params = _normalize_vehicle_params(payload.get("vehicle_params"))
    try:
        idm_T = float(idm_T)
    except (TypeError, ValueError):
        idm_T = 1.5
    try:
        headway_target = float(headway_target)
    except (TypeError, ValueError):
        headway_target = 120.0
    try:
        headway_k = float(headway_k)
    except (TypeError, ValueError):
        headway_k = 0.005
    try:
        headway_vcap_min = float(headway_vcap_min)
    except (TypeError, ValueError):
        headway_vcap_min = 0.7
    try:
        headway_epsilon = float(headway_epsilon)
    except (TypeError, ValueError):
        headway_epsilon = 10.0
    try:
        headway_target_opt_min = float(headway_target_opt_min)
    except (TypeError, ValueError):
        headway_target_opt_min = 10.0
    try:
        output_interval = float(output_interval)
    except (TypeError, ValueError):
        output_interval = dt

    # ログ保存（設定と結果）
    params_path, states_path = _log_paths("simulate")
    with open(params_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # シミュレーション実行
    states = run_simulation(
        network,
        trains,
        dt=dt,
        duration=duration,
        simulation_mode=simulation_mode,
        # ▼▼▼ これを追加 ▼▼▼
        llm_interval=llm_interval,
        # ▲▲▲ 追加ここまで ▲▲▲
        idm_T=idm_T,
        headway_target=headway_target,
        headway_k=headway_k,
        headway_vcap_min=headway_vcap_min,
        headway_epsilon=headway_epsilon,
        headway_target_opt_min=headway_target_opt_min,
        output_interval=output_interval,
        vehicle_params=vehicle_params
    )
    with open(states_path, "w", encoding="utf-8") as f:
        for row in states:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # CSV をストリーミングで返す
    def iter_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "time", "train_id", "route_id", "x", "y", "speed", "status",
            "wait_reason", "block_id", "control_reason", "control_block_id",
            "in_shared_section", "segment_id", "segment_ids",
            "stop_remaining", "segment_index", "distance", "route_distance"
        ])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for row in states:
            writer.writerow([
                row["time"],
                row["train_id"],
                row.get("route_id", ""),
                row["x"],
                row["y"],
                row["speed"],
                row.get("status", ""),
                row.get("wait_reason", ""),
                row.get("block_id", ""),
                row.get("control_reason", ""),
                row.get("control_block_id", ""),
                row.get("in_shared_section", ""),
                row.get("segment_id", ""),
                ",".join(row.get("segment_ids", []) or []),
                row.get("stop_remaining", ""),
                row.get("segment_index", ""),
                row.get("distance", ""),
                row.get("route_distance", "")
            ])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=simulation.csv"}
    )

@app.websocket("/ws/sim")
async def websocket_sim(ws: WebSocket):
    """
    WebSocket シミュレーション エンドポイント
    クライアントから受信: {"trains": [...], "dt": 0.5, "duration": 60, "simulation_mode": "low_precision"}
    サーバーから送信: 各ステップの列車状態 JSON
    """
    await ws.accept()
    states_file = None
    try:
        # クライアントからパラメータを受信
        params = await ws.receive_json()
        if isinstance(params, dict) and params.get("command") == "stop":
            return
        params_path, states_path = _log_paths("ws")
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2)
        states_file = open(states_path, "w", encoding="utf-8")
        try:
            network, trains = _get_network_and_trains(params)
        except ValueError as e:
            await ws.send_json({"error": str(e)})
            states_file.close()
            return

        dt = float(params.get("dt", 0.5))
        duration = float(params.get("duration", 60.0))
        output_interval = params.get("output_interval", dt)
        simulation_mode = params.get("simulation_mode", "low_precision")
        # ▼▼▼ これを追加 ▼▼▼
        llm_interval = float(params.get("llm_interval", 30.0))
        # ▲▲▲ 追加ここまで ▲▲▲
        idm_T = params.get("idm_T", 1.5)
        headway_target = params.get("headway_target", 120.0)
        headway_k = params.get("headway_k", 0.005)
        headway_vcap_min = params.get("headway_vcap_min", 0.7)
        headway_epsilon = params.get("headway_epsilon", 10.0)
        headway_target_opt_min = params.get("headway_target_opt_min", 10.0)
        vehicle_params = _normalize_vehicle_params(params.get("vehicle_params"))
        try:
            idm_T = float(idm_T)
        except (TypeError, ValueError):
            idm_T = 1.5
        try:
            headway_target = float(headway_target)
        except (TypeError, ValueError):
            headway_target = 120.0
        try:
            headway_k = float(headway_k)
        except (TypeError, ValueError):
            headway_k = 0.005
        try:
            headway_vcap_min = float(headway_vcap_min)
        except (TypeError, ValueError):
            headway_vcap_min = 0.7
        try:
            headway_epsilon = float(headway_epsilon)
        except (TypeError, ValueError):
            headway_epsilon = 10.0
        try:
            headway_target_opt_min = float(headway_target_opt_min)
        except (TypeError, ValueError):
            headway_target_opt_min = 10.0
        try:
            output_interval = float(output_interval)
        except (TypeError, ValueError):
            output_interval = dt
        playback_speed = params.get("playback_speed", 1.0)
        try:
            playback_speed = float(playback_speed)
        except (TypeError, ValueError):
            playback_speed = 1.0
        if playback_speed <= 0:
            playback_speed = 1.0

        
        print(f'[WebSocket受信] trains数={len(trains)}, dt={dt}, duration={duration}, mode={simulation_mode}')
        for i, train in enumerate(trains):
            print(f'  列車{i}: id={train.get("train_id")}, route_id={train.get("route_id")}, max_speed={train.get("max_speed")}')

        # Demand-driven finite buffering:
        # Circular routes may run indefinitely, but the server only computes a bounded
        # batch when the browser asks for more frames.
        if _has_circular_trains(network, trains):
            duration = None

        state_iter = iter(run_simulation_iter(
            network,
            trains,
            dt=dt,
            duration=duration,
            simulation_mode=simulation_mode,
            # ▼▼▼ これを追加 ▼▼▼
            llm_interval=llm_interval,
            # ▲▲▲ 追加ここまで ▲▲▲
            idm_T=idm_T,
            headway_target=headway_target,
            headway_k=headway_k,
            headway_vcap_min=headway_vcap_min,
            headway_epsilon=headway_epsilon,
            headway_target_opt_min=headway_target_opt_min,
            output_interval=output_interval,
            vehicle_params=vehicle_params
        ))
        pending_state = None
        state_count = 0
        complete = False

        async def send_batch(requested_frames: int):
            nonlocal pending_state, state_count, complete
            if complete:
                await ws.send_json({"type": "batch_complete", "frames": 0, "states": 0, "complete": True})
                return

            frame_limit = max(1, min(MAX_WS_BATCH_FRAMES, requested_frames))
            frames_sent = 0
            states_sent = 0
            current_time = None

            while True:
                try:
                    if pending_state is not None:
                        state = pending_state
                        pending_state = None
                    else:
                        state = next(state_iter)
                except StopIteration:
                    complete = True
                    break

                state_time = state.get("time")
                if current_time is None:
                    current_time = state_time
                    frames_sent = 1
                elif state_time != current_time:
                    if frames_sent >= frame_limit:
                        pending_state = state
                        break
                    current_time = state_time
                    frames_sent += 1

                states_file.write(json.dumps(state, ensure_ascii=False) + "\n")
                states_sent += 1
                state_count += 1
                await ws.send_json(state)

            states_file.flush()
            await ws.send_json({
                "type": "batch_complete",
                "frames": frames_sent,
                "states": states_sent,
                "complete": complete,
            })
            if state_count and state_count % 100 == 0:
                print(f'[simulation] {state_count} states sent')

        while ws.client_state == WebSocketState.CONNECTED:
            incoming = await ws.receive_json()
            if not isinstance(incoming, dict):
                continue
            if incoming.get("command") == "stop":
                print("[WebSocket] stop command received")
                break
            if incoming.get("type") == "set_playback_speed":
                continue
            if incoming.get("type") != "request_frames":
                continue
            try:
                requested = int(incoming.get("count", DEFAULT_WS_BATCH_FRAMES))
            except (TypeError, ValueError):
                requested = DEFAULT_WS_BATCH_FRAMES
            await send_batch(requested)

        print(f'[simulation complete] total states sent={state_count}')
        return

        # シミュレーション実行：各ステップの状態をストリーミング
        state_count = 0
        stop_requested = False
        target_interval = dt / playback_speed
        last_send = time.monotonic()
        for state in run_simulation_iter(
            network,
            trains,
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
            vehicle_params=vehicle_params
        ):
            if stop_requested:
                break
            if ws.client_state != WebSocketState.CONNECTED:
                break
            # --- playback_speed / stop の受信処理（キューを尽きるまで読み取り） ---
            try:
                while True:
                    incoming = await asyncio.wait_for(ws.receive_json(), timeout=0.001)
                    if not isinstance(incoming, dict):
                        continue
                    if incoming.get("command") == "stop":
                        print("[WebSocket] stop コマンドを受信しました。")
                        stop_requested = True
                        break
                    if incoming.get("type") == "set_playback_speed":
                        try:
                            new_speed = float(incoming.get("value", 1.0))
                            if new_speed > 0:
                                playback_speed = new_speed
                                target_interval = dt / playback_speed
                                print(f"[WebSocket] playback_speed更新: {playback_speed}")
                        except Exception as e:
                            print(f"[WebSocket] playback_speed更新失敗: {e}")
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                print("[WebSocket] 受信中に切断を検知しました。")
                break
            except Exception:
                pass
            if ws.client_state != WebSocketState.CONNECTED:
                print("[WebSocket] クライアント切断を検知したため送信を停止します。")
                break
            state_count += 1
            if state_count % 10 == 0:
                print(f'[シミュレーション中] {state_count} ステップ送信')
            states_file.write(json.dumps(state, ensure_ascii=False) + "\n")
            try:
                now = time.monotonic()
                elapsed = now - last_send
                if elapsed < target_interval:
                    await asyncio.sleep(target_interval - elapsed)
                await ws.send_json(state)
                last_send = time.monotonic()
            except WebSocketDisconnect:
                print("[WebSocket] 送信中に切断を検知しました。")
                break
        print(f'[シミュレーション完了] 合計 {state_count} ステップ送信')
    except WebSocketDisconnect:
        print("[WebSocket] クライアント接続切断")
    except Exception as e:
        print(f"[WebSocket エラー] {e}")
    finally:
        try:
            states_file.close()
        except Exception:
            pass
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.close()
        except Exception:
            pass
