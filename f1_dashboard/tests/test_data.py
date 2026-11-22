"""Data-layer behaviour that doesn't need the network."""
import pandas as pd

from f1dash import data
from conftest import make_car, make_session


def test_practice_drivers_ordered_by_best_valid_lap():
    sd = make_session("Practice 1")
    sd.results["Position"] = float("nan")  # practice has no classification
    sd.results = sd.results.iloc[::-1]     # entry order: CCC, BBB, AAA
    # Give CCC the quickest lap, but deleted: it must not count.
    i = sd.laps.index[(sd.laps["Driver"] == "CCC") & (sd.laps["LapNumber"] == 2)][0]
    sd.laps.loc[i, ["LapTime", "Deleted"]] = [pd.Timedelta(seconds=80), True]
    assert sd.drivers() == ["AAA", "BBB", "CCC"]


def test_race_drivers_follow_classification():
    assert make_session().drivers() == ["AAA", "BBB", "CCC"]


class _Lap(pd.Series):
    """Minimal stand-in for fastf1.core.Lap."""
    _metadata = ["pos_fails"]

    def get_car_data(self):
        car = make_car(50.0)
        return type("T", (), {"add_distance": lambda s: car})()

    def get_telemetry(self):
        if self.pos_fails:
            raise ValueError("no position data")
        return make_car(50.0)


def _lap(pos_fails):
    lap = _Lap({"LapTime": pd.Timedelta(seconds=100)})
    lap.pos_fails = pos_fails
    return lap


def test_lap_telemetry_survives_missing_position_data():
    t = data.lap_telemetry(_lap(pos_fails=True))
    assert t is not None and not t.car.empty and t.pos.empty
    assert not data.lap_telemetry(_lap(pos_fails=False)).pos.empty


def test_started_sessions_includes_weekend_in_progress():
    ts = pd.Timestamp
    sched = pd.DataFrame({
        "RoundNumber": [1, 2, 3], "EventName": ["Done GP", "Live GP", "Future GP"],
        **{f"Session{i}": [n] * 3 for i, n in enumerate(
            ["Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"], start=1)},
        **{f"Session{i}DateUtc": [ts(f"2026-03-0{i}"), ts(f"2026-04-0{i}"), ts(f"2026-05-0{i}")]
           for i in range(1, 6)},
    })
    out = data.started_sessions(sched, now=ts("2026-04-04 12:00"))  # after Live GP quali
    assert out["EventName"].tolist() == ["Live GP", "Done GP"]
    assert out.iloc[0]["Sessions"] == ["Practice 1", "Practice 2", "Practice 3", "Qualifying"]
    assert out.iloc[1]["Sessions"][-1] == "Race"
    assert data.started_sessions(sched, now=ts("2026-01-01")).empty
