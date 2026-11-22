"""Plotly figure builders. Inputs are the frames produced by `analysis`."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import analysis as an
from . import theme as th

th.register_template()


def _empty(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(color=th.TEXT_MUTED))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig.update_layout(height=220)


def telemetry(car_a: pd.DataFrame, car_b: pd.DataFrame, name_a: str, name_b: str,
              corners: pd.DataFrame | None = None) -> go.Figure:
    """Delta on top, then speed / throttle / brake / gear on one distance axis."""
    rows = [("Δ (s) · + = %s ahead" % name_a, 0.18), ("Speed (km/h)", 0.36),
            ("Throttle (%)", 0.16), ("Brake", 0.10), ("Gear", 0.20)]
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.025,
                        row_heights=[h for _, h in rows])
    delta = an.lap_delta(car_a, car_b)
    fig.add_trace(go.Scatter(x=delta["Distance"], y=delta["Delta"], name="Δ",
                             line=dict(color=th.ACCENT_2, width=1.5), fill="tozeroy",
                             fillcolor="rgba(192,197,202,0.10)", showlegend=False,
                             hovertemplate="Δ %{y:+.3f}s<extra></extra>"), row=1, col=1)

    for car, name, color, dash in ((car_a, name_a, th.DRIVER_A, "solid"),
                                   (car_b, name_b, th.DRIVER_B, th.DASH_B)):
        x = car["Distance"]
        line = dict(color=color, width=1.8, dash=dash)
        common = dict(x=x, legendgroup=name, line=line)
        fig.add_trace(go.Scatter(y=car["Speed"], name=name, **common,
                                 hovertemplate=name + " %{y:.0f} km/h<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Scatter(y=car["Throttle"].clip(0, 100), showlegend=False, **common,
                                 hovertemplate=name + " %{y:.0f}%<extra></extra>"), row=3, col=1)
        fig.add_trace(go.Scatter(y=car["Brake"].astype(float), showlegend=False, line_shape="hv",
                                 **common, hovertemplate=name + " brake %{y:.0f}<extra></extra>"),
                      row=4, col=1)
        fig.add_trace(go.Scatter(y=car["nGear"], showlegend=False, line_shape="hv", **common,
                                 hovertemplate=name + " gear %{y}<extra></extra>"), row=5, col=1)

    for i, (title, _) in enumerate(rows, start=1):
        fig.update_yaxes(title_text=title, title_font=dict(size=11, color=th.TEXT_MUTED), row=i, col=1)
    fig.update_yaxes(range=[-0.1, 1.1], tickvals=[0, 1], ticktext=["off", "on"], row=4, col=1)
    fig.update_yaxes(dtick=2, row=5, col=1)

    marks = an.corners_with_distance(corners)
    for _, c in marks.iterrows():
        fig.add_vline(x=c["Distance"], line=dict(color=th.HAIRLINE, width=1, dash="dot"))
        fig.add_annotation(x=c["Distance"], y=1.0, yref="paper", text=c["Label"], showarrow=False,
                           yanchor="bottom", font=dict(size=10, color=th.TEXT_MUTED))
    fig.update_xaxes(title_text="Distance (m)", row=len(rows), col=1)
    return fig.update_layout(height=760, hovermode="x unified",
                             legend=dict(orientation="h", y=1.04, x=1, xanchor="right"))


def minisector_map(ms: pd.DataFrame, name_a: str, name_b: str, rotation: float = 0.0) -> go.Figure:
    """Track outline split into runs coloured by the faster driver."""
    if ms.empty:
        return _empty("Position data not available for this lap")
    x, y = an.rotate_xy(ms["X"], ms["Y"], rotation)
    ms = ms.assign(RX=x, RY=y)
    color = {name_a: th.DRIVER_A, name_b: th.DRIVER_B}
    fig = go.Figure()
    run_id = (ms["Winner"] != ms["Winner"].shift()).cumsum()
    shown = set()
    for _, run in ms.groupby(run_id, sort=False):
        drv = run["Winner"].iat[0]
        nxt = ms.loc[run.index[-1] + 1: run.index[-1] + 1]  # bridge gap to next run
        seg = pd.concat([run, nxt])
        fig.add_trace(go.Scatter(
            x=seg["RX"], y=seg["RY"], mode="lines", name=drv, legendgroup=drv,
            showlegend=drv not in shown, line=dict(color=color[drv], width=6),
            hovertemplate=f"{drv} faster · mini-sector %{{customdata}}<extra></extra>",
            customdata=seg["MiniSector"] + 1))
        shown.add(drv)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1)
    return fig.update_layout(height=460, plot_bgcolor="rgba(0,0,0,0)",
                             legend=dict(orientation="h", y=-0.02))


def stint_chart(st: pd.DataFrame, order: list[str]) -> go.Figure:
    if st.empty:
        return _empty("No stint data")
    order = [d for d in order if d in set(st["Driver"])]
    fig = go.Figure()
    for comp, g in st.groupby("Compound"):
        fig.add_trace(go.Bar(
            y=g["Driver"], x=g["Laps"], base=g["StartLap"] - 1, orientation="h", name=comp.title(),
            marker=dict(color=th.COMPOUND_COLORS.get(comp, th.COMPOUND_COLORS["UNKNOWN"]),
                        line=dict(color=th.BG, width=2)),
            text=th.COMPOUND_LETTER.get(comp, "?"), textposition="inside", insidetextanchor="middle",
            textfont=dict(color=th.BG, family=th.FONT_MONO),
            customdata=np.stack([g["StartLap"], g["EndLap"]], axis=1),
            hovertemplate="%{y} · " + comp.title() + " · laps %{customdata[0]}–%{customdata[1]}<extra></extra>"))
    fig.update_yaxes(categoryorder="array", categoryarray=order[::-1])
    fig.update_xaxes(title_text="Lap")
    return fig.update_layout(barmode="overlay", height=max(320, 26 * len(order) + 80),
                             hovermode="closest", legend=dict(orientation="h", y=1.06))


def pace_chart(clean: pd.DataFrame, drivers: list[str]) -> go.Figure:
    """Clean lap times vs lap number; colour = compound, symbol/line = driver."""
    df = clean[clean["Driver"].isin(drivers)]
    if df.empty:
        return _empty("No green-flag laps for these drivers")
    fig = go.Figure()
    # Fill = compound, outline + symbol = driver, so the legend identifies drivers.
    styles = [("circle", th.DRIVER_A), ("diamond", th.DRIVER_B)]
    for drv, (sym, edge) in zip(drivers, styles):
        g = df[df["Driver"] == drv]
        fig.add_trace(go.Scatter(
            x=g["LapNumber"], y=g["LapTimeS"], mode="markers", name=drv,
            marker=dict(symbol=sym, size=9, line=dict(width=2, color=edge),
                        color=[th.COMPOUND_COLORS.get(c, th.COMPOUND_COLORS["UNKNOWN"]) for c in g["Compound"]]),
            customdata=np.stack([g["Compound"].fillna("?"), g["TyreLife"].fillna(-1),
                                 g["LapTimeS"].map(an.fmt_laptime)], axis=1),
            hovertemplate=drv + " L%{x} · %{customdata[2]} · %{customdata[0]} age %{customdata[1]:.0f}<extra></extra>"))
    fig.update_xaxes(title_text="Lap")
    fig.update_yaxes(title_text="Lap time (s)")
    return fig.update_layout(height=420, hovermode="closest", legend=dict(orientation="h", y=1.08))


def positions_chart(pos: pd.DataFrame, a: str, b: str, order: list[str]) -> go.Figure:
    if pos.empty:
        return _empty("Position data is only available for races and sprints")
    fig = go.Figure()
    field = [d for d in order if d not in (a, b)] + [a, b]  # draw selected on top
    for i, drv in enumerate(field):
        g = pos[pos["Driver"] == drv]
        if g.empty:
            continue
        sel = drv in (a, b)
        color = th.DRIVER_A if drv == a else th.DRIVER_B if drv == b else th.FIELD[i % len(th.FIELD)]
        fig.add_trace(go.Scatter(
            x=g["LapNumber"], y=g["Position"], mode="lines", name=drv,
            line=dict(color=color, width=3 if sel else 1, dash=th.DASH_B if drv == b else "solid"),
            opacity=1 if sel else 0.35,
            hovertemplate=drv + " L%{x} P%{y}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", title_text="Position", dtick=1)
    fig.update_xaxes(title_text="Lap")
    return fig.update_layout(height=520, hovermode="closest", legend=dict(font=dict(size=10)))


def ideal_gap_chart(sectors: pd.DataFrame, a: str, b: str) -> go.Figure:
    """Time each driver left on the table: best lap minus sum of best sectors."""
    df = sectors.dropna(subset=["Gap"])
    if df.empty:
        return _empty("Sector times not available")
    colors = [th.DRIVER_A if d == a else th.DRIVER_B if d == b else th.ACCENT_2 for d in df["Driver"]]
    fig = go.Figure(go.Bar(x=df["Driver"], y=df["Gap"], marker_color=colors,
                           hovertemplate="%{x}: %{y:.3f}s off ideal<extra></extra>"))
    fig.update_yaxes(title_text="Best lap − ideal (s)")
    return fig.update_layout(height=320, hovermode="closest")
