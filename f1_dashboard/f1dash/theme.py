"""Visual system: near-black carbon, one teal accent, brushed-silver secondary.

Every colour lives here so charts, CSS and tests read from one place.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

BG = "#0A0B0D"
SURFACE = "#121417"
SURFACE_RAISED = "#1A1D21"
HAIRLINE = "#2A2E33"
GRID = "rgba(255,255,255,0.07)"
TEXT = "#ECEEF0"
TEXT_MUTED = "#9BA3AB"
ACCENT = "#00D2BE"      # teal: selection, primary driver, key KPI
ACCENT_2 = "#C0C5CA"    # brushed silver

# Head-to-head pair: teal vs amber split on the blue-yellow axis (survives
# deuteranopia/protanopia); driver B is also dashed so colour is never the only cue.
DRIVER_A = ACCENT
DRIVER_B = "#FF8A3D"
DASH_B = "dash"

COMPOUND_COLORS = {
    "SOFT": "#E8002D", "MEDIUM": "#FFD12E", "HARD": "#F0F0EC",
    "INTERMEDIATE": "#43B02A", "WET": "#0072C6", "UNKNOWN": "#5A6068",
}
COMPOUND_LETTER = {"SOFT": "S", "MEDIUM": "M", "HARD": "H",
                   "INTERMEDIATE": "I", "WET": "W", "UNKNOWN": "?"}

# Field colours for many-driver charts (positions). Teal is excluded so it keeps
# meaning "selected driver".
FIELD = ["#C0C5CA", "#7AA7FF", "#FF8A3D", "#B48CFF", "#F2C14E", "#FF6B8B",
         "#6FD08C", "#8A939C", "#E0E4E8", "#4FA3D1"]

FONT_BODY = "Inter, system-ui, sans-serif"
FONT_MONO = "JetBrains Mono, ui-monospace, monospace"

TEMPLATE_NAME = "carbon_teal"


def register_template() -> None:
    pio.templates[TEMPLATE_NAME] = go.layout.Template(layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=BG,
        font=dict(family=FONT_BODY, color=TEXT, size=13),
        colorway=[DRIVER_A, DRIVER_B, ACCENT_2, "#7AA7FF"],
        xaxis=dict(gridcolor=GRID, zerolinecolor=HAIRLINE, linecolor=HAIRLINE,
                   tickfont=dict(family=FONT_MONO, color=TEXT_MUTED, size=11)),
        yaxis=dict(gridcolor=GRID, zerolinecolor=HAIRLINE, linecolor=HAIRLINE,
                   tickfont=dict(family=FONT_MONO, color=TEXT_MUTED, size=11)),
        hoverlabel=dict(bgcolor=SURFACE_RAISED, bordercolor=ACCENT,
                        font=dict(family=FONT_MONO, color=TEXT)),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT_MUTED)),
        margin=dict(l=56, r=16, t=40, b=40),
    ))
    pio.templates.default = TEMPLATE_NAME


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,600;0,700;1,700&family=Inter:wght@400;500&family=JetBrains+Mono:wght@500&display=swap');
html, body, [data-testid="stAppViewContainer"] {{ background:{BG}; color:{TEXT}; font-family:{FONT_BODY}; }}
[data-testid="stSidebar"] {{ background:{SURFACE}; border-right:1px solid {HAIRLINE}; }}
h1, h2, h3 {{ font-family:'Barlow Condensed', sans-serif; text-transform:uppercase; letter-spacing:.06em; }}
h1 {{ font-style:italic; font-weight:700; }}
[data-testid="stMetric"] {{ background:{SURFACE}; border:1px solid {HAIRLINE}; border-left:3px solid {ACCENT_2};
  border-radius:2px; padding:.6rem .8rem; }}
[data-testid="stMetricLabel"] p {{ font-family:'Barlow Condensed', sans-serif; text-transform:uppercase;
  letter-spacing:.08em; color:{TEXT_MUTED}; }}
[data-testid="stMetricValue"] {{ font-family:{FONT_MONO}; font-variant-numeric:tabular-nums;
  font-size:clamp(1.1rem, 1.6vw, 1.6rem); color:{TEXT}; }}
[data-testid="stHeader"] {{ background:{BG}; }}
[data-testid="stTabs"] button[aria-selected="true"] {{ color:{ACCENT}; }}
.tl-rule {{ height:2px; background:linear-gradient(90deg,{ACCENT} 0 64px,{HAIRLINE} 64px); margin:-.4rem 0 1rem; }}
.tl-matchup {{ font-family:'Barlow Condensed',sans-serif; font-size:1.35rem; text-transform:uppercase; letter-spacing:.06em; }}
.tl-a {{ color:{DRIVER_A}; }} .tl-b {{ color:{DRIVER_B}; }} .tl-muted {{ color:{TEXT_MUTED}; }}
.tl-foot {{ color:{TEXT_MUTED}; font-size:.75rem; border-top:1px solid {HAIRLINE}; padding-top:.6rem; margin-top:2rem; }}
</style>
"""
