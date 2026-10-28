import numpy as np
import pandas as pd
import pytest

from f1dash import analysis as an
from conftest import TRACK_M, make_car, make_laps


@pytest.mark.parametrize("value,expected", [
    (83.456, "1:23.456"), (59.9, "59.900"), (60.0, "1:00.000"),
    (pd.Timedelta(seconds=92.1), "1:32.100"), (pd.NaT, "—"), (None, "—"), (float("nan"), "—"),
    (-0.25, "-0.250"),
])
def test_fmt_laptime(value, expected):
    assert an.fmt_laptime(value) == expected


def test_lap_delta_identical_is_zero():
    car = make_car(50.0)
    assert np.allclose(an.lap_delta(car, car)["Delta"], 0.0)


def test_lap_delta_matches_hand_calc():
    # 5000 m at 50 m/s = 100 s; at 40 m/s = 125 s. At distance d: d/40 - d/50 = d/200.
    d = an.lap_delta(make_car(50.0), make_car(40.0))
    assert d["Distance"].iloc[0] == 0.0
    i = (d["Distance"] - 2000).abs().idxmin()
    assert d.loc[i, "Delta"] == pytest.approx(d.loc[i, "Distance"] / 200, abs=1e-6)
    assert (d["Delta"].diff().dropna() >= 0).all()  # ref always pulling away


def test_lap_delta_handles_stalled_distance_and_empty():
    car = make_car(50.0)
    stalled = pd.concat([car.iloc[:1], car]).reset_index(drop=True)  # duplicate distance 0
    assert np.isfinite(an.lap_delta(car, stalled)["Delta"]).all()
    assert an.lap_delta(car, car.iloc[0:0]).empty


def test_minisectors_split_by_who_is_faster():
    n = 500
    dist = np.linspace(0, TRACK_M, n)
    a = make_car(np.where(dist < TRACK_M / 2, 60.0, 40.0))
    b = make_car(50.0)
    ms = an.minisector_winners(a, b, "A", "B", n=10)
    per = ms.drop_duplicates("MiniSector").set_index("MiniSector")["Winner"]
    assert (per.loc[0:4] == "A").all() and (per.loc[5:9] == "B").all()
    assert set(ms["MiniSector"]) == set(range(10))


def test_rotate_xy_quarter_turn():
    x, y = an.rotate_xy([1.0], [0.0], 90)
    assert x[0] == pytest.approx(0.0, abs=1e-12) and y[0] == pytest.approx(1.0)


def test_clean_laps_drops_pit_sc_deleted_and_slow():
    laps = make_laps(drivers=("AAA",))
    laps.loc[laps["LapNumber"] == 3, "Deleted"] = True
    laps.loc[laps["LapNumber"] == 4, "LapTime"] = pd.NaT
    laps.loc[laps["LapNumber"] == 5, "LapTime"] = pd.Timedelta(seconds=200)  # > 107%
    kept = set(an.clean_laps(laps)["LapNumber"])
    assert kept == set(range(1, 21)) - {3, 4, 5, 10, 11, 15}
    assert 15 in set(an.clean_laps(laps, green_only=False)["LapNumber"])


def test_stints():
    st = an.stints(make_laps(drivers=("AAA",)))
    assert st[["Stint", "Compound", "StartLap", "EndLap", "Laps"]].values.tolist() == [
        [1, "MEDIUM", 1, 10, 10], [2, "HARD", 11, 20, 10]]


def test_degradation_recovers_known_slope():
    deg = an.degradation(make_laps(drivers=("AAA",)))
    assert len(deg) == 2
    assert deg["SlopeSPerLap"].to_numpy() == pytest.approx([0.1, 0.1], abs=1e-9)


def test_sector_table_ideal_and_order():
    laps = make_laps()
    # Give AAA a best S1 on a different lap from its best lap: ideal < best.
    laps.loc[(laps["Driver"] == "AAA") & (laps["LapNumber"] == 20), "Sector1Time"] = pd.Timedelta(seconds=29)
    sec = an.sector_table(laps)
    assert sec["Driver"].tolist() == ["AAA", "BBB", "CCC"]
    a = sec.iloc[0]
    assert a["BestLap"] == pytest.approx(90.1)
    assert a["Ideal"] == pytest.approx(29 + 2 * 90.1 / 3)
    assert a["Gap"] == pytest.approx(90.1 - a["Ideal"], abs=5e-4)  # rounded to ms
    assert sec.iloc[1]["Gap"] == pytest.approx(0.0, abs=1e-9)


def test_corners_with_distance():
    c = pd.DataFrame({"Number": [1, 2], "Letter": ["", "A"], "Distance": [10.0, np.nan]})
    assert an.corners_with_distance(c)["Label"].tolist() == ["1"]
    assert an.corners_with_distance(None).empty


def test_positions_drops_unknown():
    laps = make_laps(drivers=("AAA",))
    laps.loc[0, "Position"] = np.nan
    assert len(an.positions(laps)) == 19


def test_sector_gap_has_no_float_residue():
    sec = an.sector_table(make_laps())
    assert (sec["Gap"] == 0.0).all()  # thirds of 90.1 s sum back with ~1e-9 error
