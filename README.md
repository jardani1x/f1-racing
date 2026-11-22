# Teal Line Telemetry

An unofficial Formula 1 session dashboard built on [FastF1](https://github.com/theOehrly/Fast-F1),
Streamlit and Plotly. It uses a dark, single-teal-accent style inspired by the Mercedes-AMG
"Silver Arrows" look. It has no logos and no affiliation with any team.

## Run

```bash
git clone https://github.com/jardani1x/f1-racing.git
cd f1-racing/f1_dashboard
pip install -r requirements.txt
streamlit run app.py
```

Running `streamlit run f1_dashboard/app.py` from the repo root also works: the root
`.streamlit/config.toml` carries the same theme.

**Streamlit Community Cloud:** deploy `jardani1x/f1-racing`, branch `main`, main file
`f1_dashboard/app.py` (or the URL
`https://github.com/jardani1x/f1-racing/blob/main/f1_dashboard/app.py`).

The first load of a session downloads its timing and telemetry from the F1 live-timing service,
which often takes 30–90 s. After that, sessions load from the disk cache at `~/.cache/teal-line-fastf1`
(override it with `F1_CACHE_DIR`). FastF1 needs outbound HTTPS to `livetiming.formula1.com`,
`api.jolpi.ca` (the Ergast mirror) and `api.multiviewer.app` (corner positions; optional).

## What's in it

| Tab | Views | FastF1 source |
|---|---|---|
| Header | Winner/P1, fastest lap, laps, track and air temperature, rain | `results`, `laps`, `weather_data` |
| Overview | Classification; position changes (races); race-control log | `results`, `laps.Position`, `race_control_messages` |
| Head-to-head | Fastest-lap delta + speed/throttle/brake/gear vs distance with corner markers; mini-sector dominance map; top speed | `Lap.get_car_data().add_distance()`, `Lap.get_telemetry()` X/Y, `get_circuit_info()` |
| Strategy | Tyre stints; green-flag race pace by compound; degradation slope per stint | `laps` Stint/Compound/TyreLife/TrackStatus |
| Sectors | Best S1/S2/S3, ideal lap, time left on the table, speed trap | `laps` Sector*Time, SpeedST |

The views were chosen by a three-voter panel: race engineer, brand designer and fan/UX. The gear-shift
map was cut (1–2/5), and weather and race control were reduced to a strip and a log.

### Analysis notes

- **Delta** is computed by interpolating each lap's elapsed time onto a shared 5 m distance grid.
  FastF1 3.9 removed `utils.delta_time`.
- **Mini-sectors**: the lap is cut into 25 equal-length segments, and each goes to the driver who
  spent *less time* in it. Mean speed is not used because it over-weights slow corners.
- **Race pace / degradation** use green-flag laps only (no pit in/out, no SC/VSC/yellow, not
  deleted, within 107% of the fastest). The degradation slope still includes fuel burn-off,
  so true tyre wear is somewhat higher than shown.
- **Brake** is an on/off channel in the public feed; there is no pressure data.

## Tests

```bash
cd f1_dashboard
pip install -r requirements-dev.txt
python -m pytest
```

The tests use small synthetic frames that follow FastF1's column schema. Expected values are
hand-derivable: for example, 5 km at 50 vs 40 m/s gives a 25 s gap. The tests drive the whole
Streamlit app with `AppTest`, including error and empty states. The app itself never shows
synthetic data.

## Layout

```
.streamlit/config.toml     theme for runs from the repo root (Streamlit Cloud)
f1_dashboard/
  app.py                   Streamlit UI
  f1dash/data.py           only module that imports fastf1 / touches the network
  f1dash/analysis.py       pure DataFrame transforms (unit-tested)
  f1dash/charts.py         Plotly figures
  f1dash/theme.py          palette, Plotly template, CSS
  .streamlit/config.toml   theme for runs from this folder
  tests/                   pytest + Streamlit AppTest suite
  requirements.txt         runtime dependencies
```

Unofficial fan project. Not associated with Formula 1, the FIA or any F1 team.
