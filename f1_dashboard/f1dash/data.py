"""FastF1 access. The only module that imports fastf1 or talks to the network.

The UI receives a `SessionData` of plain DataFrames plus a telemetry callback,
so tests can build one from fixtures without FastF1 or a connection.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

log = logging.getLogger(__name__)

CACHE_DIR = os.environ.get("F1_CACHE_DIR", os.path.expanduser("~/.cache/teal-line-fastf1"))
FIRST_YEAR = 2018  # FastF1 telemetry coverage starts here
RACE_TYPES = {"Race", "Sprint"}


class DataUnavailable(RuntimeError):
    """Raised with a user-facing message when a session can't be loaded."""


@dataclass
class LapTelemetry:
    lap: pd.Series          # the lap row (LapTime, Compound, ...)
    car: pd.DataFrame       # Distance, Time, Speed, Throttle, Brake, nGear, RPM, DRS
    pos: pd.DataFrame       # Distance, Time, X, Y (merged; for maps)


@dataclass
class SessionData:
    year: int
    event: str
    session: str
    laps: pd.DataFrame
    results: pd.DataFrame
    weather: pd.DataFrame = field(default_factory=pd.DataFrame)
    messages: pd.DataFrame = field(default_factory=pd.DataFrame)
    corners: Optional[pd.DataFrame] = None
    rotation: float = 0.0
    telemetry_fn: Callable[[str], Optional[LapTelemetry]] = lambda drv: None

    @property
    def is_race(self) -> bool:
        return self.session in RACE_TYPES

    def drivers(self) -> list[str]:
        """Abbreviations in classification order. Practice has no classification
        (Position is all NaN), so order by best valid lap time instead."""
        res = self.results
        if not res.empty and "Abbreviation" in res and res.get("Position", pd.Series(dtype=float)).notna().any():
            order = res.sort_values("Position", na_position="last")["Abbreviation"]
            return [d for d in order.dropna().tolist() if d]
        if self.laps.empty:
            return []
        laps = self.laps
        if "Deleted" in laps:
            laps = laps[laps["Deleted"].fillna(False) != True]  # noqa: E712
        best = laps.groupby("Driver")["LapTime"].min().sort_values(na_position="last")
        return [d for d in best.index.tolist() if d] + sorted(
            set(self.laps["Driver"].dropna()) - set(best.index))


def _fastf1():
    import fastf1
    os.makedirs(CACHE_DIR, exist_ok=True)
    fastf1.Cache.enable_cache(CACHE_DIR)
    fastf1.set_log_level("ERROR")
    return fastf1


def started_sessions(sched: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Rounds with at least one session started by `now` (naive UTC), each listing
    only its started sessions, newest round first. Includes the weekend in progress."""
    rows = []
    for _, r in sched.iterrows():
        names = []
        for i in range(1, 6):
            name, start = r.get(f"Session{i}"), pd.to_datetime(r.get(f"Session{i}DateUtc"))
            if isinstance(name, str) and name and pd.notna(start) and start <= now:
                names.append(name)
        if names:
            rows.append((r["RoundNumber"], r["EventName"], names))
    out = pd.DataFrame(rows, columns=["RoundNumber", "EventName", "Sessions"])
    return out.sort_values("RoundNumber", ascending=False).reset_index(drop=True)


def completed_events(year: int) -> pd.DataFrame:
    """Rounds of `year` with at least one started session, newest first."""
    f1 = _fastf1()
    try:
        sched = f1.get_event_schedule(year, include_testing=False)
    except Exception as exc:  # network, parsing, backend outage
        raise DataUnavailable(f"Couldn't fetch the {year} calendar ({type(exc).__name__}).") from exc
    return started_sessions(sched, pd.Timestamp(dt.datetime.now(dt.timezone.utc)).tz_localize(None))


def lap_telemetry(lap) -> Optional[LapTelemetry]:
    """Car channels for the overlay, plus X/Y for the map when available. Position
    data is optional: losing it must not hide the speed/throttle/brake traces."""
    try:
        car = pd.DataFrame(lap.get_car_data().add_distance())
    except Exception:
        log.info("car data unavailable", exc_info=True)
        return None
    if car.empty:
        return None
    try:
        pos = pd.DataFrame(lap.get_telemetry())
    except Exception:
        log.info("position data unavailable", exc_info=True)
        pos = pd.DataFrame()
    return LapTelemetry(lap=pd.Series(lap), car=car, pos=pos)


def load_session(year: int, event: str, session: str) -> SessionData:
    """Download (or read from disk cache) one session. Slow on first call."""
    f1 = _fastf1()
    try:
        ses = f1.get_session(year, event, session)
        ses.load(laps=True, telemetry=True, weather=True, messages=True)
        laps = pd.DataFrame(ses.laps)  # raises DataNotLoadedError if timing failed
    except Exception as exc:
        raise DataUnavailable(
            f"Timing data for {year} {event} {session} isn't available "
            f"({type(exc).__name__}). It may not be published yet, or the F1 "
            "live-timing service is unreachable.") from exc
    if laps.empty:
        raise DataUnavailable(f"No laps were recorded for {year} {event} {session}.")

    corners, rotation = None, 0.0
    try:
        info = ses.get_circuit_info()
        if info is not None:
            corners, rotation = info.corners, float(info.rotation)
    except Exception:  # MultiViewer API is optional decoration
        log.info("circuit info unavailable", exc_info=True)

    def _safe(attr):
        try:
            return pd.DataFrame(getattr(ses, attr))
        except Exception:
            return pd.DataFrame()

    def telemetry(drv: str) -> Optional[LapTelemetry]:
        drv_laps = ses.laps.pick_drivers(drv)
        # IsPersonalBest excludes deleted laps; fall back to the quickest
        # non-deleted lap when the flag is missing (some older sessions).
        lap = drv_laps.pick_fastest()
        if lap is None:
            lap = drv_laps.pick_not_deleted().pick_fastest(only_by_time=True)
        if lap is None or pd.isna(lap["LapTime"]):
            return None
        return lap_telemetry(lap)

    return SessionData(
        year=year, event=str(ses.event["EventName"]), session=ses.name, laps=laps,
        results=_safe("results"), weather=_safe("weather_data"),
        messages=_safe("race_control_messages"), corners=corners, rotation=rotation,
        telemetry_fn=telemetry,
    )
