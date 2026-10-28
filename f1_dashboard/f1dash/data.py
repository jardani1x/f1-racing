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
        """Abbreviations in classification order, falling back to lap data."""
        if not self.results.empty and "Abbreviation" in self.results:
            order = self.results.sort_values("Position", na_position="last")["Abbreviation"]
            return [d for d in order.dropna().tolist() if d]
        return sorted(self.laps["Driver"].dropna().unique().tolist()) if not self.laps.empty else []


def _fastf1():
    import fastf1
    os.makedirs(CACHE_DIR, exist_ok=True)
    fastf1.Cache.enable_cache(CACHE_DIR)
    fastf1.set_log_level("ERROR")
    return fastf1


def completed_events(year: int) -> pd.DataFrame:
    """Rounds of `year` whose last session has started, newest first."""
    f1 = _fastf1()
    try:
        sched = f1.get_event_schedule(year, include_testing=False)
    except Exception as exc:  # network, parsing, backend outage
        raise DataUnavailable(f"Couldn't fetch the {year} calendar ({type(exc).__name__}).") from exc
    now = pd.Timestamp(dt.datetime.now(dt.timezone.utc)).tz_localize(None)
    date_col = "Session5DateUtc" if "Session5DateUtc" in sched else "EventDate"
    done = sched[pd.to_datetime(sched[date_col]) <= now]
    sessions = done.apply(
        lambda r: [r[f"Session{i}"] for i in range(1, 6)
                   if isinstance(r.get(f"Session{i}"), str) and r[f"Session{i}"]], axis=1)
    out = pd.DataFrame({"RoundNumber": done["RoundNumber"], "EventName": done["EventName"],
                        "Sessions": sessions})
    return out.sort_values("RoundNumber", ascending=False).reset_index(drop=True)


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
        try:
            car = pd.DataFrame(lap.get_car_data().add_distance())
            pos = pd.DataFrame(lap.get_telemetry())
        except Exception:
            log.info("telemetry unavailable for %s", drv, exc_info=True)
            return None
        if car.empty:
            return None
        return LapTelemetry(lap=pd.Series(lap), car=car, pos=pos)

    return SessionData(
        year=year, event=str(ses.event["EventName"]), session=ses.name, laps=laps,
        results=_safe("results"), weather=_safe("weather_data"),
        messages=_safe("race_control_messages"), corners=corners, rotation=rotation,
        telemetry_fn=telemetry,
    )
