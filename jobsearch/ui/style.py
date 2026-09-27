"""Shared look-and-feel for the dashboard: a dark, neon, glass-panel style layered on the theme in
.streamlit/config.toml, plus page headers, cards and status badges."""

import html
from pathlib import Path

import streamlit as st

CSS = """
<style>
:root {
  --cy: #22D3EE; --vi: #A78BFA; --mint: #34D399;
  --glass: rgba(12, 19, 34, 0.62);
  --edge: rgba(34, 211, 238, 0.16);
  --edge-hi: rgba(34, 211, 238, 0.55);
  --mono: 'JetBrains Mono', ui-monospace, monospace;
  --display: 'Space Grotesk', Inter, sans-serif;
}

/* Backdrop: faint grid + cyan/violet glows on near-black */
[data-testid="stApp"] {
  background:
    radial-gradient(1100px 560px at 88% -12%, rgba(167,139,250,.16), transparent 60%),
    radial-gradient(900px 520px at -8% 6%, rgba(34,211,238,.11), transparent 60%),
    linear-gradient(rgba(34,211,238,.035) 1px, transparent 1px) 0 0 / 44px 44px,
    linear-gradient(90deg, rgba(34,211,238,.035) 1px, transparent 1px) 0 0 / 44px 44px,
    #05070D;
  background-attachment: fixed;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 2rem; padding-bottom: 3rem; max-width: 1320px; }
::selection { background: rgba(34,211,238,.28); }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(34,211,238,.22); border-radius: 8px; }
::-webkit-scrollbar-track { background: transparent; }

/* Headings: gradient display type */
h1 {
  font-family: var(--display) !important; letter-spacing: -0.03em;
  background: linear-gradient(92deg, #ECFEFF 0%, var(--cy) 42%, var(--vi) 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent !important;
}
h2, h3 { font-family: var(--display) !important; letter-spacing: -0.015em; }
h3 { color: #CFFAFE; }

/* Page kicker + subtitle */
.js-kicker { font-family: var(--mono); font-size: .72rem; letter-spacing: .22em; color: var(--cy);
             text-transform: uppercase; opacity: .85; margin-bottom: -.4rem; }
.js-kicker span { color: var(--vi); padding: 0 .35rem; }
.js-sub { font-family: var(--mono); font-size: .8rem; color: #7C8BA5; margin: -.3rem 0 1.1rem 0; }
.js-sub::before { content: "// "; color: rgba(34,211,238,.6); }

/* System status strip */
.js-status { display: flex; flex-wrap: wrap; gap: .5rem; margin: .2rem 0 1.2rem 0; }
.js-chip { font-family: var(--mono); font-size: .72rem; letter-spacing: .06em; color: #A5B4CC;
           border: 1px solid var(--edge); background: rgba(12,19,34,.55); border-radius: 999px; padding: .28rem .7rem; }
.js-chip b { color: #E6EDF7; font-weight: 600; }
.js-dot { display: inline-block; width: .5rem; height: .5rem; border-radius: 50%; margin-right: .45rem;
          background: var(--mint); box-shadow: 0 0 8px var(--mint); vertical-align: middle; }
.js-dot.warn { background: #FBBF24; box-shadow: 0 0 8px #FBBF24; }
.js-dot.err { background: #F87171; box-shadow: 0 0 8px #F87171; }

/* Glass panels: metrics, cards, expanders */
[data-testid="stMetric"], [class*="st-key-card-"], [data-testid="stExpander"] details {
  background: var(--glass) !important;
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--edge) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.035), 0 10px 30px rgba(0,0,0,.35);
  transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}
[data-testid="stMetric"]:hover, [class*="st-key-card-"]:hover {
  border-color: var(--edge-hi) !important;
  box-shadow: 0 0 0 1px rgba(34,211,238,.15), 0 0 26px rgba(34,211,238,.16), 0 10px 30px rgba(0,0,0,.35);
}
[data-testid="stMetric"] { position: relative; overflow: hidden; }
[data-testid="stMetric"]::before {   /* neon top edge */
  content: ""; position: absolute; left: 0; right: 0; top: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--cy), var(--vi), transparent); opacity: .8;
}
[data-testid="stMetricLabel"] p { font-family: var(--mono); font-size: clamp(.6rem, .75vw, .7rem); text-transform: uppercase;
                                  letter-spacing: .08em; color: #7DD3FC; opacity: .9; }
[data-testid="stMetricValue"] { font-family: var(--display); font-size: clamp(1.35rem, 2.1vw, 2.05rem); font-weight: 600;
                                text-shadow: 0 0 22px rgba(34,211,238,.35); }
[data-testid="stExpander"] summary:hover { color: var(--cy); }

/* Buttons */
[data-testid="stBaseButton-primary"] {
  background: linear-gradient(92deg, #06B6D4, #8B5CF6) !important; color: #03050A !important;
  border: 0 !important; font-weight: 650 !important; box-shadow: 0 0 18px rgba(34,211,238,.32);
}
[data-testid="stBaseButton-primary"]:hover { filter: brightness(1.1); box-shadow: 0 0 28px rgba(139,92,246,.5); }
[data-testid="stBaseButton-secondary"], [data-testid^="stBaseLinkButton"] {
  background: rgba(34,211,238,.04) !important; border: 1px solid var(--edge) !important; font-weight: 550;
}
[data-testid="stBaseButton-secondary"]:hover, [data-testid^="stBaseLinkButton"]:hover {
  border-color: var(--cy) !important; color: var(--cy) !important; box-shadow: 0 0 14px rgba(34,211,238,.25);
}

/* Sidebar */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #03050A 0%, #060A14 100%) !important;
  border-right: 1px solid rgba(34,211,238,.12);
}
[data-testid="stNavSectionHeader"] { font-family: var(--mono); text-transform: uppercase; letter-spacing: .18em;
                                     font-size: .66rem !important; color: #56657F !important; }
[data-testid="stSidebarNavLink"] { border-radius: .55rem; transition: background .15s ease; }
[data-testid="stSidebarNavLink"]:hover { background: rgba(34,211,238,.06) !important; }
[data-testid="stSidebarNavLink"][aria-current="page"] {
  background: linear-gradient(90deg, rgba(34,211,238,.18), rgba(34,211,238,0)) !important;
  box-shadow: inset 2px 0 0 var(--cy);
}
[data-testid="stSidebarNavLink"][aria-current="page"] span { color: #ECFEFF !important; }

/* Progress bars: neon gradient */
[data-testid="stProgress"] [role="progressbar"] > div > div > div {
  background: linear-gradient(90deg, var(--cy), var(--vi)) !important; box-shadow: 0 0 10px rgba(34,211,238,.55);
}

/* Dataframes sit in a glass frame */
[data-testid="stDataFrame"] { border: 1px solid var(--edge); border-radius: .7rem; overflow: hidden; }
</style>
"""

# status -> badge colour (Streamlit markdown badge colours, tuned in config.toml)
STATUS_COLORS = {
    "new": "gray", "scored": "blue", "shortlisted": "violet", "tailored": "orange", "approved": "green",
    "applied": "green", "dismissed": "gray", "filtered_out": "gray",
    "pending_review": "orange", "rejected": "red", "superseded": "gray",
    "needs_input": "red", "ready": "green",
    "profiled": "blue", "sent": "blue", "replied": "green", "skipped": "gray",
}
STATUS_LABELS = {"pending_review": "to review", "needs_input": "needs you", "filtered_out": "filtered out"}

ASSETS = Path(__file__).resolve().parents[2] / "dashboard" / "assets"


def apply() -> None:
    st.html(CSS)


def badge(status: str) -> str:
    """Inline markdown badge for a status, e.g. in headings, captions and expander labels."""
    color = STATUS_COLORS.get(status, "gray")
    return f":{color}-badge[{STATUS_LABELS.get(status, status).replace('_', ' ')}]"


def header(title: str, subtitle: str = "", kicker: str = "") -> None:
    st.html(f'<div class="js-kicker">Job Search Swarm<span>/</span>{html.escape(kicker or title)}</div>')
    st.markdown(f"# {title}")
    if subtitle:
        st.html(f'<div class="js-sub">{html.escape(subtitle)}</div>')


def status_strip(chips: list[tuple[str, str, str]]) -> None:
    """chips: (state: ok|warn|err, label, value) shown as glowing status pills."""
    parts = "".join(
        f'<span class="js-chip"><span class="js-dot {"" if s == "ok" else s}"></span>{html.escape(label)} '
        f"<b>{html.escape(value)}</b></span>" for s, label, value in chips)
    st.html(f'<div class="js-status">{parts}</div>')


def card(key: str):
    """A glass panel. Use as `with style.card("jobs-12"):`; the key must be unique on the page."""
    return st.container(border=True, key=f"card-{key}")


def brand() -> None:
    st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
