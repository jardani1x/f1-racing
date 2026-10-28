"""Teal Line Telemetry: an unofficial F1 session dashboard built on FastF1.

Run:  streamlit run f1_dashboard/app.py
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from f1dash import analysis as an
from f1dash import charts, data
from f1dash import theme as th

st.set_page_config(page_title="Teal Line Telemetry", page_icon="◆", layout="wide")
st.markdown(th.CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- caching
@st.cache_data(ttl=3600, show_spinner=False)
def events(year: int) -> pd.DataFrame:
    return data.completed_events(year)


@st.cache_resource(max_entries=4, show_spinner=False)
def session(year: int, rnd: int, name: str) -> data.SessionData:
    return data.load_session(year, rnd, name)


@st.cache_resource(max_entries=32, show_spinner=False)
def telemetry(year: int, rnd: int, name: str, drv: str):
    return session(year, rnd, name).telemetry_fn(drv)


# ---------------------------------------------------------------- sidebar
st.sidebar.markdown("### Session")
this_year = dt.date.today().year
seasons = list(range(this_year, data.FIRST_YEAR - 1, -1))
try:  # Between seasons, open on last year instead of an empty calendar.
    if events(this_year).empty:
        seasons.remove(this_year)
except data.DataUnavailable:
    pass  # the error is reported below for whichever season is picked
year = st.sidebar.selectbox("Season", seasons)

try:
    ev = events(year)
except data.DataUnavailable as exc:
    st.error(str(exc))
    st.stop()
if ev.empty:
    st.info(f"No {year} rounds have finished yet. Pick an earlier season.")
    st.stop()

labels = [f"R{r.RoundNumber:02d} · {r.EventName}" for r in ev.itertuples()]
idx = st.sidebar.selectbox("Event", range(len(ev)), format_func=lambda i: labels[i])
row = ev.iloc[idx]
sessions = list(row["Sessions"])
default_ses = sessions.index("Race") if "Race" in sessions else len(sessions) - 1
ses_name = st.sidebar.selectbox("Session", sessions, index=default_ses)

with st.status(f"Loading {year} {row['EventName']} · {ses_name}", expanded=False) as status:
    status.write("First load of a session downloads timing and telemetry "
                 "(often 30–90 s). It's cached on disk afterwards.")
    try:
        sd = session(year, int(row["RoundNumber"]), ses_name)
        status.update(label=f"{sd.event} · {sd.session}", state="complete")
    except data.DataUnavailable as exc:
        status.update(label="Session unavailable", state="error")
        st.error(str(exc))
        st.stop()

drivers = sd.drivers()
if len(drivers) < 2:
    st.warning("This session has fewer than two drivers with data.")
    st.stop()
st.sidebar.markdown("### Head-to-head")
drv_a = st.sidebar.selectbox("Driver A", drivers, index=0)
drv_b = st.sidebar.selectbox("Driver B", [d for d in drivers if d != drv_a], index=0)
key = (year, int(row["RoundNumber"]), ses_name)

# ---------------------------------------------------------------- header
st.markdown(f"# {sd.event} <span class='tl-muted'>· {sd.year} · {sd.session}</span>",
            unsafe_allow_html=True)
st.markdown("<div class='tl-rule'></div>", unsafe_allow_html=True)

laps = sd.laps
valid = laps[laps["Deleted"].fillna(False) != True] if "Deleted" in laps else laps  # noqa: E712
fastest = valid.loc[valid["LapTime"].idxmin()] if valid["LapTime"].notna().any() else None
k = st.columns(5)
k[0].metric("Winner" if sd.is_race else "P1", drivers[0])
k[1].metric("Fastest lap", an.fmt_laptime(fastest["LapTime"]) if fastest is not None else "—",
            fastest["Driver"] if fastest is not None else None, delta_color="off", delta_arrow="off")
k[2].metric("Laps", int(laps["LapNumber"].max()))
if not sd.weather.empty:
    w = sd.weather
    k[3].metric("Track temp", f"{w['TrackTemp'].mean():.1f} °C",
                f"{w['TrackTemp'].min():.0f}–{w['TrackTemp'].max():.0f}", delta_color="off", delta_arrow="off")
    k[4].metric("Air · rain", f"{w['AirTemp'].mean():.1f} °C",
                "rain seen" if w["Rainfall"].astype(bool).any() else "dry", delta_color="off", delta_arrow="off")
else:
    k[3].metric("Track temp", "—")
    k[4].metric("Air · rain", "—")

tab_over, tab_h2h, tab_strat, tab_sec = st.tabs(["Overview", "Head-to-head", "Strategy", "Sectors"])

# ---------------------------------------------------------------- overview
with tab_over:
    res = sd.results
    if not res.empty:
        if sd.is_race:
            cols = {"Position": "Pos", "Abbreviation": "Driver", "TeamName": "Team",
                    "GridPosition": "Grid", "Status": "Status", "Points": "Pts"}
            table = res[[c for c in cols if c in res]].rename(columns=cols)
        else:
            cols = {"Position": "Pos", "Abbreviation": "Driver", "TeamName": "Team",
                    "Q1": "Q1", "Q2": "Q2", "Q3": "Q3"}
            table = res[[c for c in cols if c in res]].rename(columns=cols)
            for q in ("Q1", "Q2", "Q3"):
                if q in table:
                    table[q] = table[q].map(an.fmt_laptime)
            if table[["Q1", "Q2", "Q3"]].eq("—").all().all() if {"Q1", "Q2", "Q3"} <= set(table) else False:
                table = table.drop(columns=["Q1", "Q2", "Q3"])
                best = laps.groupby("Driver")["LapTime"].min().map(an.fmt_laptime)
                table["Best"] = table["Driver"].map(best)
        st.dataframe(table, hide_index=True, width="stretch")
    if sd.is_race:
        st.markdown("### Position changes")
        st.plotly_chart(charts.positions_chart(an.positions(laps), drv_a, drv_b, drivers),
                        width="stretch", theme=None)
        st.caption(f"Lower is better. {drv_a} and {drv_b} are highlighted; the rest of the field is faded.")
    if not sd.messages.empty:
        with st.expander(f"Race control · {len(sd.messages)} messages"):
            cols = [c for c in ("Lap", "Category", "Flag", "Message") if c in sd.messages]
            st.dataframe(sd.messages[cols], hide_index=True, width="stretch")

# ---------------------------------------------------------------- head-to-head
with tab_h2h:
    ta, tb = telemetry(*key, drv_a), telemetry(*key, drv_b)
    if ta is None or tb is None:
        missing = ", ".join(d for d, t in ((drv_a, ta), (drv_b, tb)) if t is None)
        st.info(f"No timed lap with telemetry for {missing} in this session.")
    else:
        gap = (tb.lap["LapTime"] - ta.lap["LapTime"]).total_seconds()
        st.markdown(
            f"<div class='tl-matchup'><span class='tl-a'>{drv_a}</span> "
            f"{an.fmt_laptime(ta.lap['LapTime'])} <span class='tl-muted'>vs</span> "
            f"<span class='tl-b'>{drv_b}</span> {an.fmt_laptime(tb.lap['LapTime'])} "
            f"<span class='tl-muted'>· Δ {gap:+.3f}s · fastest laps</span></div>",
            unsafe_allow_html=True)
        left, right = st.columns([3, 2])
        with left:
            st.plotly_chart(charts.telemetry(ta.car, tb.car, drv_a, drv_b, sd.corners), width="stretch", theme=None)
            st.caption(f"Δ rises where {drv_a} gains time and falls where {drv_b} gains. "
                       "Dotted lines mark corners. Brake is on/off only; FastF1 has no pressure channel.")
        with right:
            ms = an.minisector_winners(ta.pos, tb.pos, drv_a, drv_b) \
                if {"X", "Y"} <= set(ta.pos) and {"X", "Y"} <= set(tb.pos) else pd.DataFrame()
            st.plotly_chart(charts.minisector_map(ms, drv_a, drv_b, sd.rotation), width="stretch", theme=None)
            if not ms.empty:
                wins = ms.drop_duplicates("MiniSector")["Winner"].value_counts()
                st.caption(f"25 equal-length mini-sectors, coloured by who covered each one quicker. "
                           f"{drv_a} {wins.get(drv_a, 0)} · {drv_b} {wins.get(drv_b, 0)}.")
            c1, c2 = st.columns(2)
            c1.metric(f"{drv_a} top speed", f"{ta.car['Speed'].max():.0f} km/h")
            c2.metric(f"{drv_b} top speed", f"{tb.car['Speed'].max():.0f} km/h",
                      f"{tb.car['Speed'].max() - ta.car['Speed'].max():+.0f}")

# ---------------------------------------------------------------- strategy
with tab_strat:
    st.markdown("### Tyre stints")
    st.plotly_chart(charts.stint_chart(an.stints(laps), drivers), width="stretch", theme=None)
    st.caption("Each bar is one stint. S/M/H/I/W letters repeat the compound colour.")
    if sd.is_race:
        clean = an.clean_laps(laps)
        st.markdown("### Race pace")
        st.plotly_chart(charts.pace_chart(clean, [drv_a, drv_b]), width="stretch", theme=None)
        st.caption(f"Green-flag laps only: no pit in/out laps, no SC/VSC, within 107% of the fastest. "
                   f"Marker colour is the compound; ● {drv_a}, ◇ {drv_b}.")
        deg = an.degradation(laps)
        deg = deg[deg["Driver"].isin([drv_a, drv_b])]
        if not deg.empty:
            st.markdown("### Degradation per stint")
            st.dataframe(deg.rename(columns={"SlopeSPerLap": "s / lap of tyre age"})
                         .style.format({"s / lap of tyre age": "{:+.3f}"}), hide_index=True)
            st.caption("Linear fit of lap time against tyre age. Fuel burn-off makes the car "
                       "about 0.03–0.06 s/lap faster as the race goes on, so true tyre wear is "
                       "higher than shown.")
    else:
        st.info("Race pace and degradation apply to races and sprints. Pick one in the sidebar.")

# ---------------------------------------------------------------- sectors
with tab_sec:
    sec = an.sector_table(laps)
    if sec.empty:
        st.info("No sector times recorded for this session.")
    else:
        traps = laps.groupby("Driver")["SpeedST"].max() if "SpeedST" in laps else pd.Series(dtype=float)
        view = sec.assign(**{c: sec[c].map(an.fmt_laptime) for c in ["BestLap", "S1", "S2", "S3", "Ideal"]})
        view["Gap"] = sec["Gap"].map(lambda g: "—" if pd.isna(g) else f"{g:.3f}")
        view["Speed trap"] = view["Driver"].map(traps).map(lambda v: "—" if pd.isna(v) else f"{v:.0f}")
        st.dataframe(view.rename(columns={"BestLap": "Best lap", "Gap": "Left on table (s)",
                                          "Speed trap": "Speed trap (km/h)"}),
                     hide_index=True, width="stretch")
        st.caption("Ideal = the driver's best S1 + best S2 + best S3, which may come from different laps. "
                   "A big gap means the pace was there but never in one lap.")
        st.plotly_chart(charts.ideal_gap_chart(sec, drv_a, drv_b), width="stretch", theme=None)

st.markdown("<div class='tl-foot'>Unofficial fan project built on the open-source FastF1 library. "
            "Not associated with Formula 1, the FIA or any F1 team. F1 and team names are trademarks "
            "of their respective owners.</div>", unsafe_allow_html=True)
