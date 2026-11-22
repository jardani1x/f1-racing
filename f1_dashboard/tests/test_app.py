"""End-to-end render of the Streamlit app with the data layer patched out."""
import os

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from f1dash import charts, data
from f1dash import analysis as an
from conftest import ROOT, make_session

APP = os.path.join(ROOT, "app.py")
EVENTS = pd.DataFrame({"RoundNumber": [3], "EventName": ["Test Grand Prix"],
                       "Sessions": [["Practice 1", "Qualifying", "Race"]]})


@pytest.fixture(autouse=True)
def _clear_caches():
    st.cache_data.clear()
    st.cache_resource.clear()


def run(monkeypatch, loader, session_choice=None):
    monkeypatch.setattr(data, "completed_events", lambda year: EVENTS)
    monkeypatch.setattr(data, "load_session", loader)
    at = AppTest.from_file(APP, default_timeout=60).run()
    if session_choice:
        at.sidebar.selectbox[2].set_value(session_choice).run()
    return at


def test_race_renders_every_tab(monkeypatch):
    at = run(monkeypatch, lambda y, r, s: make_session(s))
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["Overview", "Head-to-head", "Strategy", "Sectors"]
    m = {x.label: x.value for x in at.metric}
    assert m["Winner"] == "AAA" and m["Fastest lap"] == "1:30.100" and m["Laps"] == "20"
    assert m["Track temp"] == "41.0 °C"
    assert m["AAA top speed"] == "180 km/h"
    assert len(at.dataframe) >= 3  # results, degradation, sectors (+ race control)
    assert not at.error


def test_driver_b_excludes_driver_a(monkeypatch):
    at = run(monkeypatch, lambda y, r, s: make_session(s))
    at.sidebar.selectbox[3].set_value("BBB").run()
    assert "BBB" not in at.sidebar.selectbox[4].options
    assert not at.exception


def test_qualifying_hides_race_only_views(monkeypatch):
    at = run(monkeypatch, lambda y, r, s: make_session(s), session_choice="Qualifying")
    assert not at.exception, at.exception
    assert any("apply to races" in i.value for i in at.info)
    assert "P1" in [x.label for x in at.metric]


def test_missing_telemetry_is_an_info_not_a_crash(monkeypatch):
    at = run(monkeypatch, lambda y, r, s: make_session(s, with_tel=False))
    assert not at.exception
    assert any("No timed lap with telemetry" in i.value for i in at.info)


def test_load_failure_shows_error(monkeypatch):
    def boom(y, r, s):
        raise data.DataUnavailable("Timing data isn't available")
    at = run(monkeypatch, boom)
    assert not at.exception
    assert at.error and "Timing data" in at.error[0].value


def test_no_completed_events(monkeypatch):
    monkeypatch.setattr(data, "completed_events", lambda y: EVENTS.iloc[0:0])
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception and "No" in at.info[0].value


def test_charts_build_on_empty_inputs():
    empty = pd.DataFrame()
    for fig in (charts.minisector_map(pd.DataFrame(columns=["X", "Y", "Distance", "MiniSector", "Winner"]), "A", "B"),
                charts.stint_chart(an.stints(empty), []),
                charts.positions_chart(an.positions(empty), "A", "B", []),
                charts.ideal_gap_chart(an.sector_table(empty), "A", "B")):
        assert fig.layout.annotations  # shows a message rather than raising


def test_telemetry_chart_corner_labels_and_clip():
    s = make_session()
    a, b = s.telemetry_fn("AAA"), s.telemetry_fn("BBB")
    fig = charts.telemetry(a.car, b.car, "AAA", "BBB", s.corners)
    assert [x.text for x in fig.layout.annotations if x.text in ("1", "2A")] == ["1", "2A"]
    throttle = [t for t in fig.data if t.yaxis == "y3"]
    assert max(max(t.y) for t in throttle) <= 100


def test_load_session_wraps_backend_errors(monkeypatch):
    class Fake:
        @staticmethod
        def get_session(*a):
            raise ConnectionError("403")
    monkeypatch.setattr(data, "_fastf1", lambda: Fake)
    with pytest.raises(data.DataUnavailable, match="unreachable"):
        data.load_session(2024, 1, "Race")


def test_deleted_lap_is_not_the_fastest_lap(monkeypatch):
    def loader(y, r, s):
        sd = make_session(s)
        i = sd.laps.index[(sd.laps["Driver"] == "CCC") & (sd.laps["LapNumber"] == 2)][0]
        sd.laps.loc[i, ["LapTime", "Deleted"]] = [pd.Timedelta(seconds=80), True]
        return sd
    at = run(monkeypatch, loader)
    m = {x.label: x.value for x in at.metric}
    assert m["Fastest lap"] == "1:30.100"


def test_season_falls_back_when_current_year_has_no_rounds(monkeypatch):
    import datetime as dt
    this_year = dt.date.today().year
    monkeypatch.setattr(data, "completed_events",
                        lambda y: EVENTS.iloc[0:0] if y == this_year else EVENTS)
    monkeypatch.setattr(data, "load_session", lambda y, r, s: make_session(s))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.sidebar.selectbox[0].value == this_year - 1
    assert not at.exception and not at.info


def test_rain_ignores_missing_samples_and_best_excludes_deleted(monkeypatch):
    def loader(y, r, s):
        sd = make_session(s)
        sd.weather["Rainfall"] = pd.Series([False, None], dtype=object)
        i = sd.laps.index[(sd.laps["Driver"] == "CCC") & (sd.laps["LapNumber"] == 2)][0]
        sd.laps.loc[i, ["LapTime", "Deleted"]] = [pd.Timedelta(seconds=80), True]
        return sd
    at = run(monkeypatch, loader, session_choice="Qualifying")
    assert not at.exception, at.exception
    m = {x.label: x for x in at.metric}
    assert m["Air · rain"].delta == "dry"
    best = at.dataframe[0].value.set_index("Driver")["Best"]
    assert best["CCC"] == "1:30.700"  # not the deleted 1:20.000
