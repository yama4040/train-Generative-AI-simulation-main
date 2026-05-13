import json

from backend.sim.engine import RuntimeTrain, _build_network, _section_occupancy, _state_for_train, run_simulation


def load_network():
    with open("backend/data/sample_network.json", "r", encoding="utf-8") as f:
        return json.load(f)


def train(train_id, route_id, **kwargs):
    return {
        "train_id": train_id,
        "route_id": route_id,
        "max_speed": kwargs.get("max_speed", 40.0),
        "accel": kwargs.get("accel", 3.0),
        "decel": kwargs.get("decel", 4.0),
        "length": kwargs.get("length", 80.0),
        "start_time": kwargs.get("start_time", 0.0),
    }


def test_single_train_moves_forward():
    network = load_network()
    states = run_simulation(network, [train("T1", "R_A", max_speed=30.0)], dt=0.5, duration=30.0, simulation_mode="high_precision")
    assert len(states) > 0
    xs = [s["x"] for s in states if s["train_id"] == "T1"]
    assert xs[-1] >= xs[0]


def test_decel_does_not_exceed_limit():
    network = load_network()
    dt = 0.5
    decel = 4.0
    states = run_simulation(
        network,
        [train("T1", "R_A", max_speed=100.0, accel=3.2, decel=decel)],
        dt=dt,
        duration=120.0,
        simulation_mode="high_precision",
    )
    speeds = [s["speed"] for s in states if s["train_id"] == "T1"]
    max_drop = decel * dt + 1e-2
    for prev, curr in zip(speeds, speeds[1:]):
        if curr < prev:
            assert prev - curr <= max_drop


def test_start_time_delays_departure():
    network = load_network()
    dt = 0.5
    start_time = 2.0
    states = run_simulation(
        network,
        [train("T1", "R_A", max_speed=20.0, start_time=start_time)],
        dt=dt,
        duration=5.0,
        simulation_mode="high_precision",
    )
    train_states = [state for state in states if state["train_id"] == "T1"]
    assert train_states
    assert min(state["time"] for state in train_states) >= start_time


def test_staggered_same_origin_trains_do_not_crash_before_departure():
    network = load_network()
    states = run_simulation(
        network,
        [
            train("T1", "R_A", max_speed=25.0, length=80.0, start_time=0.0),
            train("T2", "R_A", max_speed=20.0, length=80.0, start_time=5.0),
        ],
        dt=0.5,
        duration=80.0,
        simulation_mode="high_precision",
        output_interval=0.5,
    )

    assert not [state for state in states if state["status"] == "crash"]
    t2_states = [state for state in states if state["train_id"] == "T2"]
    assert t2_states
    assert min(state["time"] for state in t2_states) >= 5.0


def test_exclusive_section_allows_only_one_train_inside():
    network = load_network()
    states = run_simulation(
        network,
        [
            train("TA", "R_A", max_speed=35.0, start_time=0.0),
            train("TB", "R_B", max_speed=35.0, start_time=0.0),
        ],
        dt=0.5,
        duration=80.0,
        simulation_mode="high_precision",
        output_interval=0.5,
    )
    by_time = {}
    for state in states:
        by_time.setdefault(state["time"], []).append(state)

    for snapshot in by_time.values():
        inside = [s for s in snapshot if s.get("in_shared_section")]
        assert len(inside) <= 1

    waits = [s for s in states if s.get("wait_reason") == "exclusive_section"]
    assert waits


def test_output_interval_thins_states():
    network = load_network()
    dense = run_simulation(network, [train("T1", "R_A")], dt=0.5, duration=40.0, simulation_mode="high_precision", output_interval=0.5)
    sparse = run_simulation(network, [train("T1", "R_A")], dt=0.5, duration=40.0, simulation_mode="high_precision", output_interval=2.0)
    assert len(sparse) < len(dense)


def test_vehicle_params_control_low_precision_acceleration():
    network = {
        "stations": [
            {"id": "S1", "name": "A", "x": 0.0, "y": 0.0, "stop_time": 0.0},
            {"id": "S2", "name": "B", "x": 1000.0, "y": 0.0, "stop_time": 0.0},
        ],
        "segments": [
            {"id": "E1", "start": "S1", "end": "S2", "length": 1000.0, "bidirectional": True},
        ],
        "routes": [
            {"id": "R", "name": "R", "legs": [{"segment_id": "E1", "from": "S1", "to": "S2"}]},
        ],
        "exclusive_sections": [],
    }
    trains = [{"train_id": "T1", "route_id": "R", "max_speed": 100.0, "length": 10.0, "start_time": 0.0}]

    slow = run_simulation(
        network,
        trains,
        dt=1.0,
        duration=2.0,
        simulation_mode="low_precision",
        output_interval=1.0,
        vehicle_params={"low_precision_accel": 1.0},
    )
    fast = run_simulation(
        network,
        trains,
        dt=1.0,
        duration=2.0,
        simulation_mode="low_precision",
        output_interval=1.0,
        vehicle_params={"low_precision_accel": 5.0},
    )

    assert slow[0]["speed"] == 1.0
    assert fast[0]["speed"] == 5.0


def test_station_length_expands_route_as_station_blocks():
    network = {
        "stations": [
            {"id": "S1", "name": "A", "x": 0.0, "y": 0.0, "kind": "station", "length": 200.0, "stop_time": 0.0},
            {"id": "S2", "name": "B", "x": 1000.0, "y": 0.0, "kind": "station", "length": 200.0, "stop_time": 0.0},
            {"id": "S3", "name": "C", "x": 2000.0, "y": 0.0, "kind": "station", "length": 200.0, "stop_time": 0.0},
        ],
        "segments": [
            {"id": "E1", "start": "S1", "end": "S2", "length": 1000.0, "bidirectional": True},
            {"id": "E2", "start": "S2", "end": "S3", "length": 1000.0, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R",
                "name": "R",
                "legs": [
                    {"segment_id": "E1", "from": "S1", "to": "S2"},
                    {"segment_id": "E2", "from": "S2", "to": "S3"},
                ],
            }
        ],
        "exclusive_sections": [],
    }

    _stations, _segments, routes, _sections = _build_network(network)
    route = routes["R"]

    assert route.length == 2400.0
    assert route.station_cumulative == [0.0, 1200.0, 2400.0]
    assert [leg.length for leg in route.legs] == [100.0, 1000.0, 100.0, 100.0, 1000.0, 100.0]


def test_train_stops_with_center_at_station_center():
    network = {
        "stations": [
            {"id": "S1", "name": "A", "x": 0.0, "y": 0.0, "kind": "station", "length": 200.0, "stop_time": 0.0},
            {"id": "S2", "name": "B", "x": 1000.0, "y": 0.0, "kind": "station", "length": 200.0, "stop_time": 5.0},
            {"id": "S3", "name": "C", "x": 2000.0, "y": 0.0, "kind": "station", "length": 200.0, "stop_time": 0.0},
        ],
        "segments": [
            {"id": "E1", "start": "S1", "end": "S2", "length": 1000.0, "bidirectional": True},
            {"id": "E2", "start": "S2", "end": "S3", "length": 1000.0, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R",
                "name": "R",
                "legs": [
                    {"segment_id": "E1", "from": "S1", "to": "S2"},
                    {"segment_id": "E2", "from": "S2", "to": "S3"},
                ],
            }
        ],
        "exclusive_sections": [],
    }

    states = run_simulation(
        network,
        [train("T1", "R", max_speed=80.0, accel=3.2, decel=4.0, length=80.0)],
        dt=0.5,
        duration=180.0,
        simulation_mode="high_precision",
        output_interval=0.5,
    )

    stop_states = [
        state for state in states
        if state["train_id"] == "T1" and state["stop_remaining"] > 0 and abs(state["route_distance"] - 1200.0) < 1e-6
    ]
    assert stop_states
    assert all(abs(state["x"] - 1000.0) < 1e-6 for state in stop_states)
    assert all(abs(state["y"]) < 1e-6 for state in stop_states)


def test_exclusive_section_occupied_until_train_rear_clears():
    network = {
        "stations": [
            {"id": "S0", "name": "A", "x": 0.0, "y": 0.0, "stop_time": 0.0},
            {"id": "S1", "name": "B", "x": 200.0, "y": 0.0, "stop_time": 0.0},
            {"id": "S2", "name": "C", "x": 400.0, "y": 0.0, "stop_time": 0.0},
        ],
        "segments": [
            {"id": "E1", "start": "S0", "end": "S1", "length": 200.0, "bidirectional": True},
            {"id": "E2", "start": "S1", "end": "S2", "length": 200.0, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R",
                "name": "R",
                "legs": [
                    {"segment_id": "E1", "from": "S0", "to": "S1"},
                    {"segment_id": "E2", "from": "S1", "to": "S2"},
                ],
            }
        ],
        "exclusive_sections": [
            {"id": "X", "name": "X", "segment_ids": ["E1"], "capacity": 1, "priority_route_ids": ["R"]},
        ],
    }
    _stations, _segments, routes, _sections = _build_network(network)
    tr = RuntimeTrain(
        id="T1",
        route_id="R",
        route=routes["R"],
        length=200.0,
        max_speed=60.0,
        accel=3.0,
        decel=4.0,
        start_time=0.0,
        index=0,
        leg_index=1,
        pos_in_leg=0.0,
    )

    assert _section_occupancy([tr]) == {"X": [0]}
    tr.pos_in_leg = 200.0
    assert _section_occupancy([tr]) == {}


def test_train_passes_waypoint_without_stopping():
    network = {
        "stations": [
            {"id": "S1", "name": "Start", "kind": "station", "x": 0, "y": 0, "stop_time": 0},
            {"id": "W1", "name": "Boundary", "kind": "waypoint", "x": 1000, "y": 0, "stop_time": 0},
            {"id": "S2", "name": "End", "kind": "station", "x": 2000, "y": 0, "stop_time": 0},
        ],
        "segments": [
            {"id": "E1", "start": "S1", "end": "W1", "length": 1000, "bidirectional": True},
            {"id": "E2", "start": "W1", "end": "S2", "length": 1000, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R",
                "name": "Route",
                "legs": [
                    {"segment_id": "E1", "from": "S1", "to": "W1"},
                    {"segment_id": "E2", "from": "W1", "to": "S2"},
                ],
            }
        ],
        "exclusive_sections": [],
    }
    states = run_simulation(
        network,
        [train("T1", "R", max_speed=60.0, accel=3.2, decel=4.0)],
        dt=0.5,
        duration=120.0,
        simulation_mode="high_precision",
        output_interval=0.5,
    )
    near_waypoint = [
        state for state in states
        if 970 <= state.get("route_distance", 0) <= 1030
    ]
    assert near_waypoint
    assert min(state["speed"] for state in near_waypoint) > 1.0
    assert any(state["segment_id"] == "E2" and state["speed"] > 1.0 for state in states)


def test_log_segment_ids_include_full_train_body():
    network = {
        "stations": [
            {"id": "A", "name": "A", "kind": "waypoint", "x": 0, "y": 0, "length": 0, "stop_time": 0},
            {"id": "B", "name": "B", "kind": "waypoint", "x": 100, "y": 0, "length": 0, "stop_time": 0},
            {"id": "C", "name": "C", "kind": "waypoint", "x": 200, "y": 0, "length": 0, "stop_time": 0},
        ],
        "segments": [
            {"id": "E1", "start": "A", "end": "B", "length": 100, "bidirectional": True},
            {"id": "E2", "start": "B", "end": "C", "length": 100, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R",
                "name": "R",
                "legs": [
                    {"segment_id": "E1", "from": "A", "to": "B"},
                    {"segment_id": "E2", "from": "B", "to": "C"},
                ],
            },
        ],
        "exclusive_sections": [],
    }
    _stations, _segments, routes, _sections = _build_network(network)
    tr = RuntimeTrain(
        id="T1",
        route_id="R",
        route=routes["R"],
        length=80.0,
        max_speed=60.0,
        accel=3.0,
        decel=4.0,
        start_time=0.0,
        index=0,
        leg_index=1,
        pos_in_leg=0.0,
    )

    state = _state_for_train(0.0, tr)

    assert state["segment_id"] == "E2"
    assert state["segment_ids"] == ["E1", "E2"]


def test_finished_train_is_emitted_once_then_removed_from_log_stream():
    network = {
        "stations": [
            {"id": "A", "name": "A", "kind": "waypoint", "x": 0, "y": 0, "length": 0, "stop_time": 0},
            {"id": "B", "name": "B", "kind": "station", "x": 100, "y": 0, "length": 0, "stop_time": 0},
            {"id": "C", "name": "C", "kind": "waypoint", "x": 0, "y": 100, "length": 0, "stop_time": 0},
            {"id": "D", "name": "D", "kind": "station", "x": 1000, "y": 100, "length": 0, "stop_time": 0},
        ],
        "segments": [
            {"id": "S", "start": "A", "end": "B", "length": 100, "bidirectional": True},
            {"id": "L", "start": "C", "end": "D", "length": 1000, "bidirectional": True},
        ],
        "routes": [
            {"id": "R_SHORT", "name": "R_SHORT", "legs": [{"segment_id": "S", "from": "A", "to": "B"}]},
            {"id": "R_LONG", "name": "R_LONG", "legs": [{"segment_id": "L", "from": "C", "to": "D"}]},
        ],
        "exclusive_sections": [],
    }
    states = run_simulation(
        network,
        [
            train("T_SHORT", "R_SHORT", max_speed=60.0, length=20.0),
            train("T_LONG", "R_LONG", max_speed=60.0, length=20.0),
        ],
        dt=0.5,
        duration=120.0,
        simulation_mode="high_precision",
        output_interval=1.0,
    )
    short_states = [state for state in states if state["train_id"] == "T_SHORT"]
    finished_short = [state for state in short_states if state["status"] == "FINISHED"]

    assert len(finished_short) == 1
    finished_time = finished_short[0]["time"]
    assert not any(state["time"] > finished_time for state in short_states)
    assert any(state["train_id"] == "T_LONG" and state["time"] > finished_time for state in states)


def test_circular_routes_can_share_single_track_section():
    network = {
        "stations": [
            {"id": "A0", "name": "A Start", "x": 0, "y": 0, "stop_time": 0},
            {"id": "W", "name": "Shared West", "x": 1000, "y": 0, "stop_time": 0},
            {"id": "E", "name": "Shared East", "x": 1600, "y": 0, "stop_time": 0},
            {"id": "AM", "name": "A Outer", "x": 700, "y": -600, "stop_time": 0},
            {"id": "B0", "name": "B Start", "x": 2600, "y": 0, "stop_time": 0},
            {"id": "BM", "name": "B Outer", "x": 1900, "y": 600, "stop_time": 0},
        ],
        "segments": [
            {"id": "A1", "start": "A0", "end": "W", "length": 1000, "bidirectional": True},
            {"id": "SH", "start": "W", "end": "E", "length": 600, "bidirectional": True},
            {"id": "A2", "start": "E", "end": "AM", "length": 400, "bidirectional": True},
            {"id": "A3", "start": "AM", "end": "A0", "length": 400, "bidirectional": True},
            {"id": "B1", "start": "B0", "end": "E", "length": 1000, "bidirectional": True},
            {"id": "B2", "start": "W", "end": "BM", "length": 400, "bidirectional": True},
            {"id": "B3", "start": "BM", "end": "B0", "length": 400, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R_A",
                "name": "Route A Loop",
                "legs": [
                    {"segment_id": "A1", "from": "A0", "to": "W"},
                    {"segment_id": "SH", "from": "W", "to": "E"},
                    {"segment_id": "A2", "from": "E", "to": "AM"},
                    {"segment_id": "A3", "from": "AM", "to": "A0"},
                ],
            },
            {
                "id": "R_B",
                "name": "Route B Loop",
                "legs": [
                    {"segment_id": "B1", "from": "B0", "to": "E"},
                    {"segment_id": "SH", "from": "E", "to": "W"},
                    {"segment_id": "B2", "from": "W", "to": "BM"},
                    {"segment_id": "B3", "from": "BM", "to": "B0"},
                ],
            },
        ],
        "exclusive_sections": [
            {
                "id": "X_SHARED",
                "name": "Shared single track",
                "segment_ids": ["SH"],
                "capacity": 1,
                "priority_route_ids": ["R_A", "R_B"],
            }
        ],
    }
    states = run_simulation(
        network,
        [train("TA", "R_A", max_speed=60.0), train("TB", "R_B", max_speed=60.0)],
        dt=0.5,
        duration=180.0,
        simulation_mode="high_precision",
        output_interval=1.0,
    )
    by_time = {}
    for state in states:
        by_time.setdefault(state["time"], []).append(state)

    for snapshot in by_time.values():
        assert len([state for state in snapshot if state.get("in_shared_section")]) <= 1
    assert any(state.get("wait_reason") == "exclusive_section" for state in states)


def test_interlocking_device_reserves_section_before_entrance():
    network = {
        "stations": [
            {"id": "A", "name": "A", "kind": "waypoint", "x": 0, "y": 0, "length": 0, "stop_time": 0},
            {"id": "B", "name": "B", "kind": "waypoint", "x": 0, "y": 100, "length": 0, "stop_time": 0},
            {"id": "J", "name": "J", "kind": "waypoint", "x": 500, "y": 0, "length": 0, "stop_time": 0},
            {"id": "K", "name": "K", "kind": "waypoint", "x": 800, "y": 0, "length": 0, "stop_time": 0},
            {"id": "Z", "name": "Z", "kind": "station", "x": 1000, "y": 0, "length": 0, "stop_time": 0},
        ],
        "segments": [
            {"id": "A1", "start": "A", "end": "J", "length": 500, "bidirectional": True},
            {"id": "B1", "start": "B", "end": "J", "length": 500, "bidirectional": True},
            {"id": "SH", "start": "J", "end": "K", "length": 300, "bidirectional": True},
            {"id": "OUT", "start": "K", "end": "Z", "length": 200, "bidirectional": True},
        ],
        "routes": [
            {
                "id": "R1",
                "name": "R1",
                "legs": [
                    {"segment_id": "A1", "from": "A", "to": "J"},
                    {"segment_id": "SH", "from": "J", "to": "K"},
                    {"segment_id": "OUT", "from": "K", "to": "Z"},
                ],
            },
            {
                "id": "R2",
                "name": "R2",
                "legs": [
                    {"segment_id": "B1", "from": "B", "to": "J"},
                    {"segment_id": "SH", "from": "J", "to": "K"},
                    {"segment_id": "OUT", "from": "K", "to": "Z"},
                ],
            },
        ],
        "exclusive_sections": [
            {"id": "X", "name": "X", "segment_ids": ["SH"], "capacity": 1, "priority_route_ids": ["R1", "R2"]},
        ],
        "interlocking_devices": [
            {"id": "I1", "name": "I1", "route_id": "R1", "section_id": "X", "approach_distance": 200, "stop_margin": 0},
            {"id": "I2", "name": "I2", "route_id": "R2", "section_id": "X", "approach_distance": 200, "stop_margin": 0},
        ],
    }

    states = run_simulation(
        network,
        [train("T1", "R1", max_speed=60.0, length=100.0), train("T2", "R2", max_speed=60.0, length=100.0)],
        dt=0.5,
        duration=80.0,
        simulation_mode="high_precision",
        output_interval=0.5,
    )
    waiting_t2 = [state for state in states if state["train_id"] == "T2" and state.get("wait_reason") == "interlocking"]
    approach_t2 = [
        state for state in states
        if state["train_id"] == "T2"
        and state.get("status") == "RUNNING"
        and state.get("control_reason") == "interlocking"
        and state.get("control_block_id") == "X"
    ]

    assert waiting_t2
    assert approach_t2
    assert all(not state.get("in_shared_section") for state in waiting_t2)
    assert max(state["route_distance"] for state in waiting_t2) <= 250.0


def test_same_segment_can_belong_to_multiple_exclusive_sections():
    network = {
        "stations": [
            {"id": "A", "name": "A", "x": 0, "y": 0, "kind": "waypoint", "length": 0, "stop_time": 0},
            {"id": "B", "name": "B", "x": 500, "y": 0, "kind": "waypoint", "length": 0, "stop_time": 0},
        ],
        "segments": [
            {"id": "E", "start": "A", "end": "B", "length": 500, "bidirectional": True},
        ],
        "routes": [
            {"id": "R", "name": "R", "legs": [{"segment_id": "E", "from": "A", "to": "B"}]},
        ],
        "exclusive_sections": [
            {"id": "X1", "name": "X1", "segment_ids": ["E"], "capacity": 1, "priority_route_ids": ["R"]},
            {"id": "X2", "name": "X2", "segment_ids": ["E"], "capacity": 1, "priority_route_ids": ["R"]},
        ],
    }
    _stations, _segments, routes, _sections = _build_network(network)
    route = routes["R"]
    tr = RuntimeTrain(
        id="T1",
        route_id="R",
        route=route,
        length=100.0,
        max_speed=60.0,
        accel=3.0,
        decel=4.0,
        start_time=0.0,
        index=0,
        leg_index=0,
        pos_in_leg=100.0,
    )

    assert set(route.legs[0].section_ids) == {"X1", "X2"}
    assert _section_occupancy([tr]) == {"X1": [0], "X2": [0]}


def test_collision_outputs_crash_status():
    network = {
        "stations": [
            {"id": "A", "name": "A", "x": 0, "y": 0, "kind": "waypoint", "length": 0, "stop_time": 0},
            {"id": "B", "name": "B", "x": 1000, "y": 0, "kind": "station", "length": 0, "stop_time": 0},
        ],
        "segments": [
            {"id": "E", "start": "A", "end": "B", "length": 1000, "bidirectional": True},
        ],
        "routes": [
            {"id": "R", "name": "R", "legs": [{"segment_id": "E", "from": "A", "to": "B"}]},
        ],
        "exclusive_sections": [],
    }

    states = run_simulation(
        network,
        [train("T1", "R", length=200.0), train("T2", "R", length=200.0)],
        dt=1.0,
        duration=5.0,
        simulation_mode="high_precision",
        output_interval=1.0,
    )

    assert {state["status"] for state in states} == {"crash"}
    assert all(state["wait_reason"] == "crash" for state in states)
