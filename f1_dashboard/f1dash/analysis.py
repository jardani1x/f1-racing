"""Pure transforms from FastF1 frames to chart-ready frames.

Nothing here touches the network or Streamlit, so every function is unit-testable
with small hand-built DataFrames that follow FastF1's column schema.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COMPOUND_ORDER = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]


def fmt_laptime(value) -> str:
    """Timedelta or seconds -> 'm:ss.000'. Missing values -> an em dash."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return "—"
    secs = value.total_seconds() if isinstance(value, pd.Timedelta) else float(value)
    sign = "-" if secs < 0 else ""
    secs = round(abs(secs), 3)  # round before divmod so 119.9999 -> 2:00.000, not 1:60.000
    minutes, rem = divmod(secs, 60)
    return f"{sign}{int(minutes)}:{rem:06.3f}" if minutes else f"{sign}{rem:.3f}"


def _seconds(series: pd.Series) -> np.ndarray:
    if pd.api.types.is_timedelta64_dtype(series):
        return series.dt.total_seconds().to_numpy(dtype=float)
    return series.to_numpy(dtype=float)


def clean_laps(laps: pd.DataFrame, threshold: float = 1.07,
               green_only: bool = True) -> pd.DataFrame:
    """Representative racing laps: timed, not deleted, no pit in/out, green flag
    only (no SC/VSC/yellow), within `threshold` x the fastest remaining lap
    (FastF1's pick_quicklaps rule)."""
    if laps.empty:
        return laps.copy()
    out = laps[laps["LapTime"].notna()]
    out = out[out["PitInTime"].isna() & out["PitOutTime"].isna()]
    if "Deleted" in out:
        out = out[out["Deleted"].fillna(False) != True]  # noqa: E712
    # Skip the filter when the track-status feed didn't load (all NaN).
    if green_only and "TrackStatus" in out and out["TrackStatus"].notna().any():
        # TrackStatus concatenates every status seen during the lap, e.g. "14".
        out = out[out["TrackStatus"].astype(str) == "1"]
    if out.empty:
        return out.copy()
    limit = out["LapTime"].min() * threshold
    out = out[out["LapTime"] <= limit].copy()
    out["LapTimeS"] = _seconds(out["LapTime"])
    return out


def lap_delta(ref: pd.DataFrame, cmp: pd.DataFrame, step_m: float = 5.0) -> pd.DataFrame:
    """Gap of `cmp` to `ref` along the lap, sampled every `step_m` metres.

    Both inputs need 'Distance' (m) and 'Time' (lap-relative timedelta).
    Delta > 0 means cmp reached that point later, i.e. ref is ahead.
    Replaces fastf1.utils.delta_time, which no longer exists in FastF1 3.9.
    """
    if ref.empty or cmp.empty:
        return pd.DataFrame(columns=["Distance", "Delta"])
    d_ref, t_ref = ref["Distance"].to_numpy(float), _seconds(ref["Time"])
    d_cmp, t_cmp = cmp["Distance"].to_numpy(float), _seconds(cmp["Time"])
    end = min(d_ref.max(), d_cmp.max())
    grid = np.arange(0.0, end, step_m)
    # Distance can stall at standstill; interp needs monotonic x, so keep first hits.
    d_ref, i_ref = np.unique(d_ref, return_index=True)
    d_cmp, i_cmp = np.unique(d_cmp, return_index=True)
    delta = np.interp(grid, d_cmp, t_cmp[i_cmp]) - np.interp(grid, d_ref, t_ref[i_ref])
    return pd.DataFrame({"Distance": grid, "Delta": delta})


def minisector_winners(tel_a: pd.DataFrame, tel_b: pd.DataFrame,
                       name_a: str, name_b: str, n: int = 25) -> pd.DataFrame:
    """Split the lap into `n` equal-distance mini-sectors and mark who covered
    each one in less time. Returns tel_a's X/Y points tagged with the winner.

    Time-in-segment beats mean speed: mean speed over-weights slow corners where
    more samples land.
    """
    if tel_a.empty or tel_b.empty:
        return pd.DataFrame(columns=["X", "Y", "Distance", "MiniSector", "Winner"])
    end = min(tel_a["Distance"].max(), tel_b["Distance"].max())
    edges = np.linspace(0.0, end, n + 1)

    def seg_times(tel):
        d, i = np.unique(tel["Distance"].to_numpy(float), return_index=True)
        return np.diff(np.interp(edges, d, _seconds(tel["Time"])[i]))

    a_faster = seg_times(tel_a) <= seg_times(tel_b)
    out = tel_a[["X", "Y", "Distance"]].copy()
    out = out[out["Distance"] <= end]
    out["MiniSector"] = np.clip(np.searchsorted(edges, out["Distance"], side="right") - 1, 0, n - 1)
    out["Winner"] = np.where(a_faster[out["MiniSector"]], name_a, name_b)
    return out.reset_index(drop=True)


def rotate_xy(x, y, degrees: float):
    """Rotate track coordinates so the map matches the official orientation."""
    rad = np.deg2rad(degrees)
    c, s = np.cos(rad), np.sin(rad)
    x, y = np.asarray(x, float), np.asarray(y, float)
    return x * c - y * s, x * s + y * c


def stints(laps: pd.DataFrame) -> pd.DataFrame:
    """One row per (driver, stint): compound, first/last lap and length."""
    cols = ["Driver", "Stint", "Compound", "StartLap", "EndLap", "Laps"]
    if laps.empty:
        return pd.DataFrame(columns=cols)
    df = laps.dropna(subset=["Stint"]).copy()
    df["Compound"] = df["Compound"].fillna("UNKNOWN").replace("", "UNKNOWN")
    g = df.groupby(["Driver", "Stint"], sort=False).agg(
        Compound=("Compound", lambda s: s.mode().iat[0]),
        StartLap=("LapNumber", "min"),
        EndLap=("LapNumber", "max"),
    ).reset_index()
    g["Laps"] = g["EndLap"] - g["StartLap"] + 1
    return g[cols]


def degradation(laps: pd.DataFrame, min_laps: int = 5) -> pd.DataFrame:
    """Least-squares slope of lap time vs tyre age for each stint (s per lap).

    Uses clean laps only. The slope mixes tyre wear with fuel burn-off (which
    makes the car faster each lap), so it understates true tyre wear.
    """
    cols = ["Driver", "Stint", "Compound", "Laps", "SlopeSPerLap"]
    clean = clean_laps(laps)
    if clean.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for (drv, stint), g in clean.dropna(subset=["TyreLife"]).groupby(["Driver", "Stint"]):
        if len(g) < min_laps or g["TyreLife"].nunique() < 2:
            continue
        slope = np.polyfit(g["TyreLife"].to_numpy(float), g["LapTimeS"].to_numpy(float), 1)[0]
        rows.append((drv, int(stint), g["Compound"].mode().iat[0], len(g), float(slope)))
    return pd.DataFrame(rows, columns=cols)


def positions(laps: pd.DataFrame) -> pd.DataFrame:
    """Driver, LapNumber, Position for laps where the position is known."""
    if laps.empty:
        return pd.DataFrame(columns=["Driver", "LapNumber", "Position"])
    return laps.dropna(subset=["Position"])[["Driver", "LapNumber", "Position"]] \
        .sort_values(["Driver", "LapNumber"]).reset_index(drop=True)


def sector_table(laps: pd.DataFrame, drivers: list[str] | None = None) -> pd.DataFrame:
    """Per driver: best lap, best of each sector, and the ideal lap (sum of the
    three best sectors). Sorted by best lap. Times in seconds."""
    cols = ["Driver", "BestLap", "S1", "S2", "S3", "Ideal", "Gap"]
    if laps.empty:
        return pd.DataFrame(columns=cols)
    df = laps if drivers is None else laps[laps["Driver"].isin(drivers)]
    if "Deleted" in df:
        df = df[df["Deleted"].fillna(False) != True]  # noqa: E712
    out = df.groupby("Driver").agg(
        BestLap=("LapTime", "min"), S1=("Sector1Time", "min"),
        S2=("Sector2Time", "min"), S3=("Sector3Time", "min"),
    )
    for c in ["BestLap", "S1", "S2", "S3"]:
        out[c] = _seconds(out[c])
    out["Ideal"] = out[["S1", "S2", "S3"]].sum(axis=1, min_count=3)
    # Timing is millisecond-resolution; rounding removes float residue from the sum.
    out["Gap"] = (out["BestLap"] - out["Ideal"]).round(3)
    return out.dropna(subset=["BestLap"]).sort_values("BestLap").reset_index()[cols]


def corners_with_distance(corners: pd.DataFrame | None) -> pd.DataFrame:
    """Corner markers that have a usable Distance (needs telemetry loaded)."""
    if corners is None or corners.empty or "Distance" not in corners:
        return pd.DataFrame(columns=["Label", "Distance"])
    c = corners.dropna(subset=["Distance"]).copy()
    c["Label"] = c["Number"].astype(int).astype(str) + c.get("Letter", "").fillna("").astype(str)
    return c[["Label", "Distance"]].reset_index(drop=True)
