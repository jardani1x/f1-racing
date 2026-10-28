"""Synthetic fixtures shaped like FastF1 output. Used only by tests: the app
never shows synthetic data."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from f1dash import data  # noqa: E402

TRACK_M = 5000.0


def make_car(speed_mps: float | np.ndarray, n: int = 500, length: float = TRACK_M) -> pd.DataFrame:
    """Constant- or profile-speed lap on a circular track of `length` metres."""
    dist = np.linspace(0, length, n)
    v = np.full(n, speed_mps) if np.isscalar(speed_mps) else np.asarray(speed_mps, float)
    t = np.concatenate([[0.0], np.cumsum(np.diff(dist) / v[1:])])
    theta = 2 * np.pi * dist / length
    r = length / (2 * np.pi)
    return pd.DataFrame({
        "Distance": dist, "Time": pd.to_timedelta(t, unit="s"), "Speed": v * 3.6,
        "Throttle": np.where(dist % 1000 < 800, 100.0, 104.0),  # 104 = invalid reading
        "Brake": dist % 1000 >= 800, "nGear": np.where(dist % 1000 < 800, 8, 4),
        "RPM": 11000.0, "DRS": 0, "X": r * np.cos(theta), "Y": r * np.sin(theta),
    })


def make_laps(drivers=("AAA", "BBB", "CCC"), n_laps: int = 20) -> pd.DataFrame:
    rows = []
    for k, drv in enumerate(drivers):
        for lap in range(1, n_laps + 1):
            stint = 1 if lap <= 10 else 2
            life = lap if stint == 1 else lap - 10
            t = 90.0 + k * 0.3 + 0.1 * life  # exactly 0.1 s/lap deg
            pit_in = pd.Timedelta(seconds=1) if lap == 10 else pd.NaT
            pit_out = pd.Timedelta(seconds=1) if lap == 11 else pd.NaT
            rows.append(dict(
                Driver=drv, LapNumber=lap, LapTime=pd.Timedelta(seconds=t), Stint=stint,
                Compound="MEDIUM" if stint == 1 else "HARD", TyreLife=float(life),
                PitInTime=pit_in, PitOutTime=pit_out, Deleted=False,
                TrackStatus="4" if lap == 15 else "1", Position=float(k + 1),
                Sector1Time=pd.Timedelta(seconds=t / 3), Sector2Time=pd.Timedelta(seconds=t / 3),
                Sector3Time=pd.Timedelta(seconds=t / 3), SpeedST=320.0 - k, IsPersonalBest=False))
    return pd.DataFrame(rows)


def make_session(session: str = "Race", with_tel: bool = True) -> data.SessionData:
    laps = make_laps()
    results = pd.DataFrame({
        "Position": [1.0, 2.0, 3.0], "Abbreviation": ["AAA", "BBB", "CCC"],
        "TeamName": ["Team A", "Team B", "Team C"], "GridPosition": [2.0, 1.0, 3.0],
        "Status": ["Finished"] * 3, "Points": [25.0, 18.0, 15.0],
        "Q1": [pd.NaT] * 3, "Q2": [pd.NaT] * 3, "Q3": [pd.NaT] * 3})
    weather = pd.DataFrame({"AirTemp": [25.0, 26.0], "TrackTemp": [40.0, 42.0], "Rainfall": [False, False]})
    corners = pd.DataFrame({"Number": [1, 2], "Letter": ["", "A"], "Distance": [800.0, 1800.0],
                            "X": [0.0, 0.0], "Y": [0.0, 0.0], "Angle": [0.0, 0.0]})
    speeds = {"AAA": 50.0, "BBB": 49.0, "CCC": 48.0}

    def tel(drv):
        if not with_tel or drv not in speeds:
            return None
        car = make_car(speeds[drv])
        lap = laps[laps["Driver"] == drv].sort_values("LapTime").iloc[0]
        return data.LapTelemetry(lap=lap, car=car, pos=car)

    return data.SessionData(
        year=2024, event="Test Grand Prix", session=session, laps=laps, results=results,
        weather=weather, messages=pd.DataFrame({"Lap": [15], "Category": ["SafetyCar"],
                                                "Flag": [None], "Message": ["VSC DEPLOYED"]}),
        corners=corners, rotation=90.0, telemetry_fn=tel)


@pytest.fixture
def session_data():
    return make_session()
