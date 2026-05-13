import json

from backend.sim.engine import run_simulation


def test_two_trains_no_crash():
    with open("backend/data/sample_network.json", "r", encoding="utf-8") as f:
        network = json.load(f)

    trains = [
        {"train_id": "T1", "route_id": "R_A", "max_speed": 25.0, "accel": 2.0, "decel": 3.0, "length": 80.0},
        {"train_id": "T2", "route_id": "R_A", "max_speed": 20.0, "accel": 2.0, "decel": 3.0, "length": 80.0, "start_time": 5.0},
    ]

    states = run_simulation(network, trains, dt=0.5, duration=80.0, simulation_mode="high_precision")
    assert len(states) > 0
    train_ids = {s["train_id"] for s in states}
    assert "T1" in train_ids and "T2" in train_ids
