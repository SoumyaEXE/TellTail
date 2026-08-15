"""
TELLTAIL — Streamlit in Snowflake.

Nine tabs, in narrative order. A judge who clicks left to right gets the argument
without reading a word of the post:

  1 Pack            who
  2 Live Collar     is it real
  3 Ethogram        what normal looks like
  4 Syndromes       THE FINDING
  5 Baselines       why it is abnormal for THIS dog
  6 Vet Note        what to do
  7 Drivers         what explains it
  8 Shelter Reality why it matters
  9 Pipeline        how it was built

Plus a tenth page, Ask TELLTAIL, which is Cortex answering only from rows.

SIX SiS-ONLY HAZARDS, ALL HANDLED HERE. Every one of them reproduces nowhere
else, which is what makes them expensive:

  1. Snowpark to_pandas() returns numerics as object-dtype Decimal. Plotly then
     treats them as CATEGORIES, so y becomes the row index and every line chart
     renders as an identical straight diagonal. `rows()` converts element-wise
     with float() and every chart is fed plain Python lists.
  2. SiS pins an older Streamlit than you develop against. st.column_config is
     guarded with hasattr and there is an HTML table fallback.
  3. plotly is not importable without environment.yml shipped to the stage next
     to this file. deploy_streamlit.py refuses to deploy without it.
  4. Old plotly drops traces whose customdata is a mixed-type numpy array, and
     the axes scale while zero points render. Hover strings are prebuilt and
     passed as text= with hoverinfo="text".
  5. There is no outbound network, so a photograph can only be shown if its
     bytes are already in the account. Breed photos are base64 in
     REF.BREED_IMAGE, written by scripts/fetch_breed_images.py; everything
     else on screen is inline SVG. They are photographs OF THE BREED and the
     page says so everywhere one appears.
  6. Custom bidirectional components cannot be installed — there is no way to
     serve a component's frontend bundle from the sandbox. The chat on the
     last page is AI-Yash/st-chat's design ported to plain HTML and CSS, with
     locally drawn avatars in place of its DiceBear URLs.

AND ONE ARCHITECTURAL RULE: this app reads TABLES. It never calls a Cortex AI
function, because a render path that costs credits is a render path that will
exhaust a trial cap during a demo. Every note, triage and brief on screen was
batched into a table by a task.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import streamlit as st

# ---------------------------------------------------------------------------
# design system  (spec section 15: clinical register, one accent, no chart junk)
# ---------------------------------------------------------------------------
SURFACE = "#FAFAF9"
CARD = "#FFFFFF"
BORDER = "#E7E5E4"
INK = "#1C1917"
INK_2 = "#57534E"
ACCENT = "#B45309"
GRID = "#F5F5F4"

# Chat surface, taken from the reference build rather than the palette above.
# The rest of the app is a warm stone scale; a chat transcript is the one place
# that reads as a conversation rather than a document, and the reference greys
# are what make it read that way.
CHAT_BG = "#F0F2F6"
CHAT_INK = "#262730"
CHAT_BOT = "#DD6B4D"     # bottts orange
CHAT_USER = "#FBD7B0"    # fun-emoji peach

TRIAGE_COLOUR = {
    1: "#15803D", "routine monitoring": "#15803D",
    2: "#B45309", "schedule appointment": "#B45309",
    3: "#B91C1C", "urgent veterinary attention": "#B91C1C",
}
# Pattern-symbol palette for the hero ribbon. Deliberately not the state palette:
# the point of that chart is which PATTERN VARIABLE each epoch played.
SYMBOL_COLOURS = [
    "#1C1917", "#B45309", "#0369A1", "#B91C1C", "#15803D",
    "#7C3AED", "#0F766E", "#A16207", "#BE185D",
]

# ---------------------------------------------------------------------------
# ONE HEIGHT SCALE FOR EVERY CHART IN THE APP.
#
# These were once twelve hand-picked numbers — 54, 90, 210, 220, 230, 250, 260,
# 280, 290, 300, 330, 430 — each chosen for its own chart as it was written.
# Every chart was fine and the whole was a mess: two charts side by side in one
# row ended at different heights, the same kind of chart was a different size on
# two pages, and nine pages of that reads as unfinished.
#
# The rule now: pick a step off this scale, and two charts sharing a row pick
# the SAME step. Horizontal bar charts use bars(), because a 25-bar chart
# genuinely does need more room than a 4-bar one — but it grows on a fixed row
# pitch and clamps, so it cannot run away down the page.
# ---------------------------------------------------------------------------
H_STRIP = 56       # one stacked bar, no axes (the triage mix)
H_RIBBON = 96      # a state or symbol ribbon
H_SM = 260         # a supporting chart, or one of a stack of time series
H_MD = 320         # the default: a chart in a two-column row
H_LG = 420         # a hero — the 3D feature space


def bars(n: int, *, row: int = 24) -> int:
    """Height for a horizontal bar chart of n categories."""
    return max(H_SM, min(560, row * int(n or 0) + 76))


st.set_page_config(page_title="TELLTAIL", page_icon="🐕", layout="wide")

st.markdown(f"""
<style>
  .stApp {{ background: {SURFACE}; }}
  html, body, [class*="css"] {{
      font-family: Geist, Inter, -apple-system, "Segoe UI", sans-serif;
      font-variant-numeric: tabular-nums;
      color: {INK};
  }}
  h1, h2, h3, h4 {{ color: {INK}; letter-spacing: -0.01em; }}
  .tt-card {{
      background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px;
      padding: 12px 14px; margin-bottom: 10px;
  }}
  .tt-metric-label {{ font-size: 11px; text-transform: uppercase;
      letter-spacing: .06em; color: {INK_2}; }}
  .tt-metric-value {{ font-size: 26px; font-weight: 600; color: {INK}; line-height: 1.1; }}
  /* The pack grid. Every card is the same height and the sparkline is pinned
     to the bottom, so 45 cards read as a grid instead of a ragged column.
     Breed names run from "Beauceron" to "Nova Scotia Duck Tolling Retriever";
     left to themselves they wrap onto a second line, shove the triage badge
     down, and every card in that row ends up a different height. */
  .tt-dogcard {{ display:flex; flex-direction:column; min-height: 180px;
      margin-bottom: 8px; padding: 11px 13px; }}
  .tt-dogcard-head {{ display:flex; justify-content:space-between;
      align-items:flex-start; gap:8px; }}
  /* photo + name as one unit, so the triage badge stays hard right and the
     thumbnail never pushes the breed onto a second line */
  .tt-dogcard-id {{ display:flex; align-items:center; gap:9px; min-width:0; }}
  .tt-dogcard-name {{ min-width:0; }}
  .tt-dogcard-name b {{ font-size:15px; white-space:nowrap; }}
  /* clip rather than wrap: the badge must stay on the first line */
  .tt-breed {{ display:block; overflow:hidden; text-overflow:ellipsis;
      white-space:nowrap; max-width:100%; }}
  .tt-badge {{ white-space:nowrap; flex:0 0 auto; }}
  .tt-chiprow {{ display:flex; flex-wrap:wrap; gap:4px; }}
  /* margin-top:auto pushes the sparkline and footer to the card floor, which
     is what makes the bottom edges line up across a row */
  .tt-spark {{ margin-top:auto; padding-top:8px; }}
  .tt-dogcard-foot {{ margin-top:4px; }}
  /* ------------------------------------------------------------------
     LEFT RAIL. The nav is an st.radio because the router needs its value,
     but a bare radio list reads as a form control rather than navigation.
     These rules turn each option into a nav row — full-width hit area,
     hover, and a hue bar on the selected one — without touching the widget
     itself. Every selector degrades to a plain radio if SiS ships a build
     whose DOM does not match, which is why none of them hide anything.
     ------------------------------------------------------------------ */
  section[data-testid="stSidebar"] {{ background: {CARD};
      border-right: 1px solid {BORDER}; }}
  section[data-testid="stSidebar"] .stRadio > div {{ gap: 1px; }}
  section[data-testid="stSidebar"] label {{ font-size: 13.5px; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 1px; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
      display: flex; align-items: center; width: 100%;
      padding: 5px 8px 5px 7px; margin: 0; border-radius: 5px;
      border-left: 2px solid transparent; cursor: pointer; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{
      background: {SURFACE}; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {{
      background: {SURFACE}; font-weight: 600; }}
  .tt-brand {{ display:flex; align-items:center; gap:9px; margin-bottom:12px; }}
  .tt-brand-mark {{ width:34px; height:34px; border-radius:8px; flex:0 0 auto;
      background:{INK}; display:flex; align-items:center; justify-content:center; }}
  .tt-railstat {{ margin-top: 14px; border-top: 1px solid {BORDER};
      padding-top: 10px; font-size: 12px; }}
  .tt-railstat > div {{ display:flex; justify-content:space-between;
      align-items:baseline; padding: 3px 0; gap: 8px; }}
  .tt-railstat span {{ color: {INK_2}; }}
  .tt-railstat b {{ color: {INK}; font-size: 13px; font-variant-numeric: tabular-nums; }}
  /* a labelled number with the proportion it represents drawn underneath it,
     so "38 of 45" is a length as well as a ratio */
  .tt-meter {{ margin: 7px 0 0; }}
  .tt-meter-top {{ display:flex; justify-content:space-between;
      align-items:baseline; gap:8px; font-size:12px; }}
  .tt-meter-top span {{ color:{INK_2}; }}
  .tt-meter-top b {{ color:{INK}; font-size:12.5px; font-variant-numeric:tabular-nums; }}
  .tt-meter-track {{ height:3px; border-radius:2px; background:{GRID};
      margin-top:4px; overflow:hidden; }}
  .tt-meter-fill {{ height:100%; border-radius:2px; }}
  .tt-pill {{ display:inline-flex; align-items:center; gap:5px; font-size:11px;
      padding:3px 8px; border-radius:11px; border:1px solid {BORDER};
      background:{SURFACE}; color:{INK_2}; }}
  .tt-railfoot {{ margin-top: 10px; font-size: 11px; color: {INK_2};
      line-height: 1.45; }}
  .tt-railnote {{ margin-top: 12px; padding: 8px 10px; background: {SURFACE};
      border-radius: 0 4px 4px 0; font-size: 12px; line-height: 1.4; }}
  .tt-pagehead {{ padding: 2px 0 2px 12px; margin: 0 0 12px; }}
  .tt-pagetitle {{ font-size: 21px; font-weight: 700; letter-spacing: -.015em;
      color: {INK}; }}
  /* Streamlit's default block padding wastes the top third of a 1080p screen
     on a dashboard that is meant to be read at a glance across the room. */
  .block-container {{ padding-top: 2.2rem; padding-bottom: 2rem; }}
  h3 {{ margin-top: .4rem !important; }}
  .tt-chip {{ display:inline-block; padding: 1px 7px; border-radius: 3px;
      font-size: 11px; font-weight: 600; border: 1px solid {BORDER}; }}
  .tt-badge {{ display:inline-block; padding: 2px 9px; border-radius: 3px;
      font-size: 11px; font-weight: 700; color: #fff; }}
  .tt-mono {{ font-family: "Geist Mono", ui-monospace, "SF Mono", Consolas, monospace;
      font-size: 12px; }}
  .tt-caveat {{ background: #FEF3C7; border-left: 3px solid {ACCENT};
      padding: 8px 12px; font-size: 12px; color: #78350F; margin: 8px 0; }}
  .tt-quiet {{ color: {INK_2}; font-size: 12px; }}
  div[data-testid="stMetricValue"] {{ font-variant-numeric: tabular-nums; }}
  /* ------------------------------------------------------------------
     CHAT BUBBLES — ported from AI-Yash/st-chat (streamlit_chat/frontend/
     src/stChat.css): .chat flex row, .chat.user row-reverse, a 50% round
     avatar, and a .msg::after triangle made of transparent borders.

     PORTED RATHER THAN INSTALLED, for two reasons that are both hard rules
     here. Streamlit in Snowflake cannot load a custom bidirectional
     component — there is no way to serve the package's compiled frontend
     bundle from inside the sandbox. And st-chat draws its avatars from
     DiceBear over HTTPS, which is exactly the outbound request SiS blocks,
     so the stock component would render two broken images per turn. The
     avatars below are inline SVG for the same reason the breed photos are
     base64.

     One deliberate change: st-chat anchors the tail to the row at top:0
     with border-top-color, which works because every message is its own
     iframe. In one page that puts a stray notch above each bubble, so the
     tail is anchored to the bubble and points sideways.
     ------------------------------------------------------------------ */
  .tt-chat {{ display:flex; flex-direction:row; align-items:flex-start;
      width:100%; margin:0 0 22px; }}
  .tt-chat.user {{ flex-direction: row-reverse; }}
  .tt-chat .avatar {{ display:flex; align-items:center; justify-content:center;
      height:48px; width:48px; flex:0 0 auto; margin:0 6px; overflow:hidden;
      border-radius:50%; }}
  /* No border and no tail: the reference build draws a plain soft-grey
     rectangle and lets ALIGNMENT carry the speaker. Adding a rule or a
     triangle to it is the thing that makes a ported chat look almost-right. */
  .tt-chat .msg {{ display:inline-block; margin:0 8px; padding:13px 18px;
      max-width:70%; min-height:1.5rem; line-height:1.7; font-size:16px;
      white-space:pre-line; border-radius:10px; background:{CHAT_BG};
      color:{CHAT_INK}; border:none; }}
  .tt-chat .msg p {{ margin-block: 0; }}
  .tt-chat-meta {{ font-size:10.5px; color:{INK_2}; margin:-16px 0 22px 68px; }}
  table.tt {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  table.tt th {{ text-align: left; border-bottom: 1px solid {BORDER}; padding: 5px 8px;
      color: {INK_2}; font-weight: 600; text-transform: uppercase; font-size: 10px;
      letter-spacing: .05em; }}
  table.tt td {{ border-bottom: 1px solid {GRID}; padding: 5px 8px; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# data access
# ---------------------------------------------------------------------------

def _session():
    from snowflake.snowpark.context import get_active_session
    return get_active_session()


def _scrub(v):
    """Decimal -> float, everything else untouched.

    THIS IS HAZARD 1. Without it, plotly receives object-dtype Decimals, decides
    the axis is categorical, and every line chart in the app renders as the same
    straight diagonal. It looks like a plotting bug and it is a dtype bug.
    """
    if isinstance(v, Decimal):
        return float(v)
    return v


@st.cache_data(ttl=45, show_spinner=False)
def rows(sql: str) -> list[dict]:
    """Run a query, return plain Python dicts. Cached, so a tab re-render does
    not re-bill the warehouse."""
    try:
        res = _session().sql(sql).collect()
    except Exception as exc:  # noqa: BLE001
        st.session_state.setdefault("_errors", []).append((sql[:120], str(exc)))
        return []
    return [{k: _scrub(v) for k, v in r.as_dict().items()} for r in res]


def rows_live(sql: str) -> list[dict]:
    """rows() without the cache. Exactly one caller: the Cortex chat, where a
    cached answer to a new question would be a lie."""
    try:
        res = _session().sql(sql).collect()
    except Exception as exc:  # noqa: BLE001
        st.session_state.setdefault("_errors", []).append((sql[:120], str(exc)))
        raise
    return [{k: _scrub(v) for k, v in r.as_dict().items()} for r in res]


@st.cache_data(ttl=900, show_spinner=False)
def rows_quiet(sql: str) -> list[dict]:
    """rows() for OPTIONAL sources, swallowing the error instead of logging it.

    REF.BREED_IMAGE is populated by scripts/fetch_breed_images.py, which needs a
    Kaggle token. On an account where that has never run the table simply does
    not exist, and that is a fact about the account rather than a bug — listing
    it in the query-error expander would train the reader to ignore that panel.
    """
    try:
        res = _session().sql(sql).collect()
    except Exception:  # noqa: BLE001
        return []
    return [{k: _scrub(v) for k, v in r.as_dict().items()} for r in res]


def sq(text: str) -> str:
    """Single-quote a string for inline SQL. The Snowpark .sql() path here takes
    no bind parameters, so the literal has to be escaped rather than bound."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


def col(data: list[dict], name: str, default=None) -> list:
    return [r.get(name, default) for r in data]


def one(data: list[dict], name: str, default=None):
    return data[0].get(name, default) if data else default


def ago(seconds) -> str:
    """Seconds since the last epoch, in units a human reads at a glance.

    The raw number is unreadable past a minute or two — the bulk corpus dogs
    were recorded in 2018 and rendered as "stale 17,715,743s", which says
    nothing except that something is broken. Past a week it is not staleness at
    all, it is the archive, and the caller labels it as such.
    """
    try:
        v = float(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    if v < 90:
        return f"{v:.0f}s"
    if v < 5400:
        return f"{v / 60:.0f}m"
    if v < 172800:
        return f"{v / 3600:.0f}h"
    return f"{v / 86400:.0f}d"


def sparkline_svg(series, width=260, height=34, colour=None) -> str:
    """A sparkline as inline SVG, drawn INSIDE the card markup.

    Was one st.plotly_chart per dog, rendered after the card's closing div —
    which put a figure-sized gap under every card, let the cards and their
    sparklines drift apart on reflow, and paid for 45 Plotly figures on the
    first tab. An SVG polyline costs nothing and belongs to the card.
    """
    pts = []
    for x in series or []:
        try:
            pts.append(float(x))
        except (TypeError, ValueError):
            pass
    if len(pts) < 3:
        return f'<div style="height:{height}px"></div>'
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    step = width / (len(pts) - 1)
    coords = " ".join(
        f"{i * step:.1f},{height - 3 - ((v - lo) / rng) * (height - 6):.1f}"
        for i, v in enumerate(pts)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'style="width:100%;height:{height}px;display:block">'
        f'<polyline points="{coords}" fill="none" '
        f'stroke="{colour or ACCENT}" stroke-width="1.2" '
        f'stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>'
    )


# --------------------------------------------------------------------------
# The dog, drawn rather than fetched.
#
# Streamlit in Snowflake has no outbound internet, so every <img src="http...">
# renders as a broken icon — no CDN illustrations, no remote breed photos, no
# matter how much nicer they would look. Only two kinds of picture survive the
# sandbox: one drawn inline as SVG, and one whose bytes are ALREADY IN THE
# ACCOUNT (see breed_photo below, reading base64 out of REF.BREED_IMAGE).
#
# The diagrams below stay drawn even now that the photographs exist, because
# they are the more useful picture: what a viewer needs from this tab is not
# what a Beauceron looks like, it is WHERE THE TWO SENSORS SIT, since the whole
# detection argument rests on the relationship between them.
# --------------------------------------------------------------------------

# One silhouette per posture, so a state reads as a shape before it reads as a
# word. Paths are deliberately crude — a recognisable stance beats a portrait.
_DOG_BODY = {
    "stand": "M14,44 L14,30 Q14,22 24,21 L52,21 Q62,22 62,30 L62,44 M20,44 L20,58 "
             "M30,44 L30,58 M48,44 L48,58 M58,44 L58,58",
    "rest":  "M12,52 Q12,44 24,44 L54,44 Q66,44 66,52 L66,58 L12,58 Z",
    "sit":   "M16,44 L16,30 Q16,22 26,21 L52,21 Q62,22 62,34 L62,58 L46,58 "
             "Q40,50 30,50 L20,58 Z",
    "move":  "M14,42 L14,28 Q14,20 24,19 L52,19 Q62,20 62,28 L62,42 M18,42 L12,58 "
             "M28,42 L34,58 M48,42 L42,58 M58,42 L64,58",
}
_STATE_POSE = {
    "REST": "rest", "SIT": "sit", "STAND": "stand", "SNIFF": "stand",
    "WALK": "move", "TROT": "move", "GALLOP": "move", "PLAY": "move",
    "PACE": "move", "CIRCLE": "move", "SHAKE": "stand", "SCRATCH": "sit",
    "PAUSE": "stand", "SLOW_TRANSITION": "sit", "UNKNOWN": "stand",
}


def dog_glyph(state: str, size: int = 42, colour: str = None) -> str:
    """A small posture silhouette for a state. Used as an inline icon."""
    pose = _STATE_POSE.get((state or "").upper(), "stand")
    col = colour or INK_2
    return (
        f'<svg viewBox="0 0 78 66" style="width:{size}px;height:{int(size*66/78)}px;'
        f'vertical-align:middle" aria-label="{state}">'
        f'<path d="{_DOG_BODY[pose]}" fill="none" stroke="{col}" stroke-width="3.4" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="66" cy="24" r="7" fill="none" stroke="{col}" stroke-width="3.4"/>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# The stand-in dog, for the four study breeds Stanford Dogs does not carry.
#
# Beauceron, Hovawart, Mudi and the crossbreeds have no reference photograph in
# REF.BREED_IMAGE, and the honest thing to show in their place is a picture that
# is obviously not a photograph of anything. This is svgsilh.com/svg/1334196
# (Pixabay, CC0), an auto-traced Labrador.
#
# TWO THINGS ABOUT IT ARE NOT OBVIOUS.
#
# It is a NEGATIVE trace: the 26 KB first path is the whole canvas with the dog
# cut out of it, and the 147 small paths after it are the face detail. Filled
# black on white it renders as a black rectangle. So it is painted PLATE-
# coloured over an INK-coloured rectangle, which knocks the plate out
# everywhere except the dog and leaves the dog standing in ink — see
# dog_silhouette_css().
#
# And it is served as ONE CSS background-image rather than 45 inline copies.
# 148 paths x 45 pack cards is 6,660 path nodes for a placeholder; as a
# background the browser rasterises the thing once and blits it.
# ---------------------------------------------------------------------------
_DOG_SILHOUETTE_G = (
    '<g transform="translate(0.000000,960.000000) scale(0.100000,-0.100000)" fill="{PLATE}" stroke="none"> <path d="M0 4800 l0 -4800 2210 0 2210 0 -9 23 c-4 12 -11 43 -15 69 -4 27 -17 60 -30 75 -20 24 -25 48 -35 148 -6 66 -16 146 -20 177 -5 32 -9 146 -8 255 0 137 -4 210 -13 237 -31 94 8 399 74 593 19 55 37 128 41 164 3 36 18 93 34 129 16 36 36 82 45 103 8 20 20 37 25 37 5 0 28 -35 51 -77 52 -96 87 -143 105 -143 9 0 16 18 20 55 7 49 5 57 -19 85 -30 36 -33 50 -11 50 24 0 18 17 -20 59 -19 21 -35 44 -35 51 0 14 -32 55 -91 117 -22 24 -38 47 -35 52 3 5 -4 11 -16 14 -21 6 -21 7 -5 25 16 17 15 20 -13 41 -17 12 -28 26 -25 31 7 12 -23 40 -43 40 -20 0 -72 87 -88 148 -8 33 -25 55 -68 94 -55 49 -57 52 -51 88 4 21 2 46 -4 56 -5 11 -13 32 -16 48 -3 16 -19 41 -35 56 -26 24 -28 29 -15 40 8 7 15 29 15 49 0 49 11 63 54 67 l37 3 -7 -34 c-6 -36 3 -44 24 -23 19 19 14 -2 -8 -29 -25 -32 -26 -46 -2 -39 25 8 58 -40 41 -60 -6 -8 -9 -18 -5 -21 4 -4 18 7 32 24 14 18 31 30 38 27 8 -3 17 -1 21 5 3 6 11 11 17 11 5 0 -12 -19 -38 -43 -29 -27 -45 -48 -40 -55 5 -8 13 -9 26 -2 15 8 18 5 22 -20 2 -16 0 -36 -4 -44 -5 -10 -5 -16 0 -16 6 0 12 7 16 15 3 8 15 15 26 15 11 0 20 5 20 11 0 6 14 27 30 46 31 35 38 36 19 2 -6 -11 -8 -25 -6 -32 2 -7 11 3 21 21 30 59 45 34 19 -34 -5 -13 -3 -16 10 -11 8 3 18 3 20 -1 2 -4 2 -2 1 5 -2 6 3 16 10 22 11 9 12 5 9 -16 -3 -26 -3 -26 9 -9 16 23 77 86 84 86 2 0 0 -9 -6 -19 -5 -11 -10 -26 -9 -33 0 -7 5 -3 11 10 19 39 29 24 27 -46 -2 -85 10 -183 22 -176 5 3 9 16 9 30 0 14 4 22 10 19 6 -3 10 -20 10 -38 0 -30 0 -31 16 -11 17 20 17 20 43 -3 23 -22 26 -23 31 -7 5 13 9 14 14 5 5 -7 15 -10 23 -7 9 4 22 1 29 -5 11 -9 14 -8 14 1 0 10 3 10 15 0 9 -7 17 -11 18 -9 1 2 9 16 17 31 13 28 34 36 44 19 3 -5 15 -7 26 -4 22 6 37 44 24 64 -4 8 -3 9 4 5 7 -4 12 -13 12 -20 0 -7 9 -21 20 -31 15 -13 23 -15 33 -7 10 9 16 9 25 0 8 -8 15 -8 27 2 8 7 15 9 15 4 0 -5 7 -1 14 9 12 15 15 15 26 2 7 -9 29 -15 52 -15 38 0 40 2 34 24 -3 14 -2 28 4 31 17 11 11 25 -22 56 -36 32 -48 36 -48 15 0 -21 -23 -25 -42 -8 -17 14 -18 13 -18 -9 -1 -23 -2 -23 -32 11 -18 21 -32 30 -35 23 -2 -7 -11 -9 -19 -6 -8 3 -21 -1 -30 -8 -13 -10 -17 -10 -26 2 -7 10 -8 3 -4 -23 2 -21 3 -38 2 -38 -2 0 -16 18 -31 40 -31 45 -41 49 -50 21 -4 -13 -12 -18 -22 -14 -10 4 -14 2 -10 -4 4 -6 3 -22 -2 -35 -9 -21 -10 -20 -10 13 -1 20 -8 42 -16 49 -8 7 -15 21 -15 32 0 14 5 18 15 14 8 -3 15 -1 15 5 0 6 4 8 9 5 5 -4 7 4 3 16 -5 20 -3 20 31 -6 l36 -27 7 36 6 36 20 -20 c10 -12 22 -21 26 -22 52 -6 52 -6 52 23 0 35 13 36 34 3 15 -23 55 -38 41 -15 -3 6 -2 10 4 10 6 0 17 -12 25 -27 15 -27 15 -27 16 -5 0 12 -7 34 -16 47 -20 31 -8 33 17 3 18 -20 20 -20 38 -4 11 10 25 15 30 11 6 -3 11 -1 11 6 0 8 7 5 20 -6 15 -13 23 -15 33 -7 10 9 17 8 25 0 16 -16 16 5 1 35 l-12 22 24 -22 c21 -19 26 -20 43 -7 14 10 23 11 33 3 10 -9 13 -8 13 5 0 15 20 24 20 9 0 -5 4 -14 9 -22 7 -11 13 -9 30 8 17 17 19 25 11 41 -8 14 -8 20 1 20 6 0 16 -14 22 -30 7 -18 17 -28 24 -26 9 4 13 -5 13 -25 0 -17 5 -39 11 -50 5 -11 9 -36 7 -56 -3 -35 -4 -36 -20 -20 -9 9 -19 17 -22 17 -9 0 5 -52 17 -67 14 -19 48 -17 41 2 -4 8 -1 15 5 15 7 0 14 -7 17 -15 8 -19 34 -19 34 0 0 20 7 19 37 -5 14 -11 29 -17 34 -14 5 3 9 -2 9 -11 0 -21 45 -65 66 -65 15 0 13 5 -9 28 -32 33 -34 53 -9 75 23 21 46 22 62 2 13 -15 60 -21 60 -7 0 4 -13 13 -30 19 -40 15 -38 25 10 45 l39 16 78 -39 c42 -21 86 -47 97 -58 22 -22 15 -57 -10 -47 -9 3 -12 2 -9 -5 4 -6 -3 -13 -15 -16 -18 -5 -20 -10 -14 -36 5 -25 4 -29 -10 -24 -10 4 -16 1 -16 -8 0 -11 -7 -12 -31 -5 -27 8 -32 6 -37 -12 -4 -18 -11 -20 -53 -16 -29 3 -49 1 -49 -5 0 -5 -15 -7 -34 -4 -22 4 -36 1 -39 -6 -3 -9 -7 -9 -19 0 -12 11 -17 8 -28 -12 -14 -26 -30 -33 -30 -14 0 8 -4 8 -13 0 -11 -8 -17 -5 -27 15 -11 19 -19 24 -36 20 -16 -4 -24 -1 -28 12 -7 19 -71 28 -82 11 -10 -16 -24 -10 -24 11 0 28 -14 25 -30 -8 -13 -26 -14 -26 -18 -5 -4 25 -18 31 -25 11 -3 -9 -10 -8 -26 6 -21 19 -22 19 -37 -3 -13 -17 -19 -20 -30 -10 -12 9 -14 5 -14 -29 0 -22 -3 -54 -6 -71 -6 -26 -4 -30 10 -25 11 4 21 -3 33 -25 12 -20 26 -31 41 -31 15 0 22 -6 22 -19 0 -10 7 -21 15 -25 9 -3 15 -19 15 -41 0 -40 14 -45 32 -13 l12 23 6 -25 c4 -16 12 -24 23 -22 11 2 24 -12 39 -43 26 -54 38 -57 38 -11 0 46 16 54 35 16 18 -35 31 -38 38 -10 9 34 25 23 57 -38 17 -31 44 -79 60 -107 17 -27 37 -64 44 -80 8 -17 24 -39 36 -50 54 -49 94 -183 88 -291 -6 -91 -49 -167 -158 -279 -47 -49 -89 -103 -107 -140 -27 -55 -33 -82 -59 -267 -4 -25 -15 -45 -32 -59 -26 -20 -26 -21 -8 -35 27 -19 56 -17 91 6 17 11 44 20 62 20 27 0 45 12 103 71 64 65 72 70 97 62 20 -7 29 -17 31 -36 3 -23 9 -28 44 -33 l40 -7 -8 -41 c-4 -23 -7 -58 -7 -78 1 -26 -3 -38 -13 -38 -17 0 -19 -27 -1 -33 9 -3 7 -15 -9 -48 -12 -24 -27 -69 -34 -99 -9 -34 -23 -63 -38 -75 -29 -25 -42 -80 -28 -119 8 -22 8 -30 -1 -33 -19 -7 -16 -60 4 -72 15 -8 15 -11 3 -27 -14 -16 -13 -17 15 -10 17 4 47 10 67 13 34 4 39 2 48 -21 11 -30 19 -32 46 -11 24 18 74 19 97 2 10 -6 20 -30 24 -52 5 -36 51 -95 74 -95 4 0 10 -22 14 -50 l7 -50 41 0 c32 0 38 3 29 12 -14 14 23 29 128 54 127 30 138 34 143 54 4 14 14 20 34 20 69 0 196 65 222 114 7 13 36 33 65 45 49 20 55 20 95 6 60 -21 65 -20 108 20 47 43 79 50 199 41 80 -5 94 -4 94 8 0 22 -97 90 -155 110 -27 9 -70 30 -94 46 -48 33 -57 35 -66 20 -3 -6 -52 -10 -110 -10 -57 0 -106 -4 -110 -10 -3 -5 0 -18 7 -29 22 -36 0 -34 -32 2 -33 39 -68 56 -79 39 -4 -7 -27 3 -61 24 -66 42 -75 42 -55 4 19 -37 19 -35 -7 -37 -13 -1 -34 8 -48 21 -35 31 -81 59 -87 52 -3 -3 2 -17 11 -30 70 -107 -125 61 -224 192 -38 52 -65 116 -54 134 8 12 48 10 63 -4 10 -11 12 -8 9 12 -2 17 -9 25 -22 25 -15 0 -23 14 -37 60 -34 114 -88 257 -103 274 -8 9 -20 39 -26 66 -14 67 -51 130 -75 130 -29 0 -44 104 -40 272 4 161 19 227 76 322 21 35 41 80 45 100 3 20 18 61 32 91 14 30 25 62 24 70 0 8 2 22 4 30 4 11 7 12 13 1 5 -8 22 16 49 73 45 93 58 131 53 160 -2 15 2 17 22 12 32 -8 75 25 79 62 2 19 8 26 20 24 12 -2 20 6 24 23 4 16 13 25 26 25 13 0 25 14 39 44 24 56 18 77 -22 69 -26 -5 -29 -3 -29 20 0 14 -12 39 -26 56 -36 43 -29 67 18 64 32 -2 37 1 36 20 -1 12 -8 30 -17 40 -14 17 -13 23 9 65 24 45 26 47 65 44 l40 -4 -24 26 -24 25 39 40 c22 21 38 44 36 49 -5 17 25 68 55 93 16 14 27 32 25 41 -2 11 6 22 22 29 14 7 23 16 20 20 -3 5 2 9 10 9 20 0 48 30 42 45 -2 7 8 19 22 26 15 7 29 14 31 16 2 1 -6 14 -18 29 l-22 28 33 11 c30 10 33 14 31 47 -2 28 2 38 15 42 13 5 18 18 20 53 1 25 8 56 17 69 10 16 13 37 9 71 -5 46 -5 47 11 28 16 -19 17 -15 10 74 -6 86 -5 94 9 82 24 -20 49 -6 42 23 -4 18 -2 25 10 28 13 2 9 13 -20 57 -20 29 -36 56 -36 60 0 3 5 -1 11 -9 9 -12 12 -12 16 -2 3 9 14 -1 30 -28 14 -23 27 -39 30 -36 12 11 -8 56 -37 83 -16 15 -30 37 -30 49 0 46 -69 148 -94 138 -6 -2 -19 9 -28 26 -9 16 -30 35 -45 42 -15 6 -35 20 -44 31 -12 15 -34 20 -112 24 -60 4 -97 2 -97 -4 0 -5 12 -20 26 -34 14 -13 30 -33 36 -44 6 -11 -12 1 -39 28 -49 47 -85 67 -45 24 12 -13 19 -27 15 -31 -4 -4 -3 -10 2 -14 19 -14 34 -50 28 -71 -4 -16 8 -34 52 -76 67 -64 63 -73 -6 -15 -52 44 -61 47 -78 26 -9 -11 -5 -19 21 -41 30 -25 31 -26 8 -20 -14 4 -38 9 -55 12 -26 5 -28 4 -17 -10 7 -8 37 -28 65 -42 71 -37 77 -45 36 -46 -18 -1 -45 -4 -59 -8 l-25 -7 23 -8 c12 -4 22 -11 22 -16 0 -17 -17 -19 -44 -4 -34 17 -36 17 -36 2 0 -7 14 -21 30 -30 40 -23 38 -32 -11 -47 -45 -14 -91 -7 -110 15 -10 12 -6 14 25 14 43 0 46 11 10 39 -41 32 -86 25 -89 -14 -1 -5 -5 -18 -9 -28 -11 -23 30 -70 42 -50 6 9 16 6 40 -9 17 -11 32 -24 32 -29 0 -13 -46 -22 -74 -15 -17 4 -28 1 -33 -9 -8 -13 -15 -13 -43 -4 -19 7 -40 16 -47 21 -15 12 -33 2 -33 -17 0 -17 30 -35 58 -35 11 0 25 -7 32 -15 11 -13 7 -15 -24 -15 -20 0 -36 4 -36 8 0 8 -14 16 -63 40 -4 2 -4 -3 -1 -12 7 -18 -21 -23 -31 -6 -3 6 -13 10 -22 10 -15 0 -14 -2 0 -18 22 -24 21 -35 -1 -23 -11 6 -36 16 -58 24 -21 8 -48 18 -59 22 -12 5 -7 -4 15 -24 32 -30 33 -31 10 -26 l-25 6 28 -25 c15 -14 35 -26 43 -26 8 0 19 -11 24 -25 5 -14 16 -25 25 -25 20 0 19 19 -2 42 -9 10 -11 18 -5 18 6 0 19 -12 30 -27 35 -51 35 -53 4 -53 -20 0 -46 13 -78 40 -27 22 -62 45 -79 49 -16 5 -24 10 -17 10 20 1 4 21 -17 21 -10 0 -24 7 -31 15 -7 8 -17 15 -23 15 -6 0 3 -15 21 -33 37 -38 41 -51 12 -42 -12 4 -20 2 -20 -6 0 -7 -8 -6 -22 5 -13 9 -45 27 -71 41 -26 14 -45 30 -42 35 3 5 0 12 -6 16 -8 4 -9 3 -5 -4 5 -8 -8 -11 -42 -8 -45 2 -49 1 -44 -17 6 -23 8 -24 -87 41 -44 30 -66 40 -71 32 -5 -8 -18 3 -38 31 -17 24 -49 62 -71 84 -35 33 -43 37 -52 25 -8 -13 -13 -11 -27 10 -9 13 -25 31 -35 40 -10 8 -35 31 -56 51 -20 19 -44 38 -54 42 -9 3 -17 11 -17 17 0 5 -5 10 -11 10 -8 0 -9 -11 -5 -31 5 -27 4 -30 -9 -19 -8 6 -22 9 -32 6 -14 -4 -23 6 -40 43 -13 26 -23 54 -23 61 0 6 -6 10 -14 7 -9 -4 -24 9 -41 35 -15 22 -29 38 -31 35 -3 -3 4 -32 16 -66 23 -70 26 -97 5 -61 -8 14 -14 19 -15 13 0 -22 -20 -14 -20 8 0 11 -5 30 -12 42 -12 22 -16 46 -21 107 -2 19 -13 57 -25 84 -28 62 -28 68 3 31 17 -21 23 -24 18 -10 -6 17 -5 18 10 7 8 -8 17 -23 19 -34 2 -12 9 -23 16 -26 11 -3 11 0 3 17 -6 12 -16 30 -22 42 -13 26 -5 36 16 19 12 -10 15 -10 16 1 1 8 3 29 5 47 3 28 -1 35 -29 52 -21 13 -26 20 -14 20 9 0 17 6 17 13 0 8 3 9 8 1 8 -13 42 -15 42 -2 0 4 -4 8 -8 8 -8 0 -32 43 -32 55 0 3 7 5 15 5 20 0 19 14 -1 31 -12 10 -15 23 -10 54 3 22 8 60 11 84 2 24 9 47 14 51 13 7 -7 71 -27 83 -20 13 -14 30 8 24 18 -4 20 -2 14 16 -3 12 -10 26 -15 32 -13 14 -11 25 5 25 8 0 17 14 21 30 6 26 9 29 22 19 12 -11 13 -9 9 12 -8 31 15 74 37 73 9 -1 17 2 17 7 0 5 -4 9 -10 9 -16 0 2 31 23 39 27 10 51 80 34 101 -7 8 -13 33 -12 55 0 38 2 40 33 43 38 4 41 14 7 32 -13 7 -22 17 -19 21 2 4 0 10 -6 14 -6 4 -8 11 -5 16 10 16 -5 48 -26 54 -26 9 -55 65 -38 75 8 6 3 13 -18 24 -17 8 -33 22 -36 30 -3 8 -22 18 -41 21 -19 4 -53 23 -75 42 -23 19 -46 31 -52 28 -5 -4 -18 -1 -27 7 -37 31 -127 47 -199 36 -40 -7 -68 -27 -54 -41 12 -12 44 -1 36 13 -3 5 -2 10 2 10 5 0 14 -9 21 -21 11 -17 21 -20 52 -17 33 3 40 0 45 -19 4 -18 10 -21 39 -16 19 3 36 1 40 -6 4 -6 18 -11 31 -11 15 0 31 -10 41 -25 9 -14 37 -34 61 -45 39 -17 44 -22 33 -35 -10 -13 -10 -19 4 -33 9 -10 16 -34 16 -52 0 -32 0 -33 -20 -15 -17 16 -20 16 -20 2 0 -8 12 -24 27 -35 23 -17 27 -28 31 -84 4 -64 4 -64 -39 -111 -35 -38 -53 -49 -89 -55 -38 -7 -45 -11 -42 -29 4 -31 -26 -38 -34 -7 -5 19 -11 23 -26 18 -13 -4 -24 1 -34 13 -12 15 -15 16 -23 3 -16 -24 -21 -6 -9 34 7 23 21 42 35 49 13 6 23 18 23 28 0 10 7 22 16 27 14 8 14 10 0 21 -9 8 -16 22 -16 33 0 11 -5 18 -12 16 -20 -7 -44 25 -39 51 3 17 0 22 -8 17 -7 -4 -18 -2 -26 4 -12 10 -15 9 -15 -3 0 -11 -9 -13 -43 -10 -44 5 -107 -17 -107 -37 0 -6 -7 -10 -15 -10 -17 0 -60 -25 -86 -50 -9 -9 -16 -35 -17 -65 -1 -31 -7 -54 -17 -61 -8 -6 -15 -19 -15 -28 0 -19 -4 -20 -31 -6 -15 8 -19 7 -19 -5 0 -8 5 -15 10 -15 6 0 10 -7 10 -15 0 -22 -27 -19 -40 4 -13 25 -13 77 1 102 7 15 16 18 32 13 20 -6 20 -5 -6 29 -15 20 -27 40 -27 45 0 5 -10 13 -23 19 -17 8 -28 7 -42 -4 -12 -8 -16 -17 -10 -21 6 -4 10 -19 10 -34 -1 -16 2 -28 6 -28 15 0 21 -62 8 -71 -11 -8 -11 -11 4 -20 16 -9 14 -13 -18 -35 -20 -13 -38 -24 -40 -24 -12 0 -3 24 13 37 15 13 15 14 0 10 -9 -3 -24 1 -35 8 -17 14 -17 14 4 15 16 0 20 5 16 20 -3 11 -13 20 -22 20 -11 0 -5 10 19 30 33 29 33 30 9 25 -21 -5 -33 2 -71 43 -25 26 -55 69 -66 95 l-20 46 -61 -2 c-41 -1 -61 -6 -61 -14 0 -7 -4 -13 -10 -13 -5 0 -10 -9 -10 -20 0 -11 -7 -20 -16 -20 -8 0 -24 -4 -34 -10 -16 -9 -17 -14 -7 -46 6 -21 19 -39 29 -42 14 -3 18 -14 18 -44 0 -35 2 -40 20 -35 21 5 29 -16 10 -28 -6 -3 -10 -17 -10 -31 0 -29 -6 -30 -45 -4 -16 11 -38 20 -47 20 -24 0 -78 49 -78 71 0 10 -5 19 -10 19 -15 0 -2 -98 19 -140 10 -19 21 -47 24 -62 4 -15 15 -34 24 -44 10 -9 18 -23 19 -32 2 -8 13 -26 26 -41 38 -42 68 -89 68 -105 0 -8 4 -17 9 -20 10 -6 3 -44 -10 -62 -5 -6 -1 -35 7 -67 20 -69 14 -97 -27 -120 -42 -23 -31 -32 16 -13 21 9 39 16 41 16 9 0 -36 -63 -55 -76 -31 -22 -26 -34 11 -23 41 11 48 11 48 -1 0 -5 -6 -10 -13 -10 -8 0 -23 -10 -35 -23 -25 -26 -30 -46 -7 -27 8 6 22 10 32 7 15 -3 13 -6 -9 -15 -28 -12 -40 -29 -13 -19 23 9 29 -13 9 -29 -18 -14 -18 -15 5 -8 34 9 19 -10 -29 -37 -25 -14 -36 -25 -29 -30 14 -8 75 26 88 49 5 9 13 14 17 10 6 -7 -24 -37 -93 -98 -31 -26 -29 -40 5 -31 15 4 26 12 25 17 -2 9 26 29 32 22 2 -1 -6 -30 -17 -63 -23 -68 -54 -108 -67 -87 -6 10 -11 8 -23 -5 -26 -31 -31 -43 -12 -36 14 5 15 3 4 -18 -7 -13 -23 -36 -37 -50 -21 -23 -22 -27 -8 -33 22 -8 35 1 35 23 0 9 7 26 15 37 14 18 14 18 15 -5 0 -24 -12 -68 -32 -121 -7 -17 -6 -26 1 -28 17 -6 13 -35 -12 -84 -22 -43 -22 -53 1 -88 2 -3 10 4 18 14 12 18 14 18 14 3 0 -10 -7 -20 -15 -23 -20 -8 -19 -20 2 -28 10 -4 26 2 42 18 28 26 33 27 261 45 165 13 185 8 158 -42 -30 -58 -88 -112 -172 -162 -134 -80 -144 -87 -189 -147 -23 -30 -66 -86 -95 -124 -29 -38 -86 -98 -125 -135 -69 -64 -119 -129 -98 -129 5 0 21 14 35 32 13 18 42 42 63 53 22 12 44 35 52 53 9 20 20 31 28 28 7 -3 28 8 47 24 26 23 43 30 75 30 22 0 41 -4 41 -10 0 -5 -15 -26 -32 -46 -30 -34 -31 -36 -9 -29 20 6 22 5 16 -15 -7 -21 -5 -23 26 -16 18 3 45 17 59 30 17 17 40 25 73 28 74 5 59 -17 -33 -48 -30 -11 -77 -31 -103 -47 -26 -15 -58 -27 -71 -27 -13 0 -31 -10 -42 -21 l-19 -21 33 7 c31 6 32 6 22 -19 -10 -28 0 -35 19 -12 7 8 26 17 44 20 19 4 48 21 67 41 35 36 70 46 70 21 0 -8 -7 -16 -15 -20 -8 -3 -15 -12 -15 -21 0 -9 -19 -24 -42 -35 -24 -11 -39 -20 -33 -20 19 0 -98 -108 -137 -126 -26 -12 -44 -28 -53 -50 -14 -34 -102 -106 -121 -100 -6 2 -15 -4 -21 -14 -7 -11 -30 -20 -62 -24 -39 -5 -55 -13 -68 -32 -22 -34 -53 -131 -53 -166 0 -16 -6 -31 -15 -34 -9 -4 -15 -19 -15 -36 0 -69 -128 -199 -181 -183 -24 8 -89 133 -89 173 0 20 17 49 59 98 92 110 263 402 296 507 18 58 42 108 70 145 48 66 90 137 100 170 5 17 2 22 -12 22 -45 1 -47 13 -19 105 15 47 24 94 21 104 -10 33 12 71 45 77 16 4 30 12 31 18 0 6 4 1 9 -11 9 -22 40 -33 40 -13 0 23 -25 60 -37 56 -17 -7 -33 26 -26 54 4 17 2 21 -8 18 -13 -4 -20 -23 -34 -90 l-5 -28 -35 17 c-19 9 -37 25 -41 35 -6 20 11 114 31 168 37 100 44 163 15 145 -13 -8 -13 -5 1 34 6 17 8 44 4 59 l-7 27 -55 -60 c-55 -60 -55 -60 -22 -12 64 92 66 97 48 110 -9 7 -19 24 -22 38 -4 15 -15 37 -25 50 l-19 23 -42 -35 c-39 -32 -56 -60 -130 -219 -6 -14 -9 -16 -6 -5 4 11 13 44 21 74 13 52 45 106 127 218 37 50 38 53 24 81 -7 16 -12 42 -10 58 2 16 0 35 -5 42 -17 26 -73 -17 -227 -172 -82 -83 -134 -131 -115 -107 46 59 188 203 275 279 53 46 67 63 59 72 -9 9 -18 9 -35 0 -23 -11 -23 -11 -4 5 48 37 55 46 55 67 0 34 -14 50 -36 42 -16 -5 -15 -3 4 13 22 18 23 25 23 151 -1 73 -5 141 -10 151 -6 9 -13 42 -16 72 -4 30 -11 65 -16 79 -5 14 -11 54 -13 90 -8 121 -39 64 -60 -112 -4 -32 -11 -60 -16 -63 -5 -3 -23 7 -40 22 -37 33 -49 80 -50 195 0 42 -3 84 -6 92 -6 15 -8 15 -20 -1 -12 -16 -14 -14 -15 22 -1 51 -13 93 -22 79 -3 -6 -9 -66 -13 -133 -4 -67 -11 -124 -16 -127 -4 -3 -8 -27 -8 -53 0 -27 -9 -86 -21 -132 -12 -46 -25 -113 -29 -148 -6 -43 -15 -71 -29 -84 -11 -12 -23 -18 -27 -15 -3 4 -8 35 -11 70 -5 65 -74 281 -98 308 -8 8 -15 38 -17 65 -3 47 -5 50 -33 53 -24 3 -36 14 -64 60 -19 31 -49 72 -67 92 -17 19 -46 61 -64 93 -19 34 -39 57 -49 57 -23 0 -47 45 -55 102 -5 34 -12 49 -27 53 -10 4 -22 18 -25 33 -4 15 -13 37 -21 50 -11 19 -12 33 -3 76 7 37 8 61 0 80 -5 15 -10 46 -10 69 0 24 -7 52 -15 64 -9 12 -17 53 -19 98 -5 76 -4 79 39 164 25 47 72 128 105 180 35 55 58 101 55 111 -3 10 7 28 25 45 26 25 30 36 30 82 0 50 2 55 31 71 100 55 159 95 217 148 202 184 297 276 303 292 4 9 34 33 68 52 34 19 72 42 84 51 l21 17 -27 35 c-23 31 -24 35 -9 41 9 3 24 9 32 14 8 5 28 15 44 23 20 9 33 25 38 46 5 18 17 37 28 43 11 6 17 16 13 26 -3 8 1 22 10 32 8 9 20 35 26 57 8 31 8 44 -5 62 -14 22 -13 24 11 33 15 6 34 10 44 10 26 0 31 9 21 35 -7 20 -4 30 20 55 22 23 28 38 24 55 -4 17 0 26 16 35 17 9 20 17 15 37 -7 31 6 42 50 43 l30 1 -27 20 c-30 22 -36 39 -14 39 7 0 20 10 28 21 12 19 24 22 95 23 77 1 82 2 77 21 -4 16 6 25 48 48 29 15 62 27 73 27 11 0 33 7 49 15 15 8 39 12 52 9 16 -4 26 -1 30 9 6 17 83 15 212 -5 55 -8 76 -8 84 0 7 7 25 12 40 12 15 0 37 9 48 20 11 11 33 20 48 20 15 0 72 6 127 13 60 9 112 11 130 6 17 -5 50 -8 75 -8 25 0 89 -2 143 -6 69 -4 103 -2 113 6 7 6 21 8 30 5 9 -3 20 -3 26 0 24 15 545 25 636 13 35 -5 91 -11 125 -13 34 -2 100 -11 145 -20 l84 -15 50 25 c27 14 60 27 72 28 11 1 34 6 50 11 20 5 31 4 36 -6 7 -10 14 -10 35 -1 15 7 31 10 36 6 5 -3 49 -12 96 -20 75 -12 95 -12 150 1 78 18 73 18 73 0 0 -16 49 -45 78 -45 10 0 22 -4 28 -9 5 -4 50 -21 99 -37 50 -15 92 -32 93 -36 2 -4 8 -8 13 -8 6 0 31 -10 57 -21 41 -19 58 -21 144 -16 77 5 103 3 128 -10 48 -23 93 -11 174 48 99 73 115 78 157 48 19 -13 38 -30 42 -37 7 -10 22 -12 57 -7 43 6 50 4 60 -15 11 -21 16 -22 87 -15 l74 7 -16 -31 c-20 -39 -14 -49 35 -57 26 -4 40 -12 43 -25 3 -11 12 -19 21 -19 9 0 16 -4 16 -10 0 -5 16 -16 35 -24 19 -8 35 -19 35 -24 0 -5 43 -31 96 -57 53 -27 98 -54 101 -61 2 -7 26 -31 53 -53 39 -33 71 -48 147 -71 160 -47 262 -71 359 -85 95 -13 144 -35 144 -64 0 -10 20 -32 45 -50 25 -18 45 -40 45 -50 0 -9 14 -27 30 -39 17 -13 30 -31 30 -41 0 -11 20 -30 50 -49 27 -17 50 -36 50 -42 0 -6 20 -19 45 -30 25 -11 45 -25 45 -33 0 -17 78 -82 121 -100 19 -8 71 -42 115 -75 78 -58 81 -62 80 -98 -1 -38 0 -39 90 -94 158 -96 270 -187 314 -254 44 -68 50 -78 72 -121 8 -16 25 -47 38 -68 l23 -38 -28 7 c-26 6 -27 5 -21 -23 10 -42 -14 -61 -56 -45 -26 10 -29 9 -23 -6 22 -54 24 -73 7 -78 -59 -19 -75 -30 -84 -55 -6 -18 -15 -26 -24 -22 -9 3 -14 -2 -14 -15 0 -11 -7 -25 -15 -32 -8 -7 -15 -25 -15 -41 0 -53 -41 -160 -65 -169 -12 -5 -25 -18 -28 -29 -7 -30 -25 -26 -33 7 -7 29 -31 62 -46 62 -13 0 0 -39 23 -72 25 -35 24 -82 -2 -124 -25 -41 -32 -42 -54 -8 -23 36 -30 23 -15 -31 12 -44 12 -46 -14 -70 -14 -13 -26 -29 -26 -35 0 -7 -8 -9 -19 -5 -16 5 -18 2 -14 -15 6 -22 -7 -26 -25 -8 -8 8 -14 5 -22 -16 -5 -15 -10 -33 -10 -39 -1 -19 -35 -55 -56 -58 -10 -2 -26 -19 -36 -37 -9 -19 -19 -29 -23 -23 -11 19 -27 12 -20 -8 9 -29 -12 -35 -29 -9 -17 28 -36 115 -36 167 0 20 -7 46 -16 57 -12 15 -15 16 -10 2 2 -9 8 -33 12 -52 5 -29 3 -36 -9 -36 -9 0 -19 5 -22 10 -5 8 -12 7 -23 -2 -12 -10 -16 -10 -19 0 -8 21 -23 13 -23 -13 0 -14 -8 -29 -20 -35 -16 -9 -20 -8 -20 5 0 30 -25 9 -28 -24 -3 -26 -7 -31 -23 -26 -16 5 -19 1 -19 -24 0 -17 -4 -31 -10 -31 -5 0 -10 11 -10 24 0 14 -4 28 -10 31 -13 8 -13 -5 6 -89 25 -119 38 -151 56 -148 9 1 26 -10 38 -25 18 -23 21 -36 16 -72 -5 -37 -2 -48 19 -73 29 -34 28 -44 -16 -126 -16 -29 -29 -62 -29 -75 0 -12 -7 -35 -15 -50 -8 -15 -12 -34 -8 -43 3 -8 -2 -23 -11 -33 -11 -12 -18 -51 -24 -137 -5 -65 -7 -124 -5 -130 2 -6 15 -16 29 -22 13 -7 24 -20 24 -30 0 -11 9 -27 20 -37 12 -11 20 -31 20 -50 0 -32 23 -61 65 -83 11 -6 31 -31 44 -56 23 -45 23 -47 6 -60 -18 -13 -18 -15 -2 -37 58 -81 98 -149 155 -267 l65 -134 187 -192 c135 -139 203 -218 248 -287 61 -93 100 -129 140 -129 11 0 49 7 84 16 52 14 92 15 236 10 159 -7 180 -10 255 -37 45 -17 131 -44 190 -60 129 -35 222 -80 296 -144 30 -26 81 -65 113 -86 67 -46 90 -76 86 -114 -3 -24 5 -31 71 -67 41 -23 149 -66 245 -96 l171 -55 200 -5 c109 -2 228 -1 262 3 l63 7 0 3419 0 3419 -6400 0 -6400 0 0 -4800z m9720 1017 c0 -5 -4 -5 -10 -2 -5 3 -10 14 -10 23 0 15 2 15 10 2 5 -8 10 -19 10 -23z m50 -123 c0 -8 -4 -14 -10 -14 -5 0 -10 9 -10 21 0 11 5 17 10 14 6 -3 10 -13 10 -21z m-4621 -68 c-1 -53 -20 -121 -34 -121 -14 0 -16 147 -3 179 11 27 12 27 25 10 7 -10 13 -41 12 -68z m4591 20 c-13 -13 -30 9 -30 39 1 19 2 18 21 -4 14 -17 17 -27 9 -35z m-50 -52 c0 -21 -4 -33 -10 -29 -5 3 -10 19 -10 36 0 16 5 29 10 29 6 0 10 -16 10 -36z m-70 -60 c0 -8 -4 -12 -10 -9 -5 3 -10 10 -10 16 0 5 5 9 10 9 6 0 10 -7 10 -16z m-5575 -205 c34 -20 44 -48 20 -55 -16 -5 -79 52 -71 64 9 15 9 16 51 -9z m575 -24 c22 -20 80 -19 110 3 30 21 103 24 95 4 -3 -8 -5 -16 -5 -18 0 -2 -9 -4 -20 -4 -13 0 -20 -7 -20 -20 0 -22 -10 -26 -27 -9 -9 9 -14 5 -18 -14 -4 -16 -12 -24 -20 -21 -7 3 -15 -3 -18 -13 -3 -14 -7 -11 -18 15 -15 37 -15 37 -63 17 -36 -15 -37 -15 -61 11 -27 29 -28 34 -19 59 8 20 57 14 84 -10z m-1194 -116 c-5 -42 -4 -59 3 -54 6 3 11 15 11 26 0 11 5 17 10 14 11 -7 6 -57 -13 -115 -6 -19 -11 -39 -12 -45 0 -5 -7 -22 -14 -37 -11 -21 -11 -31 -2 -41 9 -11 7 -20 -9 -38 l-21 -24 5 40 c3 22 8 103 12 180 7 146 8 155 26 155 7 0 8 -20 4 -61z m1464 -14 c0 -12 -29 -3 -34 10 -4 13 -2 14 14 5 11 -6 20 -13 20 -15z m-35 -35 c16 -17 16 -20 3 -15 -9 4 -19 13 -22 21 -7 19 -2 18 19 -6z m135 -11 c0 -6 -4 -7 -10 -4 -5 3 -10 11 -10 16 0 6 5 7 10 4 6 -3 10 -11 10 -16z m-1800 -208 c0 -72 18 -110 60 -121 31 -9 37 -7 52 13 10 12 18 25 18 30 0 4 5 7 11 7 6 0 8 -9 4 -22 -6 -18 -4 -20 11 -13 11 4 28 10 39 13 19 5 19 4 3 -14 -20 -22 -61 -115 -54 -121 3 -3 31 9 63 26 59 32 53 27 -39 -38 -50 -35 -67 -59 -137 -196 -22 -42 -31 -50 -57 -53 -50 -5 -51 20 -16 311 6 51 5 72 -4 85 -19 24 -17 107 2 144 16 30 17 31 30 13 8 -10 14 -39 14 -64z m936 -159 c-8 -13 -96 -65 -102 -60 -4 5 99 75 105 72 1 -2 0 -7 -3 -12z m23 -41 c-13 -11 -28 -20 -34 -20 -5 0 3 11 20 24 16 13 31 22 34 20 2 -3 -7 -13 -20 -24z m428 -43 c-3 -8 -6 -5 -6 6 -1 11 2 17 5 13 3 -3 4 -12 1 -19z m-167 -27 c0 -6 -4 -13 -10 -16 -5 -3 -10 1 -10 9 0 9 5 16 10 16 6 0 10 -4 10 -9z m40 -1 c0 -5 -9 -14 -21 -20 -19 -11 -20 -10 -9 9 11 22 30 29 30 11z m-317 -30 c-9 -16 -21 -30 -26 -30 -9 0 -1 17 21 43 23 26 25 21 5 -13z m467 15 c0 -8 -4 -15 -10 -15 -5 0 -7 7 -4 15 4 8 8 15 10 15 2 0 4 -7 4 -15z m-130 -19 c0 -7 -7 -19 -15 -26 -16 -13 -20 -3 -9 24 8 20 24 21 24 2z m310 -36 c0 -13 -1 -13 -10 0 -5 8 -10 22 -10 30 0 13 1 13 10 0 5 -8 10 -22 10 -30z m-715 10 c3 -5 -1 -24 -10 -41 -9 -17 -12 -28 -6 -25 6 4 11 5 11 2 0 -14 -49 -114 -59 -120 -16 -10 -14 6 10 60 11 27 16 50 11 52 -6 2 -3 20 6 42 17 40 26 48 37 30z m128 -101 c-14 -52 -21 -20 -9 41 9 47 11 50 14 24 2 -18 -1 -47 -5 -65z m57 64 c0 -19 -3 -24 -10 -17 -6 6 -8 18 -4 27 9 24 14 21 14 -10z m660 -13 c0 -13 -1 -13 -10 0 -5 8 -10 22 -10 30 0 13 1 13 10 0 5 -8 10 -22 10 -30z m-820 16 c0 -3 -4 -8 -10 -11 -5 -3 -10 -1 -10 4 0 6 5 11 10 11 6 0 10 -2 10 -4z m630 -18 c0 -13 -4 -29 -8 -36 -5 -8 -8 1 -8 23 1 19 4 35 9 35 4 0 7 -10 7 -22z m30 -8 c0 -11 -4 -20 -9 -20 -5 0 -7 9 -4 20 3 11 7 20 9 20 2 0 4 -9 4 -20z m67 -52 c-3 -8 -6 -5 -6 6 -1 11 2 17 5 13 3 -3 4 -12 1 -19z m70 -40 c-3 -8 -6 -5 -6 6 -1 11 2 17 5 13 3 -3 4 -12 1 -19z m-117 2 c0 -5 -5 -10 -11 -10 -5 0 -7 5 -4 10 3 6 8 10 11 10 2 0 4 -4 4 -10z m230 -50 c0 -13 -1 -13 -10 0 -5 8 -10 22 -10 30 0 13 1 13 10 0 5 -8 10 -22 10 -30z m130 -39 c-1 -12 -5 -11 -20 8 -11 13 -20 31 -20 40 1 12 5 11 20 -8 11 -13 20 -31 20 -40z m181 -98 c13 -16 12 -17 -3 -4 -10 7 -18 15 -18 17 0 8 8 3 21 -13z m147 -141 c2 -7 -3 -12 -12 -12 -9 0 -16 7 -16 16 0 17 22 14 28 -4z m73 -49 c18 -21 22 -28 8 -15 -14 12 -28 20 -31 17 -3 -3 -8 3 -11 15 -3 11 -4 20 -3 20 1 0 18 -17 37 -37z m-7 -38 c3 -8 1 -15 -4 -15 -6 0 -10 7 -10 15 0 8 2 15 4 15 2 0 6 -7 10 -15z m182 -83 c-6 -5 -76 51 -76 61 0 4 18 -7 40 -24 22 -18 38 -34 36 -37z m834 19 c19 -16 32 -32 29 -35 -3 -3 -23 10 -45 29 -47 41 -33 47 16 6z m-730 9 c12 -8 11 -10 -7 -10 -13 0 -23 5 -23 10 0 13 11 13 30 0z m-447 -60 c7 -27 2 -63 -23 -166 l-32 -132 16 -71 c22 -95 16 -115 -32 -118 -20 -1 -66 -5 -102 -8 -53 -6 -63 -5 -53 6 7 7 33 13 58 14 49 1 82 23 72 49 -10 25 -27 19 -27 -9 0 -28 -12 -32 -30 -10 -16 20 -36 19 -44 -1 -4 -10 -12 -14 -23 -10 -10 4 -27 -4 -47 -22 -17 -16 -54 -37 -82 -47 -41 -15 -50 -22 -46 -37 6 -26 82 -58 139 -59 l48 0 -75 -19 c-41 -10 -138 -28 -215 -40 -77 -12 -163 -26 -190 -32 -50 -10 -135 3 -135 21 0 4 13 20 29 35 l28 27 17 -26 c9 -14 24 -25 33 -25 13 0 14 3 5 12 -18 18 -14 45 9 61 13 9 16 17 10 22 -19 11 -12 25 14 25 16 0 25 -6 25 -15 0 -19 5 -19 34 0 18 12 29 13 55 4 41 -14 81 -4 81 20 0 10 -7 22 -16 25 -28 11 -91 6 -103 -8 -16 -19 -149 -15 -162 5 -5 7 -9 9 -9 3 0 -6 -18 -14 -40 -18 -30 -5 -47 -1 -72 14 -18 11 -42 20 -54 20 -25 0 -44 21 -73 77 -12 23 -30 47 -42 53 -33 18 -90 148 -91 208 -2 94 42 105 119 30 33 -32 40 -45 31 -55 -13 -16 3 -39 34 -49 11 -3 17 -12 14 -20 -3 -8 1 -14 10 -14 8 0 29 -7 46 -16 l32 -16 -32 -14 c-18 -7 -32 -16 -32 -20 0 -7 54 0 85 12 17 7 18 6 7 -8 -10 -13 -10 -17 5 -24 10 -4 32 -9 48 -10 51 -4 90 -18 90 -34 0 -26 45 -24 75 5 31 30 117 65 159 65 89 0 193 79 282 215 113 173 107 167 136 163 21 -2 29 -10 36 -38z m564 -10 c7 0 15 -4 18 -10 14 -23 -18 -8 -48 23 -18 17 -21 22 -7 10 14 -13 31 -23 37 -23z m383 9 c0 -5 -7 -9 -15 -9 -9 0 -15 9 -15 21 0 18 2 19 15 9 8 -7 15 -16 15 -21z m-426 -35 c9 -20 15 -38 13 -41 -7 -6 -47 48 -47 63 0 25 16 14 34 -22z m243 -3 c3 -11 13 -21 24 -24 22 -6 26 -37 5 -37 -16 0 -27 14 -44 53 -8 18 -8 27 -1 27 6 0 13 -9 16 -19z m-307 -11 c20 -31 11 -44 -13 -22 -13 12 -29 22 -35 22 -7 0 -12 4 -12 8 0 14 50 7 60 -8z m283 -65 c-5 -25 -6 -25 -35 -7 -25 15 -30 15 -38 2 -8 -12 -10 -11 -10 6 0 17 5 20 29 16 16 -2 32 1 36 7 11 19 23 3 18 -24z m-277 0 c10 -8 14 -15 7 -15 -19 0 -43 11 -43 21 0 13 14 11 36 -6z m630 -101 c-4 -9 -9 -15 -11 -12 -3 3 -3 13 1 22 4 9 9 15 11 12 3 -3 3 -13 -1 -22z m127 -5 c15 12 27 5 27 -16 0 -10 -7 -12 -19 -8 -11 3 -22 1 -26 -5 -3 -5 -23 -10 -43 -9 -31 0 -34 2 -15 9 16 7 20 14 16 31 -6 21 -5 21 21 4 19 -13 29 -15 39 -6z m-2593 -139 c0 -5 -5 -10 -11 -10 -5 0 -7 5 -4 10 3 6 8 10 11 10 2 0 4 -4 4 -10z m2520 -10 c0 -12 -28 -25 -36 -17 -9 9 6 27 22 27 8 0 14 -5 14 -10z m-360 -120 c0 -5 -8 -10 -17 -10 -15 0 -16 2 -3 10 19 12 20 12 20 0z m255 -10 c-3 -5 -10 -10 -16 -10 -5 0 -9 5 -9 10 0 6 7 10 16 10 8 0 12 -4 9 -10z m-1248 -82 c-3 -8 -6 -5 -6 6 -1 11 2 17 5 13 3 -3 4 -12 1 -19z m473 -46 c0 -12 -23 -32 -37 -32 -11 0 -11 4 -1 20 13 20 38 28 38 12z m-376 -41 c7 -10 -42 -33 -55 -25 -14 8 10 34 32 34 10 0 20 -4 23 -9z m101 -35 c-16 -23 -15 -25 9 -41 17 -12 35 -15 56 -10 26 6 31 3 40 -20 13 -34 13 -48 0 -40 -6 4 -8 11 -5 16 10 15 -22 10 -35 -6 -14 -17 -50 -4 -50 18 0 15 -51 47 -74 47 -9 0 -16 6 -16 14 0 21 24 36 48 29 13 -3 22 -1 22 6 0 6 5 11 10 11 6 0 3 -11 -5 -24z m296 -6 c13 0 19 -7 19 -23 0 -36 -8 -37 -40 -5 -34 33 -38 43 -14 34 9 -3 24 -6 35 -6z m-1672 -80 c-21 -38 -49 -60 -49 -39 0 17 29 67 42 72 27 11 29 6 7 -33z m751 -14 c0 -16 -18 -31 -27 -22 -8 8 5 36 17 36 5 0 10 -6 10 -14z m770 -51 c0 -8 -6 -15 -14 -15 -17 0 -28 14 -19 24 12 12 33 6 33 -9z m-89 -92 c13 -16 12 -17 -3 -4 -10 7 -18 15 -18 17 0 8 8 3 21 -13z m-1001 3 c0 -3 -4 -8 -10 -11 -5 -3 -10 -1 -10 4 0 6 5 11 10 11 6 0 10 -2 10 -4z m765 -6 c3 -5 2 -10 -4 -10 -5 0 -13 5 -16 10 -3 6 -2 10 4 10 5 0 13 -4 16 -10z m65 -30 c0 -5 -2 -10 -4 -10 -3 0 -8 5 -11 10 -3 6 -1 10 4 10 6 0 11 -4 11 -10z m1210 -20 c-33 -26 -48 -26 -25 0 10 11 25 20 34 20 12 0 9 -6 -9 -20z m-2850 -20 c0 -5 -5 -10 -11 -10 -5 0 -7 5 -4 10 3 6 8 10 11 10 2 0 4 -4 4 -10z m793 -30 c-25 -43 -62 -87 -63 -74 0 16 58 104 70 104 7 0 3 -13 -7 -30z m416 -73 c1 -19 -3 -26 -11 -21 -8 5 -9 2 -5 -9 4 -10 1 -29 -5 -43 -10 -20 -9 -30 5 -54 39 -66 3 -35 -58 50 -31 43 -48 40 -19 -5 9 -14 14 -28 11 -31 -4 -5 -67 80 -67 92 0 2 7 4 15 4 13 0 15 8 10 38 -4 24 -3 32 3 23 6 -8 12 -9 16 -3 9 15 44 11 50 -5 9 -22 25 -14 33 15 l7 27 7 -25 c4 -14 7 -38 8 -53z m1251 -19 c0 -6 -6 -5 -15 2 -8 7 -15 14 -15 16 0 2 7 1 15 -2 8 -4 15 -11 15 -16z m970 -2358 c19 -17 31 -30 26 -30 -14 0 -86 43 -86 52 0 16 26 7 60 -22z"/> <path d="M4922 3428 c-26 -26 -14 -39 31 -32 53 8 57 12 33 30 -24 17 -48 18 -64 2z"/> <path d="M4742 2780 c0 -14 2 -19 5 -12 2 6 2 18 0 25 -3 6 -5 1 -5 -13z"/> <path d="M7883 7873 c-18 -4 -21 -11 -21 -48 1 -38 4 -44 26 -50 18 -4 34 0 53 14 16 12 38 21 51 21 19 0 20 2 8 10 -8 5 -32 10 -52 10 -36 0 -49 12 -28 25 6 3 10 10 10 16 0 9 -8 10 -47 2z"/> <path d="M7870 7739 c0 -11 10 -29 23 -39 30 -24 15 -37 -19 -16 -20 14 -37 16 -74 11 -46 -6 -47 -7 -36 -30 6 -13 22 -27 34 -30 19 -5 21 -9 12 -20 -9 -11 -6 -17 15 -31 l27 -18 -23 -8 c-23 -9 -23 -9 9 -28 37 -23 42 -41 10 -33 -18 4 -20 3 -12 -6 6 -7 30 -15 53 -18 22 -3 46 -12 53 -21 12 -13 10 -14 -12 -3 l-25 12 24 -20 c12 -12 27 -19 31 -16 15 9 22 -15 10 -30 -9 -11 -4 -24 24 -61 31 -41 43 -64 33 -64 -2 0 -18 11 -37 25 -57 42 -65 29 -19 -27 23 -29 51 -61 62 -71 11 -10 17 -20 14 -23 -3 -4 -13 1 -22 10 -35 34 -47 14 -15 -26 10 -14 17 -27 15 -30 -6 -5 12 -64 44 -148 25 -66 26 -72 4 -68 -14 2 -18 -4 -18 -32 0 -31 3 -35 30 -38 39 -5 41 -9 37 -77 -4 -55 -4 -56 -23 -39 -27 24 -35 10 -13 -23 22 -34 31 -125 10 -102 -7 8 -18 33 -25 57 -21 73 -33 50 -24 -45 4 -49 7 -106 6 -128 -1 -22 -1 -53 0 -70 2 -64 2 -224 0 -295 -5 -180 0 -285 14 -306 13 -20 12 -26 -2 -53 -14 -28 -14 -34 0 -67 8 -19 14 -45 14 -57 0 -12 3 -43 7 -69 4 -32 3 -53 -6 -63 -10 -12 -6 -21 19 -52 23 -28 31 -48 31 -78 0 -23 5 -46 12 -53 12 -12 18 -163 6 -155 -14 8 -9 -22 7 -43 8 -10 15 -31 15 -46 0 -15 5 -30 11 -33 6 -4 7 -19 4 -35 -5 -24 -1 -31 30 -50 37 -23 43 -40 7 -21 -30 16 -31 14 -19 -19 10 -25 9 -33 -3 -40 -12 -8 -11 -13 7 -32 27 -29 43 -70 43 -111 0 -25 -3 -29 -11 -17 -12 17 -7 -99 5 -118 4 -7 1 -12 -9 -12 -29 0 2 -38 48 -58 26 -12 59 -41 87 -75 45 -56 65 -71 53 -41 -12 32 29 9 48 -26 93 -177 104 -212 99 -294 -1 -21 -30 -47 -43 -39 -5 3 -13 17 -18 31 -10 26 -11 26 -24 8 -8 -10 -16 -24 -18 -30 -3 -7 -11 -5 -24 6 -25 22 -33 23 -33 1 0 -13 -6 -11 -28 10 -35 34 -44 34 -36 2 4 -15 1 -32 -6 -40 -11 -13 -3 -25 51 -75 62 -59 76 -94 20 -52 l-29 21 -5 -21 c-4 -14 2 -31 19 -50 28 -34 29 -41 10 -57 -10 -8 -31 -10 -63 -5 -47 7 -48 6 -30 -13 30 -34 20 -38 -40 -16 -9 3 -13 -4 -13 -22 0 -20 4 -26 15 -22 9 4 23 -5 36 -24 l21 -30 82 7 82 6 -4 -36 c-3 -24 0 -36 8 -36 7 0 10 -7 6 -17 -4 -9 -1 -20 6 -25 8 -5 2 -8 -18 -8 -35 0 -54 -16 -54 -45 0 -12 -11 -36 -24 -54 -14 -18 -22 -39 -19 -47 3 -8 -7 -30 -22 -50 -31 -40 -24 -57 21 -52 22 2 30 10 37 34 6 22 18 34 42 43 25 9 37 22 50 53 9 23 23 47 32 53 13 9 13 16 4 33 -10 19 -9 22 9 22 19 0 41 27 95 115 28 46 122 138 133 131 22 -13 106 86 157 185 20 37 19 42 -5 34 -18 -6 -20 -2 -20 53 0 33 7 79 16 103 8 24 13 59 10 77 -6 37 3 102 14 102 4 0 16 -12 26 -27 25 -38 23 -121 -4 -166 -18 -31 -19 -39 -8 -71 7 -20 20 -42 29 -49 18 -13 106 -234 138 -347 22 -79 25 -138 6 -122 -7 6 -32 51 -56 99 -89 182 -82 174 -124 140 l-29 -22 31 -38 c16 -21 28 -44 25 -52 -3 -7 -1 -16 5 -20 8 -4 8 -12 0 -27 -8 -16 -8 -31 0 -59 8 -26 8 -45 2 -57 -7 -13 -4 -23 15 -41 l24 -23 -30 -53 c-17 -30 -30 -57 -28 -62 5 -18 -14 -52 -33 -58 -12 -4 -19 -14 -17 -23 3 -16 -56 -82 -73 -82 -5 0 -4 -9 3 -20 11 -17 10 -22 -1 -30 -12 -7 -12 -11 -2 -24 10 -12 8 -19 -13 -37 -20 -18 -23 -25 -14 -36 9 -11 1 -25 -39 -66 -28 -29 -64 -77 -82 -108 -17 -31 -40 -59 -51 -62 -17 -4 -18 -8 -7 -31 7 -14 21 -27 32 -28 33 -5 23 -26 -30 -66 -32 -24 -49 -45 -48 -57 1 -10 -7 -30 -18 -43 -10 -14 -19 -31 -19 -39 0 -8 -17 -34 -37 -59 -30 -35 -44 -44 -69 -44 -34 0 -64 -21 -64 -46 0 -8 -7 -17 -15 -20 -20 -8 -19 -40 2 -48 15 -6 15 -8 3 -16 -10 -6 -23 -4 -37 6 -13 8 -23 11 -24 7 -1 -5 -2 -17 -3 -27 -1 -25 -67 -48 -116 -40 -45 7 -81 -10 -130 -61 l-35 -38 28 7 c15 4 27 2 27 -3 0 -6 -12 -18 -27 -26 -28 -16 -9 -16 30 0 10 4 17 3 17 -4 0 -6 -4 -11 -10 -11 -5 0 -10 -6 -10 -14 0 -7 -10 -26 -22 -41 l-22 -28 27 5 c27 5 36 -5 16 -18 -6 -3 -9 -15 -6 -25 3 -10 -1 -23 -9 -30 -8 -6 -14 -22 -14 -34 0 -12 -7 -28 -15 -35 -17 -14 -9 -34 11 -26 22 8 17 -6 -15 -44 -45 -55 -47 -75 -12 -96 23 -14 28 -21 19 -31 -7 -9 -6 -14 6 -19 9 -3 16 -12 16 -20 0 -7 8 -17 17 -23 17 -9 17 -10 0 -29 -10 -11 -17 -29 -16 -39 2 -10 0 -31 -4 -47 -6 -21 -4 -31 9 -40 14 -11 11 -17 -32 -55 l-49 -42 53 6 c28 4 52 4 52 1 0 -4 -7 -18 -16 -31 -14 -22 -14 -25 -1 -25 8 0 17 -7 21 -15 7 -19 56 -19 100 -1 32 13 34 17 32 58 -2 35 4 53 32 96 33 49 34 53 19 70 -16 17 -14 20 27 55 25 21 49 52 56 71 8 26 21 40 51 52 21 10 42 27 45 38 4 11 39 63 79 116 81 107 148 206 158 233 3 9 11 17 17 17 5 0 10 14 10 30 0 18 12 47 30 72 17 22 30 47 30 55 0 8 14 45 30 83 17 38 30 77 30 88 0 10 13 36 29 56 16 20 35 60 41 89 16 66 67 197 86 218 7 8 17 30 20 47 4 17 13 37 20 43 8 6 14 26 14 43 0 60 18 103 46 110 22 6 25 11 20 34 -17 75 -17 88 3 120 19 29 25 32 71 32 44 0 52 3 62 25 9 21 8 29 -9 48 -25 27 -76 138 -67 146 4 3 11 -2 18 -13 25 -42 59 -76 75 -76 12 0 22 13 31 39 11 34 10 43 -9 85 -19 43 -20 56 -11 149 5 56 14 125 20 152 12 54 6 178 -12 260 -17 78 -53 149 -102 204 -26 29 -57 74 -71 101 -24 47 -46 62 -60 40 -12 -19 -51 -11 -62 13 -6 12 -28 31 -49 42 -22 11 -51 33 -66 50 -20 22 -30 27 -36 18 -6 -9 -22 1 -57 33 -28 25 -70 62 -95 82 -61 49 -117 108 -125 132 -4 11 -12 20 -19 20 -7 0 -32 27 -57 60 -24 33 -56 77 -71 97 -16 19 -28 39 -28 43 0 4 -20 34 -45 66 -25 31 -45 68 -45 80 0 12 -11 36 -25 52 -14 17 -28 45 -31 64 -3 18 -14 56 -24 83 -52 139 -97 314 -105 410 -9 103 -26 209 -35 215 -4 3 1 37 12 75 28 96 34 260 13 360 -16 80 -13 109 11 78 12 -17 13 -17 14 -1 0 21 30 13 30 -8 0 -8 5 -14 10 -14 6 0 10 5 10 10 0 21 16 9 27 -20 6 -16 7 -30 2 -30 -14 0 -10 -49 7 -86 11 -23 13 -38 7 -47 -6 -7 -8 -52 -4 -112 5 -88 8 -97 19 -77 15 26 37 31 24 5 -5 -10 -22 -54 -37 -98 l-27 -79 21 -17 c13 -10 19 -26 18 -44 -3 -40 18 -42 30 -3 10 31 11 31 12 7 1 -14 -6 -46 -15 -72 -9 -26 -14 -52 -11 -59 7 -20 47 73 47 110 0 39 20 43 21 5 l1 -28 14 32 c7 17 19 35 25 39 8 5 9 3 4 -6 -9 -14 11 -30 38 -30 19 0 24 36 5 42 -8 3 -4 14 10 34 20 27 45 109 60 199 7 36 9 38 19 20 6 -11 15 -16 19 -12 4 4 0 20 -9 36 -9 16 -18 59 -21 102 -2 41 -9 87 -15 102 -6 16 -10 41 -9 55 1 15 -9 47 -22 72 -21 41 -22 52 -16 148 l7 104 -39 37 c-21 20 -49 53 -61 74 -12 20 -27 37 -31 37 -5 0 -14 19 -21 43 -6 23 -18 55 -26 70 -8 16 -13 37 -10 48 4 13 -5 32 -22 52 -16 18 -34 43 -41 56 -17 33 -50 164 -59 236 -11 80 -33 120 -116 207 l-70 73 33 3 c36 4 39 10 17 38 -14 18 -16 18 -29 2 -16 -22 -39 -23 -56 -3 -19 23 -60 19 -60 -6z m61 -162 c-13 -13 -26 -3 -16 12 3 6 11 8 17 5 6 -4 6 -10 -1 -17z m499 -821 c0 -5 -7 -3 -15 4 -8 7 -15 22 -15 34 1 20 2 20 15 -4 8 -14 14 -29 15 -34z m-220 -178 c0 -5 -4 -8 -10 -8 -5 0 -10 10 -10 23 0 18 2 19 10 7 5 -8 10 -18 10 -22z m-89 -342 c-6 -25 -11 -59 -11 -75 0 -17 -5 -31 -11 -31 -8 0 -10 16 -5 53 3 28 6 70 6 92 0 36 1 38 16 23 14 -14 15 -23 5 -62z m378 27 l-1 -48 -14 34 c-15 34 -13 61 6 61 5 0 10 -21 9 -47z m-49 -50 c0 -29 -35 -8 -38 22 -4 30 -4 30 17 11 12 -11 21 -25 21 -33z m-160 -3 c0 -11 -2 -20 -4 -20 -2 0 -6 9 -9 20 -3 11 -1 20 4 20 5 0 9 -9 9 -20z m186 -59 c-9 -16 -16 -51 -16 -76 0 -33 -4 -45 -12 -43 -16 6 -23 77 -9 91 6 6 11 21 11 33 0 13 7 24 18 26 25 6 25 3 8 -31z m514 -1701 c0 -5 -4 -10 -10 -10 -5 0 -10 5 -10 10 0 6 5 10 10 10 6 0 10 -4 10 -10z m-392 -65 c6 -14 8 -25 4 -25 -12 0 -32 23 -32 37 0 22 16 15 28 -12z m52 -92 c20 -52 39 -149 40 -195 0 -38 -19 -15 -25 30 -4 26 -18 83 -31 128 -27 90 -28 94 -15 94 5 0 19 -26 31 -57z m77 -30 c20 -50 8 -53 -16 -4 -24 46 -26 55 -10 45 6 -3 18 -22 26 -41z m32 -137 c12 -44 6 -62 -15 -45 -16 13 -29 73 -19 89 10 16 21 2 34 -44z m454 -754 c19 -21 6 -42 -25 -42 -13 0 -18 8 -18 30 0 34 18 39 43 12z"/> <path d="M3736 7583 c-3 -3 -6 -13 -6 -21 0 -9 -8 -24 -17 -35 -17 -18 -17 -19 7 -16 17 3 25 11 26 25 1 12 4 26 8 32 7 12 -8 25 -18 15z"/> <path d="M3690 7455 c0 -18 5 -25 19 -25 26 0 44 23 29 38 -22 22 -48 14 -48 -13z"/> <path d="M9260 7410 c0 -6 7 -10 15 -10 8 0 15 2 15 4 0 2 -7 6 -15 10 -8 3 -15 1 -15 -4z"/> <path d="M3660 7360 c-8 -5 -11 -12 -7 -16 4 -4 13 -2 19 4 15 15 7 24 -12 12z"/> <path d="M9110 7343 c0 -5 9 -18 20 -28 11 -10 20 -14 20 -8 0 5 -9 18 -20 28 -11 10 -20 14 -20 8z"/> <path d="M9475 7230 c3 -5 11 -10 16 -10 6 0 7 5 4 10 -3 6 -11 10 -16 10 -6 0 -7 -4 -4 -10z"/> <path d="M6342 7129 c-23 -17 -42 -35 -42 -39 0 -5 -18 -30 -40 -55 -22 -25 -40 -55 -40 -66 0 -12 -7 -19 -19 -19 -15 0 -20 10 -25 45 -7 45 -26 63 -26 25 0 -11 5 -20 10 -20 16 0 12 -50 -5 -56 -8 -4 -15 -18 -16 -33 0 -25 0 -25 -9 -3 -5 12 -12 22 -14 22 -3 0 -3 -6 0 -13 3 -8 -3 -20 -12 -28 -14 -12 -19 -10 -40 16 -26 33 -31 24 -9 -15 21 -37 18 -46 -6 -19 -18 21 -20 21 -14 4 17 -47 18 -65 2 -59 -15 5 -57 -27 -57 -44 0 -14 -36 -43 -43 -35 -4 3 -7 -1 -7 -10 0 -16 -17 -22 -47 -18 -20 3 -15 -17 7 -31 11 -7 20 -17 20 -22 0 -4 -6 -3 -14 3 -11 9 -15 8 -19 -6 -2 -10 -8 -27 -12 -38 -5 -11 -5 -17 0 -13 4 4 14 3 21 -3 11 -9 10 -16 -4 -37 -15 -23 -15 -25 -1 -20 9 4 22 1 29 -5 10 -9 14 -9 17 0 2 7 10 10 18 7 7 -3 16 -1 20 5 3 6 1 11 -4 11 -22 0 -10 18 19 29 24 8 30 7 30 -4 0 -9 6 -12 16 -8 10 3 30 -11 59 -41 46 -47 78 -60 73 -28 -2 9 3 17 10 16 6 -1 21 -2 33 -2 14 -2 19 -7 15 -17 -4 -12 3 -15 29 -15 20 0 43 5 53 11 14 9 26 7 53 -7 19 -10 37 -19 42 -20 4 -1 8 -3 10 -4 1 -1 11 -4 22 -5 11 -2 29 -6 39 -11 16 -6 18 -4 13 9 -8 21 6 22 23 2 18 -22 33 -18 27 6 -5 19 -3 19 26 -7 19 -17 24 -20 13 -6 -11 12 -17 24 -14 27 3 3 11 -1 18 -10 7 -8 20 -15 29 -15 9 0 28 -7 41 -16 18 -11 16 -8 -5 10 -25 22 -27 26 -11 26 18 0 18 1 1 20 -21 23 -9 26 18 5 23 -17 48 -9 40 12 -5 12 -1 14 15 9 25 -8 74 3 66 15 -7 12 3 11 29 -3 20 -10 21 -10 8 6 -12 14 -12 18 5 30 20 15 79 11 102 -7 8 -6 12 -6 12 3 0 7 6 10 14 7 8 -3 19 0 25 7 6 7 22 11 36 8 27 -5 33 9 13 30 -7 7 -26 31 -43 53 -16 22 -42 52 -58 68 -17 15 -23 27 -16 27 11 1 10 3 -2 13 -8 7 -29 33 -47 58 -18 27 -42 49 -56 53 -35 9 -70 47 -54 58 9 5 6 8 -9 8 -30 0 -54 11 -47 21 3 5 -9 12 -26 16 -22 4 -27 8 -19 14 17 10 -1 17 -56 21 l-40 3 33 18 c19 10 32 20 30 22 -2 2 -30 -4 -62 -13 -45 -12 -61 -13 -64 -4 -6 19 -19 14 -26 -10 -7 -22 -8 -22 -15 -4 -5 14 -10 16 -15 7 -5 -7 -15 -10 -23 -6 -9 3 -27 -2 -40 -11 -14 -8 -27 -14 -29 -11 -8 7 8 37 20 37 11 0 52 87 44 95 -2 3 -23 -9 -46 -26z m333 -194 c17 -13 25 -24 18 -25 -7 0 -25 11 -40 25 -36 32 -17 32 22 0z m-128 -70 c53 -22 30 -39 -52 -38 -36 0 -65 -4 -65 -9 0 -5 -4 -6 -10 -3 -15 9 -12 23 10 45 24 24 67 26 117 5z"/> <path d="M9220 7082 c0 -5 7 -15 15 -22 8 -7 15 -8 15 -2 0 5 -7 15 -15 22 -8 7 -15 8 -15 2z"/> <path d="M9353 7063 c3 -10 8 -30 11 -44 4 -17 52 -70 131 -144 69 -65 128 -128 131 -141 7 -29 40 -59 49 -45 3 6 -5 26 -19 44 -15 19 -26 43 -26 55 0 14 -10 24 -33 31 -46 15 -191 165 -212 219 -17 40 -41 60 -32 25z"/> <path d="M4250 7046 c0 -16 -2 -16 -16 -5 -9 8 -18 9 -22 3 -4 -5 -31 -10 -61 -11 -30 -1 -57 -6 -60 -11 -3 -4 -32 -14 -64 -22 -32 -7 -66 -20 -75 -27 -15 -13 -15 -14 8 -8 14 3 42 9 63 12 44 7 50 -10 9 -25 -16 -5 -44 -23 -63 -40 -19 -16 -43 -31 -53 -33 -11 -2 -23 -16 -27 -32 -4 -16 -19 -43 -33 -62 -19 -24 -26 -46 -26 -78 0 -41 2 -45 50 -76 28 -18 50 -37 50 -42 0 -5 9 -9 21 -9 11 0 34 -9 50 -20 22 -16 33 -19 48 -10 24 12 84 13 76 1 -11 -19 13 -20 29 -2 10 11 25 17 37 14 10 -3 26 0 34 7 12 10 20 10 38 1 30 -16 64 -3 117 45 36 33 45 36 80 31 45 -6 48 0 20 35 -10 14 -16 28 -12 32 3 4 1 11 -6 15 -7 4 -10 15 -6 25 6 16 8 16 27 -1 19 -18 20 -17 8 4 -6 12 -8 29 -5 37 4 11 0 16 -13 16 -24 0 -43 19 -36 36 4 10 -3 14 -21 14 -15 0 -26 6 -26 14 0 7 -7 19 -16 27 -8 7 -31 35 -50 63 -18 28 -43 62 -54 76 -17 22 -20 23 -20 6z m21 -176 c31 -17 24 -41 -10 -34 -19 4 -38 0 -52 -10 -35 -24 -58 -21 -52 7 11 43 67 62 114 37z"/> <path d="M6207 7023 c-4 -3 -7 -16 -6 -27 0 -19 1 -19 10 3 10 24 8 36 -4 24z"/> <path d="M6112 6973 c5 -25 28 -28 28 -4 0 12 -6 21 -16 21 -9 0 -14 -7 -12 -17z"/> <path d="M8330 6971 c0 -12 5 -21 10 -21 6 0 10 6 10 14 0 8 -4 18 -10 21 -5 3 -10 -3 -10 -14z"/> <path d="M6080 6953 c0 -12 5 -25 10 -28 13 -8 13 15 0 35 -8 12 -10 11 -10 -7z"/> <path d="M9010 6946 c-5 -15 -5 -37 1 -58 11 -40 12 -57 1 -43 -4 6 -20 21 -36 35 -30 26 -27 11 5 -25 24 -27 79 -133 79 -152 0 -13 -3 -13 -15 -3 -13 10 -15 9 -15 -9 0 -17 -6 -21 -30 -21 -27 0 -30 -3 -29 -32 0 -43 -11 -98 -21 -98 -4 0 -11 7 -14 16 -4 9 -9 14 -12 11 -3 -3 8 -26 25 -51 39 -56 39 -67 1 -59 -30 5 -30 5 -30 -40 0 -37 6 -53 35 -87 19 -23 35 -51 35 -63 0 -20 0 -21 -16 -1 -11 14 -17 16 -21 6 -4 -10 -9 -10 -23 3 -22 20 -35 5 -26 -30 4 -16 24 -32 71 -54 39 -18 65 -36 65 -45 0 -9 5 -16 12 -16 6 0 20 -8 30 -17 15 -14 21 -15 27 -4 5 8 17 11 30 6 14 -4 21 -2 21 7 1 16 27 -16 47 -57 l14 -30 -5 30 c-10 60 -34 102 -63 110 -44 10 -71 32 -93 75 -23 45 -24 50 -10 50 6 0 25 -22 42 -50 34 -55 42 -62 28 -25 -9 22 -7 25 15 25 16 0 26 -7 31 -22 l7 -23 8 23 c18 47 70 -18 54 -68 -5 -17 17 -50 35 -50 16 0 21 38 8 59 -18 30 -2 37 22 10 l21 -24 -6 35 c-4 19 -9 45 -12 58 -7 28 17 30 42 2 10 -11 25 -22 35 -25 9 -3 22 -17 29 -31 21 -46 20 -21 -1 41 -21 65 -38 91 -38 59 0 -14 -5 -11 -20 14 -11 17 -24 32 -29 32 -9 0 -41 61 -41 79 0 21 20 11 38 -19 12 -19 22 -26 28 -20 6 6 20 4 38 -5 16 -9 37 -13 47 -10 10 4 28 1 39 -5 28 -15 36 -7 15 14 -9 9 -12 16 -7 16 6 0 20 -12 31 -27 22 -30 27 -20 11 21 -9 25 2 37 15 16 10 -16 38 -12 31 5 -7 19 -7 19 48 -5 38 -16 46 -17 46 -5 0 8 5 15 10 15 6 0 10 6 10 13 0 19 -122 76 -127 60 -3 -7 -11 -13 -19 -13 -16 0 -104 84 -104 100 0 5 38 -10 84 -35 96 -52 125 -57 79 -15 -27 24 -31 33 -24 57 l8 28 11 -27 c14 -35 29 -42 41 -21 9 17 11 16 27 -6 12 -17 19 -20 24 -10 6 8 -12 35 -52 78 -33 36 -64 76 -70 87 -6 12 -21 27 -34 33 -13 6 -24 15 -24 21 0 19 31 10 56 -17 44 -50 78 -82 82 -79 5 5 -45 83 -55 87 -5 2 -21 20 -36 41 -22 28 -39 39 -80 48 -29 7 -59 19 -68 26 -9 8 -19 11 -22 7 -16 -15 -5 -44 30 -78 20 -19 33 -38 30 -41 -10 -10 -57 23 -108 75 -70 70 -98 89 -86 56 4 -8 2 -17 -3 -20 -16 -10 -11 -23 10 -30 13 -4 23 -18 27 -36 6 -31 23 -40 23 -13 0 27 27 -2 40 -42 15 -44 10 -50 -23 -24 -30 24 -37 25 -37 4 0 -9 8 -18 19 -21 14 -4 50 -53 38 -53 -1 0 -24 8 -52 19 -44 17 -72 41 -47 41 6 0 9 18 7 43 -2 23 -4 47 -4 52 -1 6 -19 10 -41 10 l-40 0 6 -34 c11 -56 -18 -32 -36 30 -9 30 -22 55 -31 57 -8 1 -22 25 -32 52 -20 55 -42 65 -57 26z m37 -68 c-3 -8 -6 -5 -6 6 -1 11 2 17 5 13 3 -3 4 -12 1 -19z m240 -40 c-3 -8 -6 -5 -6 6 -1 11 2 17 5 13 3 -3 4 -12 1 -19z m-200 -54 c18 -14 33 -33 33 -42 0 -13 -2 -14 -9 -4 -4 7 -13 12 -19 10 -7 -2 -22 12 -33 30 -25 39 -17 41 28 6z m341 -88 c43 -27 70 -56 53 -56 -6 0 -11 4 -11 9 0 9 -52 22 -59 15 -2 -2 4 -13 14 -24 32 -35 0 -21 -36 16 -22 23 -27 33 -15 28 16 -6 18 -4 14 14 -8 27 -6 27 40 -2z m-158 -107 c0 -5 -4 -9 -10 -9 -5 0 -10 7 -10 16 0 8 5 12 10 9 6 -3 10 -10 10 -16z m-35 -9 c10 -11 16 -22 14 -25 -7 -6 -49 24 -49 36 0 15 16 10 35 -11z m169 4 c3 -9 6 -23 6 -32 0 -13 -5 -11 -19 8 -19 23 -19 40 -1 40 4 0 11 -7 14 -16z m-20 -49 c28 -12 36 -21 36 -40 0 -28 -9 -31 -29 -12 -11 10 -12 8 -6 -10 4 -14 2 -23 -4 -23 -6 0 -11 8 -11 18 0 9 -7 26 -15 36 -14 18 -20 46 -11 46 2 0 20 -7 40 -15z m-75 -19 c15 -16 19 -26 11 -31 -13 -8 -50 22 -50 42 0 20 14 16 39 -11z m187 -63 c-4 -5 -19 5 -32 20 -35 40 -30 51 8 17 17 -16 28 -33 24 -37z m-526 -33 c0 -5 -4 -10 -10 -10 -5 0 -10 5 -10 10 0 6 5 10 10 10 6 0 10 -4 10 -10z m306 -57 c-4 -4 -11 -1 -16 7 -16 25 -11 35 7 17 9 -9 13 -20 9 -24z m12 -126 c5 -26 -23 -20 -38 8 -7 14 -17 25 -22 25 -4 0 -8 5 -8 11 0 17 64 -25 68 -44z"/> <path d="M9495 6950 c-3 -5 -1 -10 4 -10 6 0 11 5 11 10 0 6 -2 10 -4 10 -3 0 -8 -4 -11 -10z"/> <path d="M9585 6930 c16 -16 31 -28 33 -25 7 7 -39 55 -52 55 -5 0 3 -13 19 -30z"/> <path d="M9927 6939 c7 -7 15 -10 18 -7 3 3 -2 9 -12 12 -14 6 -15 5 -6 -5z"/> <path d="M9300 6933 c0 -14 19 -38 25 -32 8 8 -5 39 -16 39 -5 0 -9 -3 -9 -7z"/> <path d="M9370 6910 c0 -5 5 -10 10 -10 6 0 10 5 10 10 0 6 -4 10 -10 10 -5 0 -10 -4 -10 -10z"/> <path d="M9915 6880 c16 -16 39 -30 51 -30 12 0 28 -10 37 -22 8 -13 17 -21 20 -18 9 9 -37 54 -80 78 -55 30 -62 28 -28 -8z"/> <path d="M9890 6865 c7 -9 15 -13 17 -11 7 7 -7 26 -19 26 -6 0 -6 -6 2 -15z"/> <path d="M9940 6803 c0 -6 8 -21 19 -34 10 -13 28 -43 40 -66 12 -24 26 -43 31 -43 20 0 9 28 -36 86 -55 71 -54 70 -54 57z"/> <path d="M9930 6734 c0 -9 -5 -12 -12 -8 -8 5 -9 2 -4 -12 19 -46 19 -55 0 -48 -11 4 -25 11 -31 17 -19 14 -16 -8 2 -23 8 -7 19 -26 24 -44 5 -17 12 -40 16 -51 4 -11 -7 -3 -25 18 -38 44 -48 42 -26 -5 8 -18 22 -51 31 -73 l16 -40 -31 30 c-24 23 -30 25 -30 11 0 -9 7 -31 16 -49 11 -20 13 -32 6 -35 -15 -5 -3 -32 14 -32 8 0 14 -4 14 -9 0 -5 19 -29 41 -52 l41 -43 -4 44 c-3 25 -8 54 -13 64 -6 14 -5 17 3 12 8 -5 12 0 12 13 0 11 4 22 9 25 5 4 7 35 4 71 -3 49 -1 65 8 63 29 -7 18 37 -27 102 -26 39 -49 70 -51 70 -2 0 -3 -7 -3 -16z"/> <path d="M3525 6641 c-64 -35 -95 -88 -75 -126 10 -20 11 -28 2 -37 -17 -17 -15 -26 8 -33 17 -5 19 -12 14 -46 -6 -34 -4 -39 13 -39 12 0 28 15 42 41 13 22 28 38 35 36 7 -3 12 13 13 47 2 28 8 64 14 79 15 36 5 45 -30 24 -30 -18 -41 -14 -41 14 0 10 9 19 21 21 12 2 28 13 37 26 18 28 10 26 -53 -7z"/> <path d="M8150 6393 c0 -12 5 -25 10 -28 13 -8 13 15 0 35 -8 12 -10 11 -10 -7z"/> <path d="M8875 6385 c4 -16 11 -34 16 -39 15 -16 10 16 -7 44 l-15 25 6 -30z"/> <path d="M3549 6343 c-6 -16 -11 -41 -10 -58 1 -16 2 -57 2 -90 0 -78 16 -55 24 35 3 36 8 82 10 103 6 44 -10 50 -26 10z"/> <path d="M9890 6348 c0 -35 44 -120 75 -148 l29 -25 -21 30 c-28 42 -63 118 -63 138 0 9 -4 19 -10 22 -6 3 -10 -4 -10 -17z"/> <path d="M8590 6325 c0 -16 27 -32 37 -21 7 7 -16 36 -28 36 -5 0 -9 -7 -9 -15z"/> <path d="M8650 6333 c0 -5 7 -21 16 -38 10 -19 11 -31 5 -33 -13 -4 -15 -42 -2 -42 18 0 51 53 51 81 0 30 -15 47 -23 27 -4 -10 -7 -11 -18 0 -15 13 -29 16 -29 5z"/> <path d="M8746 6315 c4 -8 8 -15 10 -15 2 0 4 7 4 15 0 8 -4 15 -10 15 -5 0 -7 -7 -4 -15z"/> <path d="M9540 6321 c0 -5 9 -12 19 -16 10 -3 21 -15 24 -26 5 -18 8 -19 21 -8 14 12 12 17 -10 37 -26 23 -54 29 -54 13z"/> <path d="M9430 6304 c0 -11 9 -29 20 -39 26 -23 26 -9 0 30 -17 26 -20 27 -20 9z"/> <path d="M8840 6248 c0 -21 5 -38 10 -38 13 0 13 40 0 60 -7 11 -10 5 -10 -22z"/> <path d="M8750 6223 c0 -27 16 -46 39 -48 29 -2 25 16 -10 43 -26 21 -29 22 -29 5z"/> <path d="M8612 6205 c-14 -31 -28 -32 -35 -3 -4 12 -9 19 -13 16 -3 -4 2 -31 12 -60 9 -29 19 -47 22 -40 2 6 8 10 12 7 13 -8 -10 -65 -25 -65 -8 0 -16 4 -19 9 -4 5 -15 5 -29 -1 -19 -10 -22 -17 -19 -62 2 -28 -1 -69 -6 -91 -6 -28 -5 -51 4 -75 l12 -35 17 40 c21 50 22 50 39 36 11 -10 19 -7 36 11 16 16 24 19 27 10 6 -15 23 -6 23 14 0 8 -7 14 -16 14 -13 0 -15 7 -10 33 17 80 18 98 6 112 -9 11 -7 22 11 54 14 25 19 46 14 58 -6 16 -8 16 -18 -7 -11 -24 -11 -24 -15 13 -4 43 -14 47 -30 12z m6 -211 c2 -22 -1 -51 -6 -64 l-9 -24 -12 23 c-8 15 -11 39 -6 68 8 55 29 53 33 -3z"/> <path d="M9500 6225 c0 -13 32 -66 36 -62 6 6 -23 67 -31 67 -3 0 -5 -2 -5 -5z"/> <path d="M10096 6192 c-4 -10 -3 -30 2 -42 8 -22 9 -22 16 10 7 37 -8 65 -18 32z"/> <path d="M9830 6170 c6 -11 13 -20 16 -20 2 0 0 9 -6 20 -6 11 -13 20 -16 20 -2 0 0 -9 6 -20z"/> <path d="M8970 6152 c0 -11 7 -41 15 -68 8 -27 15 -51 15 -54 -1 -12 -40 47 -40 60 0 8 -5 22 -10 30 -9 13 -11 13 -19 1 -8 -12 -13 -11 -29 10 -11 13 -24 21 -28 16 -11 -11 52 -92 72 -92 14 0 19 -8 20 -33 2 -39 7 -45 37 -48 50 -5 58 4 52 54 -9 67 -14 79 -31 86 -13 5 -15 0 -10 -27 6 -30 5 -29 -14 11 -10 23 -17 49 -14 57 4 8 1 15 -5 15 -6 0 -11 -8 -11 -18z m70 -145 c0 -5 -4 -5 -10 -2 -5 3 -10 14 -10 23 0 15 2 15 10 2 5 -8 10 -19 10 -23z"/> <path d="M9355 6150 c-8 -13 3 -30 20 -30 8 0 11 7 8 20 -6 22 -18 26 -28 10z"/> <path d="M8795 6110 c-4 -11 -3 -35 1 -53 6 -29 5 -30 -11 -17 -9 8 -26 15 -37 17 -15 2 -12 -3 13 -24 18 -16 43 -28 56 -28 28 -1 28 -1 6 32 -17 26 -17 27 0 20 21 -8 22 5 2 43 -18 35 -22 36 -30 10z"/> <path d="M9929 6105 c17 -33 51 -71 51 -58 0 7 -14 27 -31 45 -16 18 -26 24 -20 13z"/> <path d="M8870 6068 c0 -30 13 -58 26 -58 17 0 18 11 2 45 -12 26 -28 34 -28 13z"/> <path d="M9250 6068 c0 -9 7 -22 17 -29 15 -13 15 -12 3 11 -17 34 -20 36 -20 18z"/> <path d="M9295 6050 c3 -19 13 -42 21 -50 15 -14 15 -13 9 10 -4 14 -14 36 -22 50 -14 25 -14 25 -8 -10z"/> <path d="M4520 6058 c0 -12 16 -17 38 -11 32 8 27 20 -8 19 -16 -1 -30 -4 -30 -8z"/> <path d="M4435 6049 c-4 -6 -5 -12 -2 -15 2 -3 7 2 10 11 7 17 1 20 -8 4z"/> <path d="M4610 6050 c19 -13 30 -13 30 0 0 6 -10 10 -22 10 -19 0 -20 -2 -8 -10z"/> <path d="M9920 6041 c0 -5 5 -13 10 -16 6 -3 10 -2 10 4 0 5 -4 13 -10 16 -5 3 -10 2 -10 -4z"/> <path d="M9070 6012 c0 -15 28 -47 35 -40 8 8 -13 48 -25 48 -6 0 -10 -4 -10 -8z"/> <path d="M9182 5989 c2 -7 12 -13 22 -15 23 -3 15 20 -8 24 -11 3 -17 -1 -14 -9z"/> <path d="M9400 5980 c0 -8 5 -22 10 -30 9 -13 10 -13 10 0 0 8 -5 22 -10 30 -9 13 -10 13 -10 0z"/> <path d="M8861 5884 c0 -11 3 -14 6 -6 3 7 2 16 -1 19 -3 4 -6 -2 -5 -13z"/> <path d="M8938 5875 c2 -14 8 -25 13 -25 10 0 11 12 3 34 -10 25 -22 19 -16 -9z"/> <path d="M9550 5880 c0 -14 7 -20 22 -20 20 0 21 1 3 20 -10 11 -20 20 -22 20 -1 0 -3 -9 -3 -20z"/> <path d="M9792 5861 c2 -27 9 -46 17 -49 11 -4 12 3 7 34 -11 58 -28 68 -24 15z"/> <path d="M8760 5881 c0 -6 5 -13 10 -16 6 -3 10 1 10 9 0 9 -4 16 -10 16 -5 0 -10 -4 -10 -9z"/> <path d="M3355 5860 c-3 -5 -1 -10 4 -10 6 0 11 5 11 10 0 6 -2 10 -4 10 -3 0 -8 -4 -11 -10z"/> <path d="M8460 5829 c-6 -11 -10 -25 -7 -32 2 -6 9 3 15 21 12 36 8 42 -8 11z"/> <path d="M8340 5796 c0 -8 5 -18 10 -21 6 -3 7 -18 4 -33 -4 -21 -3 -24 5 -12 17 23 14 80 -4 80 -8 0 -15 -6 -15 -14z"/> <path d="M8497 5767 c-11 -29 -9 -43 4 -30 6 6 9 19 7 29 -3 18 -4 18 -11 1z"/> <path d="M9440 5751 c0 -6 4 -13 10 -16 6 -3 7 1 4 9 -7 18 -14 21 -14 7z"/> <path d="M9457 5706 c-4 -10 -5 -21 -2 -24 9 -9 17 6 13 25 -3 17 -4 17 -11 -1z"/> <path d="M5290 5670 c20 -13 43 -13 35 0 -3 6 -16 10 -28 10 -18 0 -19 -2 -7 -10z"/> <path d="M8463 5655 c-3 -9 -3 -18 -1 -21 3 -3 8 4 11 16 6 23 -1 27 -10 5z"/> <path d="M3516 5653 c-3 -3 -6 -29 -6 -57 1 -47 2 -49 15 -32 24 32 16 114 -9 89z"/> <path d="M9480 5557 c0 -19 16 -31 24 -18 3 5 -1 14 -9 21 -12 10 -15 10 -15 -3z"/> <path d="M4815 5480 c3 -5 8 -10 11 -10 2 0 4 5 4 10 0 6 -5 10 -11 10 -5 0 -7 -4 -4 -10z"/> <path d="M5135 5350 c3 -5 8 -10 11 -10 2 0 4 5 4 10 0 6 -5 10 -11 10 -5 0 -7 -4 -4 -10z"/> <path d="M8720 5283 c0 -7 -13 -19 -29 -27 -35 -19 -44 -39 -30 -70 8 -16 17 -22 30 -19 26 7 34 -19 16 -54 -8 -15 -18 -50 -22 -76 -4 -26 -11 -50 -16 -53 -13 -7 -11 -54 1 -54 6 0 13 7 16 15 11 28 27 16 20 -16 -7 -35 8 -61 30 -52 9 3 14 -2 14 -13 1 -18 1 -18 11 -1 16 28 32 20 26 -12 -7 -31 17 -51 37 -31 16 16 26 12 26 -10 0 -13 7 -20 20 -20 11 0 20 -6 20 -12 0 -10 2 -10 9 0 7 11 10 10 15 -3 3 -9 6 -28 6 -41 0 -30 16 -31 39 -2 23 27 48 18 54 -19 4 -27 4 -28 12 -5 6 18 10 20 16 10 6 -9 9 -3 9 18 0 17 5 45 11 62 9 27 14 30 29 22 25 -14 53 -2 45 19 -4 9 -9 33 -12 54 -3 20 -9 37 -13 37 -4 0 -10 24 -12 53 -3 28 -17 78 -32 111 -24 53 -28 57 -40 40 -13 -17 -14 -17 -20 4 -12 37 -59 50 -98 26 -6 -4 -17 1 -24 10 -9 12 -14 14 -19 5 -5 -7 -13 1 -23 22 -11 26 -16 30 -19 17 -6 -25 -19 -22 -27 5 -6 22 -7 22 -21 3 -15 -20 -15 -20 -15 0 0 21 -39 77 -40 57z m175 -403 c3 -5 1 -10 -4 -10 -6 0 -11 5 -11 10 0 6 2 10 4 10 3 0 8 -4 11 -10z m145 -50 c0 -11 -4 -20 -10 -20 -5 0 -10 9 -10 20 0 11 5 20 10 20 6 0 10 -9 10 -20z"/> <path d="M9542 5279 c2 -7 10 -15 17 -17 8 -3 12 1 9 9 -2 7 -10 15 -17 17 -8 3 -12 -1 -9 -9z"/> <path d="M8615 5260 c-3 -5 -1 -10 4 -10 6 0 11 5 11 10 0 6 -2 10 -4 10 -3 0 -8 -4 -11 -10z"/> <path d="M8681 5137 c-7 -9 -11 -24 -9 -34 3 -16 5 -15 17 5 15 29 10 50 -8 29z"/> <path d="M8650 5010 c0 -5 5 -10 10 -10 6 0 10 5 10 10 0 6 -4 10 -10 10 -5 0 -10 -4 -10 -10z"/> <path d="M5730 4870 c0 -6 7 -10 15 -10 8 0 15 2 15 4 0 2 -7 6 -15 10 -8 3 -15 1 -15 -4z"/> <path d="M5150 4848 c0 -9 -8 -18 -17 -20 -16 -3 -15 -6 5 -16 14 -8 28 -10 38 -4 13 7 13 11 -5 33 -16 19 -20 21 -21 7z"/> <path d="M3930 4820 c-9 -6 -10 -10 -3 -10 6 0 15 5 18 10 8 12 4 12 -15 0z"/> <path d="M5280 4800 c0 -5 5 -10 11 -10 5 0 7 5 4 10 -3 6 -8 10 -11 10 -2 0 -4 -4 -4 -10z"/> <path d="M5093 4652 c22 -24 22 -39 1 -18 -18 18 -34 21 -34 6 0 -21 33 -60 50 -60 10 0 22 -9 25 -20 3 -11 11 -20 16 -20 6 0 5 9 -2 21 -9 19 -8 20 10 14 19 -6 19 -5 5 24 -14 28 -62 71 -80 71 -4 0 0 -8 9 -18z"/> <path d="M8820 4610 c0 -5 7 -10 16 -10 8 0 12 5 9 10 -3 6 -10 10 -16 10 -5 0 -9 -4 -9 -10z"/> <path d="M5020 4553 c0 -11 80 -93 92 -93 5 0 2 10 -6 22 -9 12 -16 30 -16 39 0 9 -8 19 -17 22 -10 2 -26 8 -35 12 -10 4 -18 3 -18 -2z"/> <path d="M5330 4491 c0 -7 -4 -9 -10 -6 -5 3 -10 2 -10 -4 0 -16 21 -24 33 -12 8 8 8 14 -1 23 -9 9 -12 9 -12 -1z"/> <path d="M8330 4436 c0 -9 5 -16 10 -16 6 0 10 4 10 9 0 6 -4 13 -10 16 -5 3 -10 -1 -10 -9z"/> <path d="M7100 4376 c0 -2 8 -10 18 -17 15 -13 16 -12 3 4 -13 16 -21 21 -21 13z"/> <path d="M5190 4360 c0 -5 5 -10 10 -10 6 0 10 5 10 10 0 6 -4 10 -10 10 -5 0 -10 -4 -10 -10z"/> <path d="M5295 4340 c3 -5 8 -10 11 -10 2 0 4 5 4 10 0 6 -5 10 -11 10 -5 0 -7 -4 -4 -10z"/> <path d="M5600 4335 c7 -9 15 -13 17 -11 7 7 -7 26 -19 26 -6 0 -6 -6 2 -15z"/> <path d="M7081 4293 c7 -12 15 -20 18 -17 3 2 -3 12 -13 22 -17 16 -18 16 -5 -5z"/> <path d="M6520 4260 c0 -5 7 -10 16 -10 8 0 12 5 9 10 -3 6 -10 10 -16 10 -5 0 -9 -4 -9 -10z"/> <path d="M6515 4220 c3 -5 13 -10 21 -10 8 0 12 5 9 10 -3 6 -13 10 -21 10 -8 0 -12 -4 -9 -10z"/> <path d="M5550 4211 c0 -5 7 -14 15 -21 12 -10 15 -10 15 3 0 8 -7 17 -15 21 -8 3 -15 2 -15 -3z"/> <path d="M5549 4123 c12 -18 24 -33 27 -33 9 0 -15 37 -33 51 -12 10 -11 5 6 -18z"/> <path d="M7590 4144 c0 -18 56 -64 77 -64 50 0 10 58 -46 66 -17 3 -31 2 -31 -2z"/> <path d="M5601 4108 c-1 -11 6 -16 19 -15 11 1 20 5 20 10 0 5 -6 7 -14 4 -8 -3 -17 -1 -20 6 -2 7 -5 5 -5 -5z"/> <path d="M6324 4079 c-16 -17 -16 -22 -4 -29 22 -14 46 -12 60 5 11 13 10 18 -6 30 -25 19 -28 19 -50 -6z"/> <path d="M5750 4061 c0 -10 58 -36 65 -29 3 2 -11 12 -30 21 -19 9 -35 13 -35 8z"/> <path d="M7586 4020 c14 -14 74 -24 74 -12 0 10 -33 22 -64 22 -13 0 -17 -3 -10 -10z"/> <path d="M7575 3980 c3 -5 8 -10 11 -10 2 0 4 5 4 10 0 6 -5 10 -11 10 -5 0 -7 -4 -4 -10z"/> <path d="M7583 3894 c16 -9 32 -13 35 -9 9 9 -19 25 -43 24 -16 0 -15 -2 8 -15z"/> <path d="M7560 3835 c7 -9 15 -13 17 -11 7 7 -7 26 -19 26 -6 0 -6 -6 2 -15z"/> <path d="M7512 3819 c-11 -17 -11 -24 0 -41 12 -18 87 -52 96 -43 9 8 -25 59 -53 81 l-31 23 -12 -20z"/> <path d="M7126 3781 c-4 -7 -5 -15 -2 -18 9 -9 19 4 14 18 -4 11 -6 11 -12 0z"/> <path d="M3795 3735 c-28 -27 -33 -55 -11 -55 13 0 56 57 56 74 0 14 -22 5 -45 -19z"/> <path d="M7090 3713 c-23 -30 -23 -68 0 -68 9 0 16 11 18 28 2 15 9 27 18 27 16 0 16 2 8 24 -9 23 -19 20 -44 -11z"/> <path d="M7497 3719 c7 -7 15 -10 18 -7 3 3 -2 9 -12 12 -14 6 -15 5 -6 -5z"/> <path d="M7052 3614 c1 -9 9 -19 16 -22 9 -3 13 2 10 14 -1 9 -9 19 -16 22 -9 3 -13 -2 -10 -14z"/> <path d="M8262 3239 c-37 -40 -82 -86 -99 -102 -18 -17 -33 -36 -33 -43 0 -6 -13 -20 -29 -31 -16 -10 -49 -44 -74 -76 -25 -32 -66 -74 -91 -94 -25 -20 -46 -42 -46 -50 0 -7 -20 -28 -45 -45 -36 -26 -42 -34 -33 -46 10 -11 8 -17 -7 -28 -10 -8 -24 -14 -30 -14 -18 0 -45 -34 -45 -56 0 -11 -6 -26 -12 -33 -10 -11 -10 -14 0 -18 17 -6 15 -28 -4 -44 -9 -7 -13 -21 -10 -34 6 -23 -17 -79 -41 -103 -19 -19 -16 -31 11 -43 30 -14 76 8 108 52 17 23 28 29 45 24 17 -4 28 2 45 25 35 49 72 36 61 -21 -3 -15 1 -30 11 -38 19 -16 32 -8 40 25 4 14 16 35 27 47 11 12 31 46 46 77 14 30 37 78 51 105 65 123 67 129 68 201 2 93 16 132 73 207 60 80 144 207 140 211 -2 2 -17 6 -32 10 -25 6 -35 -1 -95 -65z"/> <path d="M7040 3212 c6 -13 14 -21 18 -18 3 4 -2 14 -12 24 -18 16 -18 16 -6 -6z"/> <path d="M6969 3206 c16 -19 44 -21 39 -3 -3 6 -15 14 -28 15 -21 3 -22 2 -11 -12z"/> <path d="M3891 3169 c-19 -7 -22 -12 -13 -21 9 -9 17 -7 34 10 24 23 18 27 -21 11z"/> <path d="M6910 3160 c0 -6 7 -10 15 -10 8 0 15 2 15 4 0 2 -7 6 -15 10 -8 3 -15 1 -15 -4z"/> <path d="M7005 3150 c3 -5 8 -10 11 -10 2 0 4 5 4 10 0 6 -5 10 -11 10 -5 0 -7 -4 -4 -10z"/> <path d="M5453 2784 c-4 -11 -14 -14 -34 -10 -23 4 -29 2 -29 -11 0 -13 -5 -12 -25 7 -20 19 -25 20 -25 7 0 -10 -5 -14 -15 -11 -10 4 -15 0 -15 -15 0 -24 -10 -27 -28 -9 -9 9 -12 7 -12 -9 0 -50 68 -82 117 -55 16 9 31 8 70 -3 26 -8 51 -15 54 -15 9 0 19 79 12 96 -3 8 -11 14 -19 14 -8 0 -14 4 -14 9 0 17 -31 21 -37 5z"/> <path d="M4551 2728 c-1 -20 4 -40 9 -43 12 -7 12 19 0 55 -7 22 -8 21 -9 -12z"/> <path d="M4275 2720 c-3 -5 1 -10 9 -10 9 0 16 5 16 10 0 6 -4 10 -9 10 -6 0 -13 -4 -16 -10z"/> <path d="M8565 2700 c-3 -5 -1 -10 4 -10 6 0 11 5 11 10 0 6 -2 10 -4 10 -3 0 -8 -4 -11 -10z"/> <path d="M5675 2667 c-9 -33 11 -103 29 -104 6 0 19 -4 29 -8 27 -11 21 22 -10 64 -16 19 -31 45 -34 56 -5 18 -7 17 -14 -8z"/> <path d="M5802 2664 c10 -10 23 -14 34 -10 16 6 16 8 4 16 -8 6 -24 10 -35 10 -18 0 -18 -1 -3 -16z"/> <path d="M4466 2634 c-3 -9 -6 -25 -6 -37 0 -27 16 -11 25 26 8 29 -8 39 -19 11z"/> <path d="M5600 2631 c0 -22 35 -69 45 -59 8 8 -24 78 -36 78 -5 0 -9 -9 -9 -19z"/> <path d="M4610 2621 c0 -11 5 -23 10 -26 6 -4 10 5 10 19 0 14 -4 26 -10 26 -5 0 -10 -9 -10 -19z"/> <path d="M4591 2604 c0 -11 3 -14 6 -6 3 7 2 16 -1 19 -3 4 -6 -2 -5 -13z"/> <path d="M5560 2580 c0 -5 4 -10 9 -10 6 0 13 5 16 10 3 6 -1 10 -9 10 -9 0 -16 -4 -16 -10z"/> <path d="M4709 2428 c-15 -29 -11 -41 46 -116 57 -76 68 -97 36 -68 -25 23 -32 9 -17 -33 8 -26 19 -37 41 -43 37 -10 56 -3 49 17 -5 11 -2 14 9 9 12 -4 19 5 28 36 11 38 10 49 -11 111 -31 91 -51 110 -119 107 -38 -2 -55 -7 -62 -20z"/> <path d="M10666 1834 c-8 -25 -29 -61 -46 -80 -16 -19 -30 -45 -30 -58 0 -13 -14 -50 -30 -81 -37 -70 -43 -118 -17 -123 23 -5 47 15 47 39 0 10 7 22 15 27 9 4 22 34 29 65 14 59 30 87 51 87 7 0 17 26 24 63 7 34 15 72 18 85 4 18 1 22 -20 22 -21 0 -28 -8 -41 -46z"/> <path d="M11763 1493 c-32 -6 -29 -19 6 -26 16 -4 30 -14 33 -25 8 -29 119 -81 118 -54 -1 15 -29 42 -79 76 -29 20 -54 35 -55 35 0 -1 -11 -4 -23 -6z"/> <path d="M10438 1346 c-6 -15 -8 -29 -5 -32 9 -9 37 15 37 32 0 25 -20 25 -32 0z"/> <path d="M8086 1231 c-25 -26 -26 -31 -14 -50 11 -18 19 -21 48 -16 37 6 81 33 88 53 3 11 -57 42 -82 42 -7 0 -25 -13 -40 -29z"/> <path d="M8107 1140 c-9 -11 -23 -20 -31 -20 -7 0 -19 -7 -26 -15 -10 -12 -8 -16 15 -25 34 -13 71 -13 96 1 17 8 18 13 8 25 -9 11 -9 18 -1 26 19 19 14 28 -16 28 -15 0 -35 -9 -45 -20z"/> <path d="M11830 1060 c0 -5 9 -14 20 -20 24 -13 26 -35 5 -44 -19 -7 -20 -36 0 -36 8 0 19 9 25 20 6 12 22 25 36 30 27 11 24 30 -4 30 -9 0 -30 7 -46 15 -33 18 -36 18 -36 5z"/> <path d="M11967 1023 c-10 -3 -16 -10 -14 -16 6 -19 41 -14 45 6 4 17 1 18 -31 10z"/> <path d="M7830 921 c-14 -4 -24 -14 -23 -22 2 -10 -12 -19 -42 -27 -33 -9 -50 -21 -65 -45 -11 -17 -20 -38 -20 -46 0 -18 -55 -67 -102 -92 -20 -10 -39 -23 -43 -29 -8 -13 13 -50 29 -50 8 0 24 -9 37 -21 l24 -21 -25 7 c-48 14 -96 16 -163 8 -76 -9 -87 -23 -30 -38 21 -6 65 -24 98 -41 172 -88 190 -105 199 -185 7 -56 6 -60 -21 -83 -25 -22 -26 -26 -14 -49 9 -18 12 -41 8 -72 -5 -31 -2 -58 8 -81 l14 -34 95 0 95 0 26 53 c14 30 25 69 25 88 0 45 23 126 46 166 18 30 18 36 2 151 -9 66 -31 171 -49 232 -29 101 -30 113 -16 127 14 14 13 18 -13 39 -15 13 -35 24 -44 24 -29 0 -17 17 17 25 28 6 30 8 12 15 -24 10 -32 10 -65 1z"/> <path d="M11994 875 l7 -35 -46 6 c-51 8 -57 -2 -20 -36 29 -28 33 -60 7 -60 -18 0 -42 -18 -42 -31 0 -4 20 -19 45 -33 25 -15 45 -31 45 -36 0 -6 8 -10 19 -10 10 0 21 -9 24 -21 4 -15 12 -20 29 -17 33 4 34 38 1 74 -19 21 -26 38 -25 62 2 17 3 47 3 65 -1 17 -1 49 -1 70 0 33 -2 37 -26 37 -25 0 -26 -2 -20 -35z"/> <path d="M5588 578 c-14 -11 -98 -152 -98 -164 0 -2 18 -4 40 -4 42 0 80 26 80 55 0 8 3 14 8 14 36 -6 52 3 52 29 0 35 -32 57 -60 42 -15 -8 -20 -8 -20 3 0 7 5 18 12 25 16 16 6 15 -14 0z"/> <path d="M11920 559 c0 -11 46 -23 59 -14 20 12 11 20 -24 22 -19 1 -35 -3 -35 -8z"/> <path d="M11800 526 c0 -2 7 -7 16 -10 8 -3 12 -2 9 4 -6 10 -25 14 -25 6z"/> <path d="M5662 420 c-12 -19 -22 -38 -22 -42 0 -5 -6 -8 -14 -8 -7 0 -25 -9 -39 -20 -13 -11 -48 -31 -76 -45 -28 -15 -51 -31 -51 -37 0 -24 47 -91 73 -105 26 -13 27 -16 14 -36 -15 -23 -28 -67 -21 -67 23 0 214 184 214 206 0 6 18 28 40 49 42 41 46 50 31 74 -8 13 -12 13 -26 1 -15 -12 -19 -12 -31 5 -8 10 -14 24 -15 29 0 6 -4 1 -9 -11 -13 -32 -28 -28 -38 10 l-9 32 -21 -35z m45 -105 c-29 -26 -47 -32 -47 -16 0 13 41 41 60 41 10 -1 6 -9 -13 -25z"/> <path d="M11810 240 c0 -5 9 -14 20 -20 25 -13 26 -36 3 -49 -15 -8 -16 -12 -5 -23 11 -11 18 -8 37 16 19 24 28 28 51 22 49 -11 57 -11 60 -1 4 11 -62 35 -94 35 -10 0 -27 7 -38 15 -22 17 -34 19 -34 5z"/> <path d="M5735 174 c-22 -19 -59 -43 -81 -53 -48 -22 -124 -87 -124 -106 0 -10 22 -14 89 -14 84 0 90 1 106 25 9 14 14 33 10 43 -3 11 3 25 16 37 40 36 64 104 37 104 -7 -1 -31 -16 -53 -36z"/> <path d="M11981 91 c-12 -8 -13 -14 -3 -36 17 -37 17 -36 -39 -29 -42 6 -51 4 -47 -7 3 -9 24 -15 64 -17 l59 -3 3 51 c3 51 -6 61 -37 41z"/> <path d="M6830 73 c-24 -9 -60 -45 -54 -54 9 -16 30 -10 58 16 37 34 35 52 -4 38z"/> </g>'
)

# the square crop, chosen by rendering the candidates: tight enough that the
# head still reads at 44 px on a pack card
_DOG_SILHOUETTE_BOX = "300 40 700 700"


def dog_silhouette_css(plate: str, ink: str) -> str:
    """The silhouette as a single reusable CSS class, colours baked in."""
    from urllib.parse import quote

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="'
           + _DOG_SILHOUETTE_BOX + '">'
           '<rect x="-400" y="-400" width="2400" height="2000" fill="'
           + ink + '"/>' + _DOG_SILHOUETTE_G.replace("{PLATE}", plate)
           + '</svg>')
    uri = "data:image/svg+xml," + quote(svg, safe="/:=' ,.-")
    return (".tt-dogsilh { background-image: url(\"" + uri + "\"); "
            "background-size: cover; background-position: center; }")


# Emitted on its own rather than inside the sheet at the top of the file: that
# one is an f-string, and 58 KB of path data full of braces has no business
# going anywhere near it.
#
# The two colours are the plate the dog is knocked out of and the dog itself.
# They were a stop lighter to begin with and the silhouette went muddy at 44px
# against a real photograph on the next card — a placeholder should read as a
# deliberate drawing, not as a photo that failed to load.
st.markdown("<style>" + dog_silhouette_css("#EDEAE7", INK_2) + "</style>",
            unsafe_allow_html=True)


@st.cache_data(ttl=900, show_spinner=False)
def breed_photos() -> dict:
    """Reference breed photographs, as base64 already inside the account.

    SiS has no outbound network, so <img src="https://..."> is a broken icon
    whatever the host. scripts/fetch_breed_images.py range-reads the Stanford
    Dogs archive, downsizes to a square thumbnail and stores the bytes in
    REF.BREED_IMAGE, which is the only form of photograph that survives the
    sandbox. Empty dict when that script has never run — every caller falls
    back to the drawn silhouette rather than showing a gap.
    """
    data = rows_quiet("""
        SELECT breed, image_b64, is_approximate, credit, source_folder
        FROM REF.BREED_IMAGE
    """)
    return {r["BREED"]: r for r in data if r.get("IMAGE_B64")}


def breed_photo(breed: str, size: int = 46, *,
                radius: str = "50%", photos: dict = None) -> str:
    """A round breed thumbnail, or the posture silhouette when there is none.

    THE CAPTION IS NOT DECORATION. This is a photograph of *a* dog of the breed,
    never of the animal being diagnosed, and the two are trivially confused by
    anyone glancing at a card. Every photo therefore carries the disclaimer in
    its tooltip, an amber ring when even the breed is only approximate, and the
    pages that render one repeat it in text underneath.

    PASS `photos` WHEN DRAWING MORE THAN ONE. st.cache_data hands back a fresh
    copy on every call so that a caller cannot corrupt the cache, and the value
    cached here is ~450 KB of base64 — the pack grid calls this 45 times, which
    is 20 MB of copying per rerun if every card looks the table up for itself.
    """
    rec = (photos if photos is not None else breed_photos()).get(breed)
    if not rec:
        # Built outside the f-string: SiS pins Python 3.11, where reusing the
        # delimiter quote inside an f-string expression is a syntax error.
        who = breed or "this breed"
        return (
            f'<div class="tt-dogsilh" style="width:{size}px;height:{size}px;'
            f'border-radius:{radius};border:1px dashed {BORDER};'
            f'flex:0 0 auto" title="No reference photograph for {who} in '
            f'Stanford Dogs — generic silhouette, not a dog of this breed and '
            f'certainly not this animal"></div>')
    appx = bool(rec.get("IS_APPROXIMATE"))
    ring = ACCENT if appx else BORDER
    tip = ("Reference photograph of a similar breed, NOT this dog"
           if appx else "Reference photograph of the breed, NOT this dog")
    return (
        f'<img src="data:image/jpeg;base64,{rec["IMAGE_B64"]}" '
        f'alt="{breed} reference photograph" title="{breed} — {tip}" '
        f'style="width:{size}px;height:{size}px;border-radius:{radius};'
        f'object-fit:cover;border:1.5px solid {ring};flex:0 0 auto;display:block">')


def sensor_anatomy_svg(neck_hz=100, back_hz=100) -> str:
    """Where the two IMUs sit, and which features each one produces.

    This is the diagram the whole build argues from: one sensor on the collar,
    one on the back harness, and the CORRELATION BETWEEN THEM is what separates
    a dog that is travelling (both sensors move together) from a dog whose head
    is doing something its body is not (shaking, scratching, sniffing).
    """
    return f'''
<svg viewBox="0 0 520 210" style="width:100%;max-width:520px;height:auto">
  <path d="M70,120 L70,86 Q70,66 96,64 L286,64 Q312,66 312,86 L312,120"
        fill="none" stroke="{INK_2}" stroke-width="6"
        stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M92,120 L88,168 M132,120 L138,168 M252,120 L246,168 M298,120 L304,168"
        fill="none" stroke="{INK_2}" stroke-width="6" stroke-linecap="round"/>
  <path d="M312,74 Q344,62 352,40" fill="none" stroke="{INK_2}"
        stroke-width="6" stroke-linecap="round"/>
  <circle cx="72" cy="58" r="26" fill="none" stroke="{INK_2}" stroke-width="6"/>
  <path d="M52,38 L46,20 L66,30" fill="none" stroke="{INK_2}" stroke-width="5"
        stroke-linejoin="round"/>

  <circle cx="104" cy="66" r="13" fill="{ACCENT}" opacity="0.9"/>
  <circle cx="104" cy="66" r="21" fill="none" stroke="{ACCENT}" stroke-width="2"
          opacity="0.45"/>
  <line x1="104" y1="45" x2="104" y2="24" stroke="{ACCENT}" stroke-width="1.5"/>
  <text x="104" y="18" text-anchor="middle" font-size="12" font-weight="700"
        fill="{ACCENT}">NECK · {neck_hz} Hz</text>

  <circle cx="246" cy="66" r="13" fill="#2563EB" opacity="0.9"/>
  <circle cx="246" cy="66" r="21" fill="none" stroke="#2563EB" stroke-width="2"
          opacity="0.45"/>
  <line x1="246" y1="45" x2="246" y2="24" stroke="#2563EB" stroke-width="1.5"/>
  <text x="246" y="18" text-anchor="middle" font-size="12" font-weight="700"
        fill="#2563EB">BACK · {back_hz} Hz</text>

  <path d="M118,80 Q175,104 232,80" fill="none" stroke="{INK}" stroke-width="2"
        stroke-dasharray="5 4"/>
  <text x="175" y="122" text-anchor="middle" font-size="12" font-weight="700"
        fill="{INK}">CORR(vm_neck, vm_back)</text>
  <text x="175" y="138" text-anchor="middle" font-size="11" fill="{INK_2}">
    high = whole body travelling · low = head acting alone</text>

  <text x="380" y="66" font-size="11" font-weight="700" fill="{ACCENT}">from the collar</text>
  <text x="380" y="82" font-size="11" fill="{INK_2}">vm_neck_std · zcr_neck</text>
  <text x="380" y="97" font-size="11" fill="{INK_2}">pitch_var · yaw_consistency</text>
  <text x="380" y="126" font-size="11" font-weight="700" fill="#2563EB">from the harness</text>
  <text x="380" y="142" font-size="11" fill="{INK_2}">vm_back_mean · dyn_back</text>
  <text x="380" y="157" font-size="11" fill="{INK_2}">energy_back · sma_back</text>
</svg>'''


def symbol_ribbon(code, dog_id, test_num, match_id, *, height=H_RIBBON):
    """The matched epochs, each coloured by the pattern variable it played.

    ALL ROWS PER MATCH + CLASSIFIER() is what makes this drawable at all: the
    engine reports which symbol consumed each row, so "onset shake itch itch
    itch shake itch itch" is a picture of real seconds rather than a caption.
    Shared by the Syndromes tab and the Vet Note tab, because a note that
    asserts a sequence should be printed beside it.
    """
    mrows = rows(f"""
        SELECT epoch_ts, state, symbol, seq_in_match
        FROM MARTS.SYNDROME_MATCH_ROWS
        WHERE syndrome_code = '{code}' AND dog_id = {dog_id}
          AND test_num = {test_num} AND match_id = {match_id}
        ORDER BY seq_in_match
    """)
    if not mrows:
        empty_state("No per-epoch symbols for this match.",
                    "CALL MARTS.SP_BUILD_MATCH_ROWS() populates them "
                    "(ALL ROWS PER MATCH + CLASSIFIER()).")
        return
    syms = list(dict.fromkeys(col(mrows, "SYMBOL")))
    cmap = {s: SYMBOL_COLOURS[i % len(SYMBOL_COLOURS)] for i, s in enumerate(syms)}
    st.markdown(
        "<div class='tt-mono' style='font-size:15px;letter-spacing:.02em;"
        "margin-bottom:6px'>" +
        " ".join(f"<span style='color:{cmap[r['SYMBOL']]};font-weight:600'>"
                 f"{r['SYMBOL']}</span>" for r in mrows) + "</div>",
        unsafe_allow_html=True)
    if PLOTLY:
        fig = go.Figure()
        for r in mrows:
            fig.add_trace(go.Bar(
                x=[1], y=["match"], orientation="h",
                marker=dict(color=cmap[r["SYMBOL"]], line=dict(width=0)),
                text=[f'{r["SYMBOL"]} — state {r["STATE"]}<br>{r["EPOCH_TS"]}'],
                hoverinfo="text", showlegend=False))
        fig.update_layout(
            barmode="stack",
            title="every matched epoch, coloured by the pattern variable it played",
            title_font_size=11,
            xaxis=dict(visible=False), yaxis=dict(visible=False))
        chart(clean_axes(fig), height)
    st.markdown(" ".join(
        f'<span class="tt-chip" style="background:{cmap[s]}22;'
        f'border-color:{cmap[s]}"><b>{s}</b> = '
        f'{[r["STATE"] for r in mrows if r["SYMBOL"] == s][0]}</span>'
        for s in syms), unsafe_allow_html=True)


def empty_state(what: str, fix: str) -> None:
    st.markdown(
        f'<div class="tt-card"><b>{what}</b><br>'
        f'<span class="tt-quiet">{fix}</span></div>',
        unsafe_allow_html=True,
    )


def metric_strip(items: list[tuple[str, str]]) -> None:
    cols = st.columns(len(items))
    for c, (label, value) in zip(cols, items):
        c.markdown(
            f'<div class="tt-card"><div class="tt-metric-label">{label}</div>'
            f'<div class="tt-metric-value">{value}</div></div>',
            unsafe_allow_html=True,
        )


def esc(text) -> str:
    """Escape for injection into an unsafe_allow_html block.

    The chat is the only place in this app where text a human typed is put back
    on the page, and `white-space: pre-line` means it does not need <br> — only
    the three characters that would end the markup early.
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# The two chat avatars, inline.
#
# st-chat asks DiceBear for a `bottts` robot and a `fun-emoji` face over HTTPS.
# SiS blocks that request, so the stock component renders two broken images per
# turn — the one thing that would make this page look unfinished. These are the
# same two characters redrawn as SVG: nothing to fetch, nothing to break.
def chat_avatar(is_user: bool) -> str:
    if not is_user:
        return (
            '<div class="avatar">'
            '<svg viewBox="0 0 48 48" style="width:48px;height:48px">'
            f'<circle cx="24" cy="24" r="24" fill="{CARD}"/>'
            f'<line x1="24" y1="5" x2="24" y2="13" stroke="{CHAT_BOT}" '
            f'stroke-width="2.4" stroke-linecap="round"/>'
            f'<circle cx="24" cy="4.4" r="2.7" fill="{CHAT_BOT}"/>'
            f'<rect x="3.5" y="21" width="4.2" height="9" rx="2.1" '
            f'fill="{CHAT_BOT}"/>'
            f'<rect x="40.3" y="21" width="4.2" height="9" rx="2.1" '
            f'fill="{CHAT_BOT}"/>'
            f'<rect x="8" y="12" width="32" height="26" rx="7.5" '
            f'fill="{CHAT_BOT}"/>'
            '<rect x="13" y="18.5" width="22" height="9.5" rx="4.75" '
            'fill="#2B2B2B"/>'
            f'<circle cx="19" cy="23.2" r="2.1" fill="{CARD}"/>'
            f'<circle cx="29" cy="23.2" r="2.1" fill="{CARD}"/>'
            '<rect x="18" y="31" width="12" height="2.8" rx="1.4" '
            'fill="#2B2B2B"/>'
            '</svg></div>')
    return (
        '<div class="avatar">'
        '<svg viewBox="0 0 48 48" style="width:48px;height:48px">'
        f'<circle cx="24" cy="24" r="24" fill="{CHAT_USER}"/>'
        '<rect x="13.5" y="18" width="4.2" height="4.2" rx="0.6" fill="#2B2B2B"/>'
        '<rect x="30.3" y="18" width="4.2" height="4.2" rx="0.6" fill="#2B2B2B"/>'
        '<rect x="17.7" y="21.4" width="2.6" height="2.6" rx="0.5" fill="#2B2B2B"/>'
        '<rect x="27.7" y="21.4" width="2.6" height="2.6" rx="0.5" fill="#2B2B2B"/>'
        '<path d="M18.5,30.5 Q24,35.5 29.5,30.5" fill="none" stroke="#2B2B2B" '
        'stroke-width="2.2" stroke-linecap="round"/>'
        '<path d="M22.2,32.6 Q24,36.4 25.8,32.6 Z" fill="#E0555B"/>'
        '</svg></div>')


def chat_bubble(text: str, *, is_user: bool, meta: str = "",
                hue: str = None) -> None:
    """One message row: avatar and bubble, the sender deciding the side."""
    side = " user" if is_user else ""
    edge = f'border-left:3px solid {hue};' if hue and not is_user else ""
    st.markdown(
        f'<div class="tt-chat{side}">{chat_avatar(is_user)}'
        f'<div class="msg" style="{edge}">{esc(text)}</div></div>'
        + (f'<div class="tt-chat-meta">{meta}</div>' if meta else ""),
        unsafe_allow_html=True)


def rail_meter(label: str, value: str, frac, hue: str, note: str = "") -> str:
    """One vitals row in the rail: a label, a number, and the share it is of
    its whole drawn as a hairline underneath.

    The rail used to be four bare numbers. "38 / 45 dogs" and "3 / 40 queued"
    look alike at 12px and mean completely different things — a bar is read
    before the digits are, so the one that is nearly empty is obvious.
    """
    try:
        pct = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        pct = 0.0
    tail = f'<span class="tt-quiet"> {note}</span>' if note else ""
    return (
        f'<div class="tt-meter"><div class="tt-meter-top">'
        f'<span>{label}</span><b>{value}{tail}</b></div>'
        f'<div class="tt-meter-track"><div class="tt-meter-fill" '
        f'style="width:{pct * 100:.1f}%;background:{hue}"></div></div></div>')


def html_table(data: list[dict], columns: list[tuple[str, str]]) -> None:
    """HAZARD 2 fallback. SiS pins an older Streamlit; st.column_config may not
    exist, and st.dataframe of Decimals renders badly. A styled HTML table is
    also denser, which suits a clinical register."""
    head = "".join(f"<th>{label}</th>" for _, label in columns)
    body = ""
    for r in data:
        cells = "".join(f"<td>{'' if r.get(k) is None else r.get(k)}</td>" for k, _ in columns)
        body += f"<tr>{cells}</tr>"
    st.markdown(f'<table class="tt"><tr>{head}</tr>{body}</table>', unsafe_allow_html=True)


def dataframe(data: list[dict], columns: list[tuple[str, str]]) -> None:
    if hasattr(st, "column_config") and hasattr(st, "dataframe"):
        try:
            st.dataframe(
                [{label: r.get(k) for k, label in columns} for r in data],
                use_container_width=True, hide_index=True,
            )
            return
        except Exception:  # noqa: BLE001
            pass
    html_table(data, columns)


def fmt(v, nd: int = 2, dash: str = "—") -> str:
    if v is None:
        return dash
    if isinstance(v, (int, float)):
        return f"{v:,.{nd}f}" if isinstance(v, float) else f"{v:,}"
    return str(v)


def clean_axes(fig, *, y_zero_line: bool = True):
    """Spec section 15: no gridlines except a single horizontal baseline, no
    chart junk, direct labelling over legends wherever a series can carry its
    own name.

    THE LEGEND IS OPT-IN, NOT FORBIDDEN. This used to set showlegend=False
    unconditionally, which quietly deleted the legend from the charts that
    genuinely need one — a stack of triage bands or weight bands has nowhere to
    put a direct label — because clean_axes runs AFTER the caller's
    update_layout and won the argument. Reading the current value keeps the
    default (no legend) while letting a caller that asked for one keep it.
    """
    legend = bool(fig.layout.showlegend)
    fig.update_layout(
        paper_bgcolor=CARD, plot_bgcolor=CARD,
        font=dict(family="Geist, Inter, sans-serif", size=11, color=INK_2),
        # a legend parked below the plot needs floor to stand on, or plotly
        # crops it to a row of half-height swatches
        margin=dict(l=8, r=8, t=28, b=46 if legend else 8),
        hoverlabel=dict(bgcolor=CARD, bordercolor=BORDER,
                        font=dict(color=INK, size=11)),
        showlegend=legend,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=BORDER, ticks="outside",
                     tickcolor=BORDER)
    fig.update_yaxes(showgrid=False, zeroline=y_zero_line, zerolinecolor=BORDER,
                     linecolor=BORDER, ticks="outside", tickcolor=BORDER)
    return fig


@st.cache_data(ttl=600, show_spinner=False)
def state_palette() -> dict[str, str]:
    """State colours live in REF.ETHOGRAM, so the ribbon and the SQL agree.
    Neck-dominant behaviours are amber so pathology literally stands out."""
    data = rows("SELECT state, colour_hex, display_name, sort_order, derivation, "
                "family, description FROM REF.ETHOGRAM ORDER BY sort_order")
    return {r["STATE"]: (r["COLOUR_HEX"] or "#D6D3D1") for r in data}


@st.cache_data(ttl=600, show_spinner=False)
def ethogram() -> list[dict]:
    return rows("SELECT * FROM REF.ETHOGRAM ORDER BY sort_order")


try:
    import plotly.graph_objects as go
    PLOTLY = True
except ModuleNotFoundError:
    PLOTLY = False


def chart(fig, height: int = H_MD) -> None:
    fig.update_layout(height=height)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------
stats = rows("SELECT * FROM MARTS.V_PIPELINE_STATS")
prov = rows("SELECT * FROM MARTS.V_STATE_PROVENANCE ORDER BY epochs DESC")

hl, hr = st.columns([3, 2])
with hl:
    st.markdown(
        f"<h1 style='margin-bottom:0'>TELLTAIL</h1>"
        f"<div style='color:{INK_2};font-size:13px;margin-top:-6px'>"
        f"A dog cannot tell you where it hurts. Ten million rows of collar data can."
        f"</div>", unsafe_allow_html=True)
with hr:
    latest = one(stats, "LATEST_EPOCH_TS")
    st.markdown(
        f"<div style='text-align:right;font-size:11px;color:{INK_2};padding-top:14px'>"
        f"pipeline clock &nbsp;<b class='tt-mono'>{latest or '—'}</b><br>"
        f"rows in the last minute &nbsp;<b>{fmt(one(stats,'ROWS_LAST_MINUTE',0),0)}</b>"
        f"</div>", unsafe_allow_html=True)

if not PLOTLY:
    st.error("plotly is not importable. environment.yml must be staged next to "
             "streamlit_app.py. Re-run: python scripts/deploy_streamlit.py")

# Honesty banner. If any of the state layer is threshold-derived rather than
# model-derived, say so at the top of every tab rather than in a footnote.
heur = sum(r["EPOCHS"] for r in prov if r["STATE_SOURCE"] == "HEURISTIC")
tot = sum(r["EPOCHS"] for r in prov) or 1
if heur:
    st.markdown(
        f'<div class="tt-caveat"><b>{100 * heur / tot:.1f}% of epochs carry a '
        f'heuristic state.</b> SHAKE and SCRATCH are not first-class labels in '
        f'this dataset, so they are derived from the neck/back correlation '
        f'feature and flagged <span class="tt-mono">state_source = '
        f'&#39;HEURISTIC&#39;</span> in the data. Not a diagnostic device.</div>',
        unsafe_allow_html=True)

# ===========================================================================
# NAVIGATION
#
# A left rail rather than st.tabs, for a reason that is not taste: EVERY
# st.tabs body executes on every rerun, so the nine pages each fired their
# queries whether or not you were looking at them — nine pages of Snowflake
# work to render one. The router below calls exactly one page function.
#
# Each page also carries its own hue, marking its rail row, its page header and
# any chrome that belongs to the page rather than to the data — so you can tell
# at a glance which page a screenshot came from.
#
# IT DOES NOT COLOUR THE CHARTS. Colour inside a chart is already spoken for:
# it means an ethogram state, a triage band, or which sensor a trace came from,
# and those meanings have to survive a page change. Page hue is chrome; chart
# colour is data.
# ===========================================================================
PAGES = [
    ("Pack",            "the ward round",                   "#B45309"),
    ("Live Collar",     "100 Hz, two sensors",              "#0F766E"),
    ("Ethogram",        "states, transitions, bouts",       "#6D28D9"),
    ("Syndromes",       "MATCH_RECOGNIZE",                  "#B91C1C"),
    ("Baselines",       "each dog against itself",          "#1D4ED8"),
    ("Vet Note",        "Cortex handoff",                   "#047857"),
    ("Drivers",         "what the model leans on",          "#A16207"),
    ("Shelter Reality", "where this ends up",               "#9D174D"),
    ("Pipeline",        "the DAG, observable",              "#374151"),
    ("Ask TELLTAIL",    "Cortex over the warehouse",        "#7C2D12"),
]

with st.sidebar:
    st.markdown(
        '<div class="tt-brand"><div class="tt-brand-mark">'
        + dog_glyph("TROT", size=24, colour=CARD) +
        '</div><div><div style="font-weight:800;font-size:19px;'
        f'letter-spacing:-.02em;color:{INK};line-height:1.1">TELLTAIL</div>'
        '<div class="tt-quiet" style="font-size:11px">canine telemetry · '
        'Snowflake</div></div></div>', unsafe_allow_html=True)

    # Numbered, because the nine pages are an argument in order and the numbers
    # are how you say "look at 4" out loud during a demo. The router still keys
    # off the bare name, so the labels are cosmetic and cannot desynchronise.
    _num = {p[0]: i for i, p in enumerate(PAGES)}
    _choice = st.radio(
        "section", [p[0] for p in PAGES], label_visibility="collapsed",
        format_func=lambda n: (f"{_num[n] + 1} · {n}" if _num[n] < 9
                               else f"·  {n}"))
    _meta = next(p for p in PAGES if p[0] == _choice)

    # The selected row's hue bar. Emitted here rather than in the sheet at the
    # top because it is the CURRENT page's colour, which is not known until the
    # radio above has returned.
    st.markdown(
        '<style>section[data-testid="stSidebar"] div[role="radiogroup"] > '
        'label:has(input:checked) { border-left-color: ' + _meta[2] + '; }'
        '</style>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="tt-railnote" style="border-left:3px solid {_meta[2]}">'
        f'<b>{_meta[0]}</b><br><span class="tt-quiet">{_meta[1]}</span></div>',
        unsafe_allow_html=True)

    # The rail carries the state of the pipeline, not blank space. These are
    # the numbers worth knowing before reading any page, and they are the same
    # numbers the pages themselves are computed from — if the rail and a page
    # disagree, something is stale and you can see it immediately. Each one is
    # drawn as a share of its own whole, so a near-empty queue looks near-empty.
    _vit = rows("""
        SELECT
            (SELECT COUNT(*) FROM MARTS.EPOCH_STATES)              AS epochs,
            (SELECT COUNT(*) FROM MARTS.SYNDROME_MATCHES)          AS matches,
            (SELECT COUNT(DISTINCT dog_id) FROM MARTS.SYNDROME_MATCHES) AS dogs_hit,
            (SELECT COUNT(*) FROM REF.DOG_INFO)                    AS dogs,
            (SELECT ROUND(100 * holdout_accuracy, 1) FROM ML.MODEL_SUMMARY) AS acc,
            (SELECT COUNT(*) FROM ORACLE.PUBLISH_QUEUE
              WHERE status = 'CONFIRMED')                          AS on_chain,
            (SELECT COUNT(*) FROM ORACLE.PUBLISH_QUEUE)            AS queued,
            (SELECT ROUND(AVG(mean_lag_sec)) FROM MARTS.DAG_LAG_SNAPSHOT
              WHERE mean_lag_sec IS NOT NULL)                      AS lag_s,
            (SELECT COUNT(*) FROM MARTS.DAG_LAG_SNAPSHOT
              WHERE state <> 'ACTIVE')                             AS dt_bad
    """)
    if _vit:
        _v = _vit[0]
        _lag = _v.get("LAG_S")
        _bad = int(_v.get("DT_BAD") or 0)
        _dogs = float(_v.get("DOGS") or 0) or 1.0
        _queued = float(_v.get("QUEUED") or 0) or 1.0
        # `tot` falls back to 1 to avoid a zero divide, which would make an
        # empty warehouse claim a confident 100% model-derived. No provenance
        # rows means the question has no answer yet, so the meter is dropped.
        _model_share = (1.0 - (heur / tot)) if prov else None
        st.markdown(
            # Counts stay plain rows. A meter needs a denominator to mean
            # anything, and "how many matches, out of what?" has no answer —
            # a full bar next to every count teaches the reader to ignore
            # the bars that do carry a ratio.
            '<div class="tt-railstat">'
            f'<div><span>epochs classified</span>'
            f'<b>{fmt(_v.get("EPOCHS"), 0)}</b></div>'
            f'<div><span>syndrome matches</span>'
            f'<b>{fmt(_v.get("MATCHES"), 0)}</b></div></div>'
            + rail_meter("dogs with a finding",
                         fmt(_v.get("DOGS_HIT"), 0),
                         float(_v.get("DOGS_HIT") or 0) / _dogs,
                         "#B91C1C", f'/ {fmt(_v.get("DOGS"), 0)}')
            + rail_meter("held-out accuracy", f'{fmt(_v.get("ACC"), 1)}%',
                         float(_v.get("ACC") or 0) / 100.0, "#15803D")
            + (rail_meter("model-derived states",
                          f"{100 * _model_share:.1f}%", _model_share, ACCENT)
               if _model_share is not None else "")
            + rail_meter("attested on chain", fmt(_v.get("ON_CHAIN"), 0),
                         float(_v.get("ON_CHAIN") or 0) / _queued,
                         "#7C3AED", f'/ {fmt(_v.get("QUEUED"), 0)} queued'),
            unsafe_allow_html=True)
        _dot = "#15803D" if _bad == 0 else "#B91C1C"
        _dag = "all active" if _bad == 0 else str(_bad) + " not active"
        _lagtxt = " · mean lag " + ago(_lag) if _lag is not None else ""
        st.markdown(
            f'<div style="margin-top:11px"><span class="tt-pill">'
            f'<span style="color:{_dot};font-size:13px">&#9679;</span> '
            f'DAG {_dag}{_lagtxt}</span></div>', unsafe_allow_html=True)

    # Photo coverage, said in the rail because the photographs are the one
    # thing on screen a viewer could reasonably mistake for evidence.
    _ph = breed_photos()
    st.markdown(
        '<div class="tt-railfoot" style="margin-top:14px">'
        f'{"Breed photos: " + str(len(_ph)) + " breeds covered. " if _ph else ""}'
        'Not a diagnostic device. Reference photographs are of the breed, '
        'never of the study animal.</div>', unsafe_allow_html=True)

PAGE, PAGE_SUB, PAGE_HUE = _meta

# Which Cortex model, from REF.PARAMS rather than hardcoded here — the same row
# the AI layer procedures read, so the dashboard cannot drift from what ran.
CORTEX_MODEL = one(
    rows("SELECT value_str AS m FROM REF.PARAMS WHERE key = 'cortex_model'"),
    "M", "claude-sonnet-4-5")

st.markdown(
    f'<div class="tt-pagehead" style="border-left:4px solid {PAGE_HUE}">'
    f'<span class="tt-pagetitle">{PAGE}</span>'
    f'<span class="tt-quiet"> — {PAGE_SUB}</span></div>',
    unsafe_allow_html=True)


# ===========================================================================
# TAB 1 — THE PACK.  The ward round.
# ===========================================================================
def _page_0():
    pack = rows("""
        SELECT p.*, t.triage_label, t.severity AS triage_severity, f.n_findings
        FROM MARTS.PACK_STATUS p
        LEFT JOIN (
            SELECT dog_id, ANY_VALUE(triage_label) AS triage_label, MAX(severity) AS severity
            FROM AI.TRIAGE GROUP BY dog_id
        ) t ON t.dog_id = p.dog_id
        LEFT JOIN (
            SELECT dog_id, COUNT(*) AS n_findings FROM MARTS.SYNDROME_MATCHES GROUP BY dog_id
        ) f ON f.dog_id = p.dog_id
        ORDER BY COALESCE(t.severity, 0) DESC, ABS(COALESCE(p.z_self, 0)) DESC
    """)

    metric_strip([
        ("dogs monitored",     fmt(len(pack), 0)),
        ("epochs classified",  fmt(one(stats, "EPOCHS_CLASSIFIED", 0), 0)),
        ("open findings",      fmt(one(stats, "SYNDROME_MATCHES", 0), 0)),
        ("on chain",           fmt(one(stats, "ATTESTATIONS_ONCHAIN", 0), 0)),
    ])

    left, right = st.columns([3, 1])

    with right:
        st.markdown("**Pack brief**")
        brief = rows("SELECT brief, n_findings, n_dogs, generated_at FROM AI.PACK_BRIEF")
        if brief:
            st.markdown(
                f'<div class="tt-card" style="font-size:13px;line-height:1.5">'
                f'{brief[0]["BRIEF"]}'
                f'<div class="tt-quiet" style="margin-top:8px">AI_AGG over '
                f'{fmt(brief[0]["N_FINDINGS"],0)} cached notes · '
                f'{brief[0]["GENERATED_AT"]}</div></div>',
                unsafe_allow_html=True)
        else:
            empty_state("No pack brief yet.",
                        "AI.T_AI generates it on a task. Nothing is called from this page.")

        # The ward round in one bar: how the pack splits across triage bands.
        # Reads the `pack` rows already in memory, so it costs no extra query.
        st.markdown("**Triage mix**")
        bands: dict = {}
        for d in pack:
            k = (d.get("TRIAGE_LABEL") or "not triaged", d.get("TRIAGE_SEVERITY"))
            bands[k] = bands.get(k, 0) + 1
        if PLOTLY and bands:
            ordered = sorted(bands.items(), key=lambda kv: -(kv[0][1] or 0))
            fig = go.Figure()
            for (lbl, sev), n in ordered:
                fig.add_trace(go.Bar(
                    x=[n], y=["pack"], orientation="h",
                    marker=dict(color=TRIAGE_COLOUR.get(sev, "#A8A29E"),
                                line=dict(width=0)),
                    text=[f"{lbl}: {n} dogs"], hoverinfo="text", showlegend=False))
            fig.update_layout(barmode="stack",
                              xaxis=dict(visible=False), yaxis=dict(visible=False))
            chart(clean_axes(fig), H_STRIP)
            st.markdown(" ".join(
                f'<span class="tt-chip" style="background:'
                f'{TRIAGE_COLOUR.get(sev, "#A8A29E")}1c;border-color:'
                f'{TRIAGE_COLOUR.get(sev, "#A8A29E")}">{lbl} <b>{n}</b></span>'
                for (lbl, sev), n in ordered), unsafe_allow_html=True)

        st.markdown("**Provenance**")
        if prov:
            html_table(
                [{"s": r["STATE_SOURCE"], "n": fmt(r["EPOCHS"], 0),
                  "p": f'{r["PCT"]}%'} for r in prov],
                [("s", "source"), ("n", "epochs"), ("p", "share")])

    with left:
        sort = st.radio("sort", ["triage severity", "deviation from own baseline", "dog id"],
                        horizontal=True, label_visibility="collapsed")
        if sort == "deviation from own baseline":
            pack = sorted(pack, key=lambda r: -abs(r.get("Z_SELF") or 0))
        elif sort == "dog id":
            pack = sorted(pack, key=lambda r: r.get("DOG_ID") or 0)

        if not pack:
            empty_state("No dogs yet.",
                        "Run scripts/load_raw.py then scripts/run_sql.py --all.")
        palette = state_palette()
        photos = breed_photos()          # once, not once per card
        spark = rows("""
            SELECT dog_id, epoch_ts, activity_index
            FROM MARTS.ACTIVITY_EPOCH
            QUALIFY ROW_NUMBER() OVER (PARTITION BY dog_id ORDER BY epoch_ts DESC) <= 60
            ORDER BY dog_id, epoch_ts
        """)
        by_dog: dict = {}
        for r in spark:
            by_dog.setdefault(r["DOG_ID"], []).append(r["ACTIVITY_INDEX"])

        for chunk_start in range(0, len(pack), 3):
            cards = st.columns(3)
            for c, d in zip(cards, pack[chunk_start:chunk_start + 3]):
                sev = d.get("TRIAGE_SEVERITY")
                colour = TRIAGE_COLOUR.get(sev, "#A8A29E")
                label = d.get("TRIAGE_LABEL") or "not triaged"
                state = d.get("CURRENT_STATE") or "—"
                stale_s = d.get("SECONDS_SINCE_LAST_EPOCH")
                # Past a week this is not lag, it is the 2018 bulk corpus. Say
                # which, rather than printing "stale 17,715,743s" and hoping.
                live = stale_s is not None and float(stale_s) < 604800
                freshness = (f'<span style="color:{ACCENT}">&#9679;</span> {ago(stale_s)} ago'
                             if live else '<span style="opacity:.55">&#9675;</span> corpus')
                spark = sparkline_svg(by_dog.get(d["DOG_ID"]) or [],
                                      colour=ACCENT if live else "#C9C4BE")
                photo = breed_photo(d.get("BREED"), 44, photos=photos)
                with c:
                    st.markdown(f"""
<div class="tt-card tt-dogcard">
  <div class="tt-dogcard-head">
    <div class="tt-dogcard-id">
      {photo}
      <div class="tt-dogcard-name">
        <b>Dog {d['DOG_ID']}</b>
        <span class="tt-quiet tt-breed">{d.get('BREED') or 'unknown breed'}</span>
      </div>
    </div>
    <span class="tt-badge" style="background:{colour}">{label}</span>
  </div>
  <div class="tt-quiet" style="margin:1px 0 5px">
    {fmt(d.get('AGE_YEARS'),1)}y · {fmt(d.get('WEIGHT_KG'),1)}kg · {d.get('COHORT_ID') or '—'}
  </div>
  <div class="tt-chiprow">
    <span class="tt-chip" style="background:{palette.get(state,'#eee')}20;
          border-color:{palette.get(state,BORDER)}">{state}</span>
    <span class="tt-chip">z<sub>self</sub> {fmt(d.get('Z_SELF'))}</span>
    <span class="tt-chip">{fmt(d.get('N_FINDINGS') or 0,0)} findings</span>
  </div>
  <div class="tt-spark">{spark}</div>
  <div class="tt-quiet tt-dogcard-foot">
    {fmt(d.get('EPOCHS_TOTAL') or 0,0)} epochs ·
    {fmt(d.get('PCT_HEURISTIC') or 0,1)}% heuristic · {freshness}
  </div>
</div>""", unsafe_allow_html=True)

        # Said once under the grid rather than 45 times inside it, and repeated
        # in the rail. A photograph implying it is the animal being diagnosed
        # would be the single most misleading thing on this screen.
        if photos:
            n_appx = sum(1 for r in photos.values() if r.get("IS_APPROXIMATE"))
            st.markdown(
                f'<div class="tt-quiet" style="margin-top:6px">'
                f'Thumbnails are <b>reference photographs of the breed</b>, never '
                f'of the study animal — {len(photos)} breeds covered, {n_appx} of '
                f'them by a near-matching breed (amber ring). Dogs with no '
                f'reference photo keep the drawn posture silhouette. '
                f'{list(photos.values())[0].get("CREDIT") or ""}</div>',
                unsafe_allow_html=True)


# ===========================================================================
# TAB 2 — LIVE COLLAR.  Kills the "is it real" objection.
# ===========================================================================
def _page_1():
    st.markdown("#### Live collar")
    st.markdown('<span class="tt-quiet">Raw 100 Hz dual-sensor waveform, the '
                'features derived from it, and the state each second was '
                'classified into. Same seconds, three levels of abstraction.'
                '</span>', unsafe_allow_html=True)

    # ONLY THE DOGS THAT ACTUALLY HAVE A WAVEFORM.
    #
    # This picker used to list every dog in MARTS.EPOCH_STATES — all 45,
    # including the bulk-corpus dogs whose epochs came from CSV and which have
    # no rows in RAW.COLLAR_TELEMETRY at all. It therefore opened on dog 16,
    # which has zero samples, and drew three empty panels on the one tab whose
    # entire job is answering "is any of this real". Most recent feed first, so
    # it opens on whatever the replayer touched last.
    dogs = rows("""
        SELECT dog_id, COUNT(*) AS n_samples, MAX(sample_ts) AS latest
        FROM RAW.COLLAR_TELEMETRY
        GROUP BY dog_id
        ORDER BY latest DESC
    """)
    if not dogs:
        empty_state(
            "No collar telemetry has landed yet.",
            "This tab reads RAW.COLLAR_TELEMETRY, which the replayer fills. "
            "Start it: python ingest/replay.py --speed 60 --dogs 12")
    else:
        n_classified = one(rows("SELECT COUNT(DISTINCT dog_id) AS n "
                                "FROM MARTS.EPOCH_STATES"), "N", 0)
        st.markdown(
            f'<span class="tt-quiet">{len(dogs)} of {fmt(n_classified, 0)} dogs '
            f'have a raw waveform to show — the rest are bulk-corpus dogs whose '
            f'epochs were loaded from CSV, so they appear on every other tab but '
            f'have no 100 Hz feed to replay here.</span>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 1, 2])
        dog = c1.selectbox("dog", [int(r["DOG_ID"]) for r in dogs], key="live_dog",
                           format_func=lambda d: "dog " + str(d))
        window = c2.selectbox("window (seconds)", [30, 60, 120, 300], index=1)
        auto = c3.checkbox("auto-refresh every 15s while the replayer runs", value=False)
        if auto:
            try:
                st.autorefresh(interval=15000, key="live_refresh")
            except AttributeError:
                st.markdown('<span class="tt-quiet">This Streamlit build has no '
                            'st.autorefresh; use the browser refresh.</span>',
                            unsafe_allow_html=True)

        ing = rows(f"""
            SELECT
                (SELECT SUM(n_rows) FROM RAW.INGEST_LOG
                  WHERE landed_at > DATEADD('minute', -1, CURRENT_TIMESTAMP())) AS last_min,
                (SELECT COUNT(*) FROM RAW.COLLAR_TELEMETRY WHERE dog_id = {dog}) AS dog_rows,
                (SELECT MAX(sample_ts) FROM RAW.COLLAR_TELEMETRY WHERE dog_id = {dog}) AS latest,
                (SELECT COUNT(DISTINCT _batch_id) FROM RAW.COLLAR_TELEMETRY) AS batches
        """)
        metric_strip([
            ("rows landed, last minute", fmt(one(ing, "LAST_MIN", 0), 0)),
            ("raw samples, this dog",    fmt(one(ing, "DOG_ROWS", 0), 0)),
            ("micro-batches",            fmt(one(ing, "BATCHES", 0), 0)),
            ("latest sample",            str(one(ing, "LATEST") or "—")[:19]),
        ])

        # The diagram the rest of this tab is evidence for. Drawn, not
        # photographed: SiS blocks outbound requests, so an <img> to any CDN
        # renders broken — and the useful picture is sensor PLACEMENT anyway.
        with st.expander("Where the two sensors sit, and why their correlation is the feature",
                         expanded=True):
            a1, a2 = st.columns([3, 2])
            with a1:
                st.markdown(sensor_anatomy_svg(), unsafe_allow_html=True)
            with a2:
                st.markdown(
                    '<div class="tt-card" style="font-size:13px;line-height:1.55">'
                    '<b>Two sensors, one question.</b><br>'
                    'A collar alone cannot tell a dog walking from a dog shaking '
                    'its head — both are vigorous neck motion. The back harness '
                    'resolves it: in <i>locomotion</i> the two sensors rise and '
                    'fall together, so their correlation is high. In a head '
                    'shake or a scratch the neck moves and the back does not, so '
                    'it collapses toward zero.<br><br>'
                    '<span class="tt-quiet">That single number, '
                    '<code>CORR(vm_neck, vm_back)</code> over a one-second epoch, '
                    'is computed in Snowflake and is what the ethogram states '
                    'are built on. The chart below it is the raw 100 Hz signal '
                    'the correlation is taken over.</span></div>',
                    unsafe_allow_html=True)

        wave = rows(f"""
            SELECT sample_ts,
                   SQRT(neck_ax*neck_ax + neck_ay*neck_ay + neck_az*neck_az) AS vm_neck,
                   SQRT(back_ax*back_ax + back_ay*back_ay + back_az*back_az) AS vm_back,
                   is_synthetic
            FROM RAW.COLLAR_TELEMETRY
            WHERE dog_id = {dog}
              AND sample_ts >= DATEADD('second', -{window},
                    (SELECT MAX(sample_ts) FROM RAW.COLLAR_TELEMETRY WHERE dog_id = {dog}))
            ORDER BY sample_ts
        """)
        if PLOTLY and wave:
            xs = [str(r["SAMPLE_TS"]) for r in wave]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=xs, y=[float(r["VM_NECK"] or 0) for r in wave],
                                     mode="lines", name="neck",
                                     line=dict(color=ACCENT, width=1),
                                     hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=xs, y=[float(r["VM_BACK"] or 0) for r in wave],
                                     mode="lines", name="back",
                                     line=dict(color="#0369A1", width=1),
                                     hoverinfo="skip"))
            fig.update_layout(title=f"1 · raw 100 Hz vector magnitude — "
                                    f"<span style='color:{ACCENT}'>neck collar</span> vs "
                                    f"<span style='color:#0369A1'>back harness</span>",
                              title_font_size=12)
            chart(clean_axes(fig, y_zero_line=False), H_SM)
            if any(r.get("IS_SYNTHETIC") for r in wave):
                st.markdown('<div class="tt-caveat">This window contains '
                            '<b>SYNTHETIC</b> samples injected by demo_spike.py. '
                            'They carry is_synthetic = TRUE; detection sees them, '
                            'training never fits them.</div>', unsafe_allow_html=True)

        feat = rows(f"""
            SELECT e.epoch_ts, e.vm_neck_mean, e.vm_neck_std, e.neck_back_corr,
                   e.n_samples, s.state, s.state_source
            FROM STAGING.EPOCH_FEATURES e
            LEFT JOIN MARTS.EPOCH_STATES s
                   ON s.dog_id = e.dog_id AND s.test_num = e.test_num
                  AND s.epoch_ts = e.epoch_ts
            WHERE e.dog_id = {dog}
              AND e.epoch_ts >= DATEADD('second', -{window},
                    (SELECT MAX(epoch_ts) FROM STAGING.EPOCH_FEATURES WHERE dog_id = {dog}))
            ORDER BY e.epoch_ts
        """)
        if PLOTLY and feat:
            xs = [str(r["EPOCH_TS"]) for r in feat]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=xs, y=[float(r["VM_NECK_MEAN"] or 0) for r in feat],
                                     mode="lines", line=dict(color=INK, width=1.4),
                                     name="vm mean", hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=xs, y=[float(r["VM_NECK_STD"] or 0) for r in feat],
                                     mode="lines", line=dict(color=INK_2, width=1,
                                                             dash="dot"),
                                     name="vm sd", hoverinfo="skip"))
            fig.add_trace(go.Scatter(
                x=xs, y=[float(r["NECK_BACK_CORR"] or 0) for r in feat],
                mode="lines", yaxis="y2", line=dict(color=ACCENT, width=1.6),
                name="corr",
                text=[f"corr {fmt(r['NECK_BACK_CORR'])}<br>{r.get('STATE')}" for r in feat],
                hoverinfo="text"))
            fig.update_layout(
                title="2 · derived epoch features — "
                      f"<span style='color:{ACCENT}'>neck/back correlation</span> "
                      "on the right axis, pinned to [-1, 1]",
                title_font_size=12,
                yaxis2=dict(overlaying="y", side="right", range=[-1, 1],
                            showgrid=False, zeroline=True, zerolinecolor=BORDER,
                            linecolor=BORDER, tickfont=dict(color=ACCENT)))
            chart(clean_axes(fig, y_zero_line=False), H_SM)

        # State ribbon, drawn from contiguous runs rather than one bar per epoch.
        if PLOTLY and feat:
            palette = state_palette()
            runs = []
            for r in feat:
                s = r.get("STATE") or "UNKNOWN"
                if runs and runs[-1][0] == s:
                    runs[-1][1] += 1
                else:
                    runs.append([s, 1, str(r["EPOCH_TS"]), r.get("STATE_SOURCE")])
            fig = go.Figure()
            for s, n, t0, src in runs:
                fig.add_trace(go.Bar(
                    x=[n], y=["state"], orientation="h",
                    marker=dict(color=palette.get(s, "#D6D3D1"),
                                line=dict(width=0)),
                    text=[f"{s} · {n}s · from {t0} · source {src}"],
                    hoverinfo="text", showlegend=False))
            fig.update_layout(barmode="stack",
                              title="3 · classified state, one block per second",
                              title_font_size=12,
                              xaxis=dict(visible=False), yaxis=dict(visible=False))
            chart(clean_axes(fig), H_RIBBON)
            legend = " ".join(
                f'<span class="tt-chip" style="background:{palette.get(e["STATE"])}30;'
                f'border-color:{palette.get(e["STATE"])}">{e["STATE"]}</span>'
                for e in ethogram())
            st.markdown(f'<div style="margin-top:-6px">{legend}</div>',
                        unsafe_allow_html=True)


# ===========================================================================
# TAB 3 — ETHOGRAM.  The behavioural fingerprint of one dog.
# ===========================================================================
def _page_2():
    st.markdown("#### Ethogram")
    dogs = rows("SELECT DISTINCT dog_id FROM MARTS.EPOCH_STATES ORDER BY dog_id")
    if not dogs:
        empty_state("No states yet.", "Run the pipeline, then the replayer.")
    else:
        dog = st.selectbox("dog", [int(r["DOG_ID"]) for r in dogs], key="etho_dog",
                           format_func=lambda d: "dog " + str(d))
        palette = state_palette()

        # Every query for this page, up front. The layout below pairs charts
        # across two columns and has to size both halves of a row together,
        # which it cannot do if half the data is still being fetched inside a
        # `with` block further down.
        bouts = rows(f"""
            SELECT state, bout_start, bout_seconds
            FROM MARTS.STATE_BOUTS
            WHERE dog_id = {dog}
            ORDER BY bout_start
            LIMIT 4000
        """)
        tr = rows(f"""
            SELECT from_state, to_state, prob, n
            FROM MARTS.STATE_TRANSITIONS WHERE dog_id = {dog}
        """)
        budget = rows(f"""
            SELECT state, COUNT(*) AS n,
                   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
            FROM MARTS.EPOCH_STATES WHERE dog_id = {dog}
            GROUP BY state ORDER BY n DESC
        """)
        bl = rows(f"""
            SELECT state,
                   ROUND(AVG(bout_seconds),1) AS mean_s,
                   MEDIAN(bout_seconds)       AS median_s,
                   MAX(bout_seconds)          AS max_s,
                   COUNT(*)                   AS bouts
            FROM MARTS.STATE_BOUTS WHERE dog_id = {dog}
            GROUP BY state ORDER BY bouts DESC
        """)

        metric_strip([
            ("distinct states",  fmt(len(budget), 0)),
            ("bouts",            fmt(sum(int(r["BOUTS"] or 0) for r in bl), 0)),
            ("longest bout",     ago(max([float(r["MAX_S"] or 0) for r in bl] or [0]))),
            ("transitions seen", fmt(len(tr), 0)),
        ])
        if PLOTLY and bouts:
            fig = go.Figure()
            for b in bouts:
                fig.add_trace(go.Bar(
                    x=[int(b["BOUT_SECONDS"])], y=["day"], orientation="h",
                    marker=dict(color=palette.get(b["STATE"], "#D6D3D1"),
                                line=dict(width=0)),
                    text=[f'{b["STATE"]} · {int(b["BOUT_SECONDS"])}s · {b["BOUT_START"]}'],
                    hoverinfo="text", showlegend=False))
            fig.update_layout(barmode="stack", title="State ribbon, full session",
                              title_font_size=12,
                              xaxis=dict(visible=False), yaxis=dict(visible=False))
            chart(clean_axes(fig), H_RIBBON)

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**Transition matrix**")
            st.markdown('<span class="tt-quiet">A first-order behavioural Markov '
                        'chain, computed in SQL with LAG and row-normalised. Which '
                        'behaviour follows which.</span>', unsafe_allow_html=True)
            if PLOTLY and tr:
                states = sorted({r["FROM_STATE"] for r in tr} | {r["TO_STATE"] for r in tr})
                idx = {s: i for i, s in enumerate(states)}
                z = [[None] * len(states) for _ in states]
                txt = [[""] * len(states) for _ in states]
                for r in tr:
                    i, j = idx[r["FROM_STATE"]], idx[r["TO_STATE"]]
                    z[i][j] = float(r["PROB"] or 0)
                    txt[i][j] = (f'{r["FROM_STATE"]} → {r["TO_STATE"]}<br>'
                                 f'p = {fmt(r["PROB"],3)}<br>n = {fmt(r["N"],0)}')
                fig = go.Figure(go.Heatmap(
                    z=z, x=states, y=states, text=txt, hoverinfo="text",
                    colorscale=[[0, "#FFFFFF"], [1, ACCENT]], showscale=False,
                    xgap=1, ygap=1))
                fig.update_layout(title="p(next state | current state)",
                                  title_font_size=11)
                chart(clean_axes(fig, y_zero_line=False), H_MD)
            else:
                empty_state("No transitions.", "MARTS.STATE_TRANSITIONS is empty.")

        with c2:
            st.markdown("**State budget**")
            st.markdown('<span class="tt-quiet">How the session divides across '
                        'the ethogram. The same states as the ribbon above, '
                        'counted rather than laid out in time.</span>',
                        unsafe_allow_html=True)
            if PLOTLY and budget:
                fig = go.Figure(go.Pie(
                    labels=col(budget, "STATE"),
                    values=[float(x) for x in col(budget, "N")],
                    hole=0.6, sort=False,
                    marker=dict(colors=[palette.get(s, "#D6D3D1")
                                        for s in col(budget, "STATE")],
                                line=dict(color=CARD, width=1.5)),
                    text=[f'{r["STATE"]} {r["PCT"]}%' for r in budget],
                    hoverinfo="text", textinfo="none"))
                fig.update_layout(title="proportion of the session in each state",
                                  title_font_size=11)
                chart(clean_axes(fig, y_zero_line=False), H_MD)

        # ------------------------------------------------------------------
        # SECOND ROW, AND THE REASON THIS PAGE WAS REBUILT.
        #
        # The layout used to be one heatmap in the left column against a donut,
        # a bar chart and a table stacked in the right — so the left half of the
        # page ran out about 600px above the right half and the tab read as
        # half-finished. Both halves of this row are bar charts of the same
        # ethogram, sized together so they end on the same line.
        # ------------------------------------------------------------------
        # self-transitions are ~95% of every row (a dog in REST stays in REST)
        # and would be the only thing visible on the chart
        moves = sorted([r for r in tr if r["FROM_STATE"] != r["TO_STATE"]],
                       key=lambda r: -(r["PROB"] or 0))[:12]
        row_h = max(bars(len(moves)), bars(len(bl)))

        d1, d2 = st.columns(2)

        with d1:
            st.markdown("**What follows what**")
            st.markdown('<span class="tt-quiet">The same matrix, read out loud. '
                        'Self-transitions are dropped — a dog at rest stays at '
                        'rest through 95% of its seconds, and leaving that in '
                        'means the only thing visible is the diagonal.</span>',
                        unsafe_allow_html=True)
            if PLOTLY and moves:
                labels = [f'{r["FROM_STATE"]} → {r["TO_STATE"]}' for r in moves][::-1]
                fig = go.Figure(go.Bar(
                    x=[float(r["PROB"] or 0) for r in moves][::-1], y=labels,
                    orientation="h",
                    marker=dict(color=[palette.get(r["TO_STATE"], "#D6D3D1")
                                       for r in moves][::-1]),
                    text=[f'{r["FROM_STATE"]} → {r["TO_STATE"]}<br>'
                          f'p = {fmt(r["PROB"],3)}<br>{fmt(r["N"],0)} times'
                          for r in moves][::-1],
                    hoverinfo="text"))
                fig.update_layout(title="likeliest changes of behaviour, "
                                        "coloured by where the dog ends up",
                                  title_font_size=11)
                chart(clean_axes(fig, y_zero_line=False), row_h)
            else:
                empty_state("No changes of state.",
                            "This dog never left its first state in the "
                            "window, or MARTS.STATE_TRANSITIONS is empty.")

        with d2:
            st.markdown("**Bout-length distribution**")
            st.markdown('<span class="tt-quiet">Where lameness and exercise '
                        'intolerance become visible before any syndrome fires: '
                        'identical totals, different bout lengths.</span>',
                        unsafe_allow_html=True)
            if PLOTLY and bl:
                # Median as the bar, mean as a marker on top of it. The GAP
                # between them is the finding: a state whose mean sits far
                # above its median is one long bout hiding in a pile of short
                # ones, which is exactly the shape lameness makes.
                names = [str(r["STATE"]) for r in bl][::-1]
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=[float(r["MEDIAN_S"] or 0) for r in bl][::-1], y=names,
                    orientation="h",
                    marker=dict(color=[palette.get(n, "#D6D3D1") for n in names]),
                    text=[f'{r["STATE"]}<br>median {fmt(r["MEDIAN_S"],0)}s'
                          f'<br>mean {fmt(r["MEAN_S"],1)}s'
                          f'<br>longest {fmt(r["MAX_S"],0)}s'
                          f'<br>{fmt(r["BOUTS"],0)} bouts' for r in bl][::-1],
                    hoverinfo="text"))
                fig.add_trace(go.Scatter(
                    x=[float(r["MEAN_S"] or 0) for r in bl][::-1], y=names,
                    mode="markers",
                    marker=dict(color=INK, size=7, symbol="line-ns-open",
                                line=dict(width=1.6, color=INK)),
                    text=[f'mean {fmt(r["MEAN_S"],1)}s' for r in bl][::-1],
                    hoverinfo="text"))
                fig.update_layout(title="median bout length (bar) against the mean "
                                        "(tick) — seconds",
                                  title_font_size=11)
                chart(clean_axes(fig, y_zero_line=False), row_h)

        # Third row: the numbers behind the two charts above, and the ethogram
        # they are all expressed in. The definitions belong on this page and
        # nowhere else — every other tab assumes you already know what SNIFF
        # means and which sensor decided it.
        e1, e2 = st.columns(2)
        with e1:
            st.markdown("**Bouts, per state**")
            if bl:
                html_table(
                    [{"s": r["STATE"], "b": fmt(r["BOUTS"], 0), "m": fmt(r["MEAN_S"], 1),
                      "d": fmt(r["MEDIAN_S"], 0), "x": fmt(r["MAX_S"], 0)} for r in bl],
                    [("s", "state"), ("b", "bouts"), ("m", "mean s"),
                     ("d", "median s"), ("x", "max s")])
        with e2:
            st.markdown("**The ethogram itself**")
            etho = ethogram()
            if etho:
                html_table(
                    [{"c": f'<span class="tt-chip" style="background:'
                           f'{r.get("COLOUR_HEX") or "#D6D3D1"}22;border-color:'
                           f'{r.get("COLOUR_HEX") or "#D6D3D1"}">{r["STATE"]}</span>',
                      "d": r.get("DISPLAY_NAME") or "", "f": r.get("FAMILY") or "",
                      "v": str(r.get("DERIVATION") or "")[:28]} for r in etho],
                    [("c", "state"), ("d", "name"), ("f", "family"),
                     ("v", "derived from")])


# ===========================================================================
# TAB 4 — SYNDROMES.  THE MONEY TAB.
# ===========================================================================
def _page_3():
    st.markdown("#### Syndromes")
    st.markdown(
        '<span class="tt-quiet">A threshold cannot detect a syndrome. A vet '
        'diagnoses from an ordered sequence of events, not from a daily average. '
        'Below, each finding is a match of a regular expression over rows — a '
        'differential diagnosis expressed as a <span class="tt-mono">'
        'MATCH_RECOGNIZE</span> clause.</span>', unsafe_allow_html=True)

    finds = rows("""
        SELECT syndrome_code, syndrome_name, body_system, dog_id, test_num, match_id,
               onset_ts, resolve_ts, duration_s, n_epochs, confidence, severity,
               avg_quality, model_purity, evidence, pattern_text, define_text,
               why_not_threshold, breed, z_self
        FROM MARTS.V_FINDINGS
        ORDER BY severity DESC, confidence DESC, onset_ts DESC
        LIMIT 500
    """)

    if not finds:
        empty_state(
            "No syndrome matches yet.",
            "Either the pipeline has not run the pattern layer "
            "(CALL MARTS.SP_BUILD_SYNDROMES()), or no dog in the replayed window "
            "expressed one of the six sequences. Inject a labelled one: "
            "python scripts/demo_spike.py --dog 7 --syndrome S1")
    else:
        by_code: dict[str, int] = {}
        for f in finds:
            by_code[f["SYNDROME_CODE"]] = by_code.get(f["SYNDROME_CODE"], 0) + 1
        metric_strip([("matches", fmt(len(finds), 0))] +
                     [(k, fmt(v, 0)) for k, v in sorted(by_code.items())][:4])

        # WHEN each finding fired, and to WHICH dog. The table below is ordered
        # by severity and so destroys the time axis; this keeps it, which is how
        # you see that one dog fired the same syndrome four times in an evening
        # rather than four dogs firing it once.
        if PLOTLY:
            codes = sorted(by_code)
            cmap = {c: SYMBOL_COLOURS[i % len(SYMBOL_COLOURS)]
                    for i, c in enumerate(codes)}
            fig = go.Figure()
            for c in codes:
                pts = [f for f in finds if f["SYNDROME_CODE"] == c]
                fig.add_trace(go.Scatter(
                    x=[str(f["ONSET_TS"]) for f in pts],
                    y=[float(f["DOG_ID"]) for f in pts],
                    mode="markers", name=c,
                    marker=dict(color=cmap[c], line=dict(width=0),
                                opacity=0.85,
                                size=[6 + 10 * float(f["CONFIDENCE"] or 0)
                                      for f in pts]),
                    text=[f'{c} · {f["SYNDROME_NAME"]}<br>dog {int(f["DOG_ID"])}'
                          f' · {f.get("BREED") or "unknown breed"}'
                          f'<br>{str(f["ONSET_TS"])[:19]}'
                          f'<br>{fmt(f["DURATION_S"],0)}s over '
                          f'{fmt(f["N_EPOCHS"],0)} epochs'
                          f'<br>confidence {fmt(f["CONFIDENCE"],3)}'
                          f' · severity {f["SEVERITY"]}' for f in pts],
                    hoverinfo="text"))
            fig.update_layout(
                title="every finding in time — one dot per match, sized by "
                      "confidence, coloured by syndrome",
                title_font_size=11, showlegend=True,
                legend=dict(orientation="h", y=-0.2, font=dict(size=10)),
                yaxis=dict(title="dog id"))
            chart(clean_axes(fig, y_zero_line=False), H_MD)

        dataframe(
            [{
                "code": f["SYNDROME_CODE"], "syndrome": f["SYNDROME_NAME"],
                "dog": f["DOG_ID"], "onset": str(f["ONSET_TS"])[:19],
                "dur_s": fmt(f["DURATION_S"], 0), "epochs": fmt(f["N_EPOCHS"], 0),
                "conf": fmt(f["CONFIDENCE"], 3), "sev": f["SEVERITY"],
                "quality": fmt(f["AVG_QUALITY"], 2), "model%": fmt(f["MODEL_PURITY"], 2),
            } for f in finds[:60]],
            [("code", "code"), ("syndrome", "syndrome"), ("dog", "dog"),
             ("onset", "onset (UTC)"), ("dur_s", "duration"), ("epochs", "epochs"),
             ("conf", "confidence"), ("sev", "severity"), ("quality", "epoch quality"),
             ("model%", "model purity")])

        st.markdown("---")
        st.markdown("##### The matched rows")
        labels = [f'{f["SYNDROME_CODE"]} · dog {f["DOG_ID"]} · {str(f["ONSET_TS"])[:19]} '
                  f'· conf {fmt(f["CONFIDENCE"],3)}' for f in finds]
        pick = st.selectbox("select a finding", range(len(labels)),
                            format_func=lambda i: labels[i], key="find_pick")
        f = finds[pick]

        cl, cr = st.columns([3, 2])

        with cl:
            symbol_ribbon(f["SYNDROME_CODE"], f["DOG_ID"], f["TEST_NUM"],
                          f["MATCH_ID"])

            ev = f.get("EVIDENCE")
            if ev:
                try:
                    parsed = json.loads(ev) if isinstance(ev, str) else ev
                    st.markdown("**Evidence**")
                    html_table([{"k": k, "v": json.dumps(v) if isinstance(v, (list, dict))
                                 else v} for k, v in parsed.items()],
                               [("k", "measure"), ("v", "value")])
                except Exception:  # noqa: BLE001
                    st.markdown(f'<div class="tt-mono">{ev}</div>', unsafe_allow_html=True)

        with cr:
            st.markdown("**The SQL that found it**")
            st.code(
                f"SELECT * FROM MARTS.V_SYNDROME_INPUT\n"
                f"MATCH_RECOGNIZE (\n"
                f"    PARTITION BY dog_id, test_num\n"
                f"    ORDER BY epoch_ts\n"
                f"    MEASURES MATCH_NUMBER() AS match_id, ...\n"
                f"    ONE ROW PER MATCH\n"
                f"    AFTER MATCH SKIP PAST LAST ROW\n"
                f"    PATTERN ( {f['PATTERN_TEXT']} )\n"
                f"    DEFINE\n        "
                + ",\n        ".join(x.strip() for x in
                                     str(f["DEFINE_TEXT"]).split(","))
                + "\n)", language="sql")
            st.markdown(
                f'<div class="tt-card"><div class="tt-metric-label">'
                f'why a sequence and not a threshold</div>'
                f'<div style="font-size:12px;line-height:1.5;margin-top:4px">'
                f'{f["WHY_NOT_THRESHOLD"]}</div></div>', unsafe_allow_html=True)

        st.markdown("---")
        c1, c2 = st.columns(2)

        with c1:
            st.markdown("##### Sensitivity")
            st.markdown('<span class="tt-quiet">Every pattern at three quantifier '
                        'settings. A single hand-tuned quantifier is a magic '
                        'number; the curve is a result.</span>',
                        unsafe_allow_html=True)
            sens = rows("""
                SELECT syndrome_code, variant, matches, dogs_firing,
                       avg_match_epochs, pattern_text
                FROM MARTS.V_SENSITIVITY_CURVE
                ORDER BY syndrome_code,
                         CASE variant WHEN 'loose' THEN 1 WHEN 'tuned' THEN 2 ELSE 3 END
            """)
            if PLOTLY and sens:
                order = {"loose": 0, "tuned": 1, "strict": 2}
                codes = sorted({r["SYNDROME_CODE"] for r in sens})
                fig = go.Figure()
                for i, code in enumerate(codes):
                    pts = sorted([r for r in sens if r["SYNDROME_CODE"] == code],
                                 key=lambda r: order.get(r["VARIANT"], 9))
                    fig.add_trace(go.Scatter(
                        x=[r["VARIANT"] for r in pts],
                        y=[float(r["MATCHES"] or 0) for r in pts],
                        mode="lines+markers+text",
                        line=dict(color=SYMBOL_COLOURS[i % len(SYMBOL_COLOURS)], width=1.6),
                        marker=dict(size=6),
                        text=[code if r["VARIANT"] == "strict" else "" for r in pts],
                        textposition="middle right", textfont=dict(size=10),
                        hovertext=[f'{code} {r["VARIANT"]}<br>{fmt(r["MATCHES"],0)} matches'
                                   f'<br>{fmt(r["DOGS_FIRING"],0)} dogs'
                                   f'<br><span style="font-family:monospace">'
                                   f'{r["PATTERN_TEXT"]}</span>' for r in pts],
                        hoverinfo="text", showlegend=False))
                fig.update_layout(title="matches by quantifier strictness",
                                  title_font_size=11)
                chart(clean_axes(fig), H_MD)
            else:
                empty_state("No sensitivity sweep.",
                            "CALL MARTS.SP_SYNDROME_SWEEP() runs all 18 variants.")

        with c2:
            st.markdown("##### By cohort")
            coh = rows("""
                SELECT syndrome_code, weight_band, age_band, matches, dogs
                FROM MARTS.V_SYNDROME_BY_COHORT
                ORDER BY syndrome_code, matches DESC
            """)
            if PLOTLY and coh:
                codes = sorted({r["SYNDROME_CODE"] for r in coh})
                bands = sorted({r["WEIGHT_BAND"] for r in coh})
                fig = go.Figure()
                for i, b in enumerate(bands):
                    ys = []
                    for code in codes:
                        ys.append(sum(float(r["MATCHES"] or 0) for r in coh
                                      if r["SYNDROME_CODE"] == code
                                      and r["WEIGHT_BAND"] == b))
                    fig.add_trace(go.Bar(
                        x=codes, y=ys, name=b,
                        marker=dict(color=SYMBOL_COLOURS[i % len(SYMBOL_COLOURS)]),
                        text=[f"{b}: {int(y)}" for y in ys], hoverinfo="text"))
                fig.update_layout(barmode="stack", showlegend=True,
                                  legend=dict(orientation="h", y=-0.15,
                                              font=dict(size=10)),
                                  title="syndrome frequency by weight band",
                                  title_font_size=11)
                chart(clean_axes(fig), H_MD)
            else:
                empty_state("No cohort breakdown.", "REF.DOG_INFO may be empty.")


# ===========================================================================
# TAB 5 — BASELINES.  Every dog is its own control.
# ===========================================================================
def _page_4():
    st.markdown("#### Baselines")
    st.markdown('<span class="tt-quiet">A Husky doing forty minutes of galloping '
                'is a Tuesday. A twelve-year-old Bulldog doing the same is an '
                'emergency. Every comparison here is against the dog itself first '
                'and its cohort second, joined with <span class="tt-mono">ASOF '
                'JOIN</span> so a gap in the feed degrades the comparison instead '
                'of silently shifting the window.</span>', unsafe_allow_html=True)

    dogs = rows("SELECT DISTINCT dog_id FROM MARTS.DOG_DEVIATION ORDER BY dog_id")
    if not dogs:
        empty_state("No deviation data.",
                    "MARTS.DOG_DEVIATION needs a trailing baseline; give the "
                    "replayer an hour of dog time (or run at --speed 60).")
    else:
        dog = st.selectbox("dog", [int(r["DOG_ID"]) for r in dogs], key="base_dog")

        dev = rows(f"""
            SELECT epoch_ts, activity_index, baseline_index, baseline_std,
                   z_self, z_cohort, cohort_mean, cohort_std, is_synthetic
            FROM MARTS.DOG_DEVIATION
            WHERE dog_id = {dog} AND baseline_index IS NOT NULL
            ORDER BY epoch_ts
            LIMIT 5000
        """)
        if PLOTLY and dev:
            xs = [str(r["EPOCH_TS"]) for r in dev]
            base = [float(r["BASELINE_INDEX"] or 0) for r in dev]
            sd = [float(r["BASELINE_STD"] or 0) for r in dev]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=xs, y=[b + 2 * s for b, s in zip(base, sd)],
                                     mode="lines", line=dict(width=0),
                                     hoverinfo="skip", showlegend=False))
            fig.add_trace(go.Scatter(x=xs, y=[b - 2 * s for b, s in zip(base, sd)],
                                     mode="lines", line=dict(width=0), fill="tonexty",
                                     fillcolor="rgba(168,162,158,0.22)",
                                     hoverinfo="skip", showlegend=False))
            fig.add_trace(go.Scatter(x=xs, y=base, mode="lines",
                                     line=dict(color=INK_2, width=1, dash="dot"),
                                     hoverinfo="skip", showlegend=False))
            fig.add_trace(go.Scatter(
                x=xs, y=[float(r["ACTIVITY_INDEX"] or 0) for r in dev], mode="lines",
                line=dict(color=ACCENT, width=1.5),
                text=[f'{fmt(r["ACTIVITY_INDEX"],3)}<br>z_self {fmt(r["Z_SELF"],2)}'
                      f'{" · SYNTHETIC" if r.get("IS_SYNTHETIC") else ""}' for r in dev],
                hoverinfo="text", showlegend=False))
            fig.update_layout(
                title="today against this dog's own trailing hour "
                      "(shaded band = ±2 SD of its own normal)",
                title_font_size=11)
            chart(clean_axes(fig, y_zero_line=False), H_MD)

        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown("**Against its cohort**")
            summ = rows(f"""
                SELECT ROUND(AVG(z_self),3) AS z_self, ROUND(AVG(z_cohort),3) AS z_cohort,
                       ROUND(AVG(activity_index),4) AS idx,
                       ROUND(AVG(cohort_mean),4) AS cohort_mean,
                       ANY_VALUE(cohort_id) AS cohort
                FROM MARTS.DOG_DEVIATION WHERE dog_id = {dog}
            """)
            if summ:
                s = summ[0]
                html_table([
                    {"k": "cohort", "v": s["COHORT"]},
                    {"k": "this dog, mean index", "v": fmt(s["IDX"], 4)},
                    {"k": "cohort mean index", "v": fmt(s["COHORT_MEAN"], 4)},
                    {"k": "z vs own baseline", "v": fmt(s["Z_SELF"], 3)},
                    {"k": "z vs cohort", "v": fmt(s["Z_COHORT"], 3)},
                ], [("k", ""), ("v", "")])

            st.markdown("**Trajectory**")
            traj = rows(f"SELECT * FROM ML.V_TRAJECTORY WHERE dog_id = {dog}")
            if traj and traj[0].get("PROJECTED_INDEX") is not None:
                t = traj[0]
                html_table([
                    {"k": "current", "v": fmt(t["CURRENT_INDEX"], 4)},
                    {"k": "projected", "v": fmt(t["PROJECTED_INDEX"], 4)},
                    {"k": "change", "v": f'{fmt(t["PCT_CHANGE"],1)}%'},
                ], [("k", ""), ("v", "")])
            else:
                st.markdown('<span class="tt-quiet">No forecast yet. ML.FORECAST '
                            'needs ~100 training points; see ML.FUNCTION_STATUS.'
                            '</span>', unsafe_allow_html=True)

        with c2:
            st.markdown("**The wall**")
            st.markdown('<span class="tt-quiet">Every dog, worst deviation first. '
                        'A grid of sparklines where three are obviously wrong.'
                        '</span>', unsafe_allow_html=True)
            wall = rows("""
                SELECT dog_id, ROUND(AVG(ABS(z_self)),3) AS z_abs,
                       ROUND(AVG(z_self),3) AS z_self, COUNT(*) AS n
                FROM MARTS.DOG_DEVIATION
                WHERE z_self IS NOT NULL
                GROUP BY dog_id ORDER BY z_abs DESC
            """)
            if PLOTLY and wall:
                fig = go.Figure(go.Bar(
                    x=[f'dog {int(r["DOG_ID"])}' for r in wall],
                    y=[float(r["Z_SELF"] or 0) for r in wall],
                    marker=dict(color=[TRIAGE_COLOUR[3] if abs(r["Z_ABS"] or 0) > 2
                                       else (ACCENT if abs(r["Z_ABS"] or 0) > 1
                                             else "#D6D3D1") for r in wall]),
                    text=[f'dog {int(r["DOG_ID"])}<br>z {fmt(r["Z_SELF"],2)}<br>'
                          f'{fmt(r["N"],0)} epochs' for r in wall],
                    hoverinfo="text"))
                fig.update_layout(title="mean deviation from own baseline, by dog "
                                        "(amber > 1 SD, red > 2 SD)",
                                  title_font_size=11)
                chart(clean_axes(fig), H_MD)

            # Where each dog is HEADING, not just where it is. ML.FORECAST
            # projects the activity index forward per dog; drawn as a dumbbell
            # so the length of the connector is the size of the move and its
            # colour is the direction — a column of numbers hides both.
            st.markdown("**Projected trajectory, every dog**")
            st.markdown('<span class="tt-quiet">Hollow dot is where the dog is '
                        'now, solid dot is where ML.FORECAST puts it. Amber '
                        'connectors are dogs winding down.</span>',
                        unsafe_allow_html=True)
            alltraj = rows("""
                SELECT dog_id, current_index, projected_index, pct_change
                FROM ML.V_TRAJECTORY
                WHERE projected_index IS NOT NULL AND current_index IS NOT NULL
                ORDER BY pct_change
            """)
            if PLOTLY and alltraj:
                fig = go.Figure()
                for r in alltraj:
                    cur = float(r["CURRENT_INDEX"] or 0)
                    proj = float(r["PROJECTED_INDEX"] or 0)
                    lbl = f'dog {int(r["DOG_ID"])}'
                    hue = ACCENT if proj < cur else "#0369A1"
                    fig.add_trace(go.Scatter(
                        x=[cur, proj], y=[lbl, lbl], mode="lines",
                        line=dict(color=hue, width=2), hoverinfo="skip"))
                    fig.add_trace(go.Scatter(
                        x=[cur, proj], y=[lbl, lbl], mode="markers",
                        marker=dict(color=[CARD, hue], size=8,
                                    line=dict(color=hue, width=1.6)),
                        text=[f'{lbl}<br>now {fmt(cur,4)}',
                              f'{lbl}<br>projected {fmt(proj,4)}'
                              f'<br>{fmt(r["PCT_CHANGE"],1)}%'],
                        hoverinfo="text"))
                fig.update_layout(title="activity index — now against projected",
                                  title_font_size=11)
                chart(clean_axes(fig, y_zero_line=False),
                      bars(len(alltraj)))
            else:
                empty_state("No forecasts yet.",
                            "ML.FORECAST needs ~100 training points per dog; "
                            "see ML.FUNCTION_STATUS.")


# ===========================================================================
# TAB 6 — VET NOTE.  A clinical document, not a chat bubble.
# ===========================================================================
def _page_5():
    st.markdown("#### Veterinary handoff note")
    notes = rows("""
        SELECT * FROM AI.V_VET_NOTE_FULL
        ORDER BY severity DESC, generated_at DESC
        LIMIT 200
    """)
    if not notes:
        empty_state(
            "No notes cached yet.",
            "AI.T_AI batches AI_COMPLETE into AI.VET_NOTES on a task. This page "
            "never calls Cortex: a render path that costs credits is a render "
            "path that will exhaust a trial cap during a demo.")
    else:
        # The caseload this note sits inside, before the note itself. Which
        # syndromes generated notes, and how those notes were triaged — from
        # the rows already fetched, so the overview costs nothing.
        st.markdown("**The caseload these notes came from**")
        st.markdown('<span class="tt-quiet">Every cached note, by syndrome and '
                    'by the triage band AI_CLASSIFY assigned it. Read the bar '
                    'first, then pick a note out of it.</span>',
                    unsafe_allow_html=True)
        if PLOTLY:
            codes = sorted({n["SYNDROME_CODE"] for n in notes})
            sevs = sorted({n.get("SEVERITY") for n in notes},
                          key=lambda s: -(s or 0))
            fig = go.Figure()
            for sev in sevs:
                bucket = [n for n in notes if n.get("SEVERITY") == sev]
                lbl = (bucket[0].get("TRIAGE_LABEL") if bucket else None) or "untriaged"
                ys = [sum(1 for n in bucket if n["SYNDROME_CODE"] == c) for c in codes]
                fig.add_trace(go.Bar(
                    x=codes, y=ys, name=str(lbl),
                    marker=dict(color=TRIAGE_COLOUR.get(sev, "#A8A29E")),
                    text=[f"{c} · {lbl}: {y} notes" for c, y in zip(codes, ys)],
                    hoverinfo="text"))
            fig.update_layout(barmode="stack", showlegend=True,
                              legend=dict(orientation="h", y=-0.18,
                                          font=dict(size=10)),
                              title="cached notes by syndrome, stacked by triage band",
                              title_font_size=11)
            chart(clean_axes(fig), H_SM)

        labels = [f'{n["SYNDROME_CODE"]} · dog {n["DOG_ID"]} · '
                  f'{str(n["ONSET_TS"])[:19]} · {n["TRIAGE_LABEL"]}' for n in notes]
        i = st.selectbox("finding", range(len(labels)),
                         format_func=lambda k: labels[k], key="note_pick")
        n = notes[i]
        colour = TRIAGE_COLOUR.get(n.get("SEVERITY"), "#A8A29E")

        photo = breed_photo(n.get("BREED"), 58, radius="6px")
        appx = (breed_photos().get(n.get("BREED")) or {}).get("IS_APPROXIMATE")
        # Under a clinical note the caption has to be unambiguous: a reader
        # skimming a SOAP paragraph beside a dog's face will assume it is the
        # patient unless told otherwise, in that exact spot, every time.
        cap = ("reference photo of a<br>similar breed, not this dog"
               if appx else "reference photo of the<br>breed, not this dog")
        st.markdown(f"""
<div class="tt-card">
  <div style="display:flex;gap:12px;align-items:flex-start">
    <div style="flex:0 0 auto;text-align:center">
      {photo}
      <div class="tt-quiet" style="font-size:9.5px;line-height:1.25;margin-top:3px;
           max-width:58px">{cap}</div>
    </div>
    <div style="flex:1 1 auto;min-width:0">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span class="tt-badge" style="background:{colour};font-size:12px">
            {str(n.get('TRIAGE_LABEL','')).upper()}</span>
          <b style="margin-left:8px;font-size:15px">{n['SYNDROME_NAME']}</b>
          <span class="tt-quiet"> · {n.get('BODY_SYSTEM')}</span>
        </div>
        <div class="tt-quiet">confidence {fmt(n.get('CONFIDENCE'),3)}</div>
      </div>
      <div class="tt-quiet" style="margin-top:4px">
        Dog {n['DOG_ID']} · {n.get('BREED') or 'unknown breed'} ·
        {fmt(n.get('AGE_YEARS'),1)}y · {fmt(n.get('WEIGHT_KG'),1)}kg ·
        onset {str(n['ONSET_TS'])[:19]} UTC · {fmt(n.get('DURATION_S'),0)}s ·
        {fmt(n.get('N_EPOCHS'),0)} epochs
      </div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown(
            f'<div class="tt-card" style="font-size:13.5px;line-height:1.65;'
            f'white-space:pre-wrap">{n["SOAP_NOTE"]}</div>',
            unsafe_allow_html=True)

        # The sequence the note is about, printed beside the note.
        #
        # A generated paragraph asserting "repeated gait interruption" is worth
        # exactly as much as the reader's willingness to believe it. This is the
        # actual matched seconds, each coloured by the pattern variable it
        # played, so the prose can be checked against the rows it came from.
        # AI.V_VET_NOTE_FULL does not carry test_num or match_id, so the match
        # is resolved by its onset — the key the note itself displays.
        st.markdown("**The sequence this note is describing**")
        key = rows(f"""
            SELECT test_num, match_id FROM MARTS.SYNDROME_MATCHES
            WHERE dog_id = {n['DOG_ID']}
              AND syndrome_code = '{n['SYNDROME_CODE']}'
              AND onset_ts = '{str(n['ONSET_TS'])[:19]}'
            LIMIT 1
        """)
        if key:
            symbol_ribbon(n["SYNDROME_CODE"], n["DOG_ID"],
                          key[0]["TEST_NUM"], key[0]["MATCH_ID"])
        else:
            empty_state("Could not resolve this note back to its match.",
                        "The note is kept; only the per-epoch ribbon is missing.")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Evidence, linked to the epoch range**")
            ev = n.get("EVIDENCE")
            trows = [
                {"k": "matched window", "v": f'{str(n["ONSET_TS"])[:19]} → '
                                             f'{str(n.get("RESOLVE_TS"))[:19]}'},
                {"k": "epochs in match", "v": fmt(n.get("N_EPOCHS"), 0)},
                {"k": "epoch completeness", "v": fmt(n.get("AVG_QUALITY"), 3)},
                {"k": "model-derived states", "v": fmt(n.get("MODEL_PURITY"), 3)},
                {"k": "z vs own baseline", "v": fmt(n.get("Z_SELF"), 2)},
                {"k": "z vs cohort", "v": fmt(n.get("Z_COHORT"), 2)},
            ]
            if ev:
                try:
                    parsed = json.loads(ev) if isinstance(ev, str) else ev
                    for k, v in parsed.items():
                        trows.append({"k": k, "v": json.dumps(v)
                                      if isinstance(v, (list, dict)) else v})
                except Exception:  # noqa: BLE001
                    pass
            html_table(trows, [("k", "claim"), ("v", "value")])
        with c2:
            st.markdown("**Pattern**")
            st.code(f"PATTERN ( {n['PATTERN_TEXT']} )", language="sql")
            st.markdown(
                f'<div class="tt-quiet">{n.get("WHY_NOT_THRESHOLD","")}</div>',
                unsafe_allow_html=True)

        st.markdown(
            f'<div class="tt-quiet" style="margin-top:10px;border-top:1px solid '
            f'{BORDER};padding-top:6px">Generated by <b>{n.get("CORTEX_MODEL")}</b> '
            f'via AI_COMPLETE · pipeline <b>{n.get("PIPELINE_VERSION")}</b> · '
            f'{n.get("GENERATED_AT")} · triage by AI_CLASSIFY over this note. '
            f'TELLTAIL is not a diagnostic device and nothing here substitutes '
            f'for a veterinarian.</div>', unsafe_allow_html=True)


# ===========================================================================
# TAB 7 — DRIVERS.  What explains it.
# ===========================================================================
def _page_6():
    st.markdown("#### Drivers")

    # THE SEPARATION, IN THE THREE AXES THE DESIGN ARGUES ABOUT.
    #
    # 3D because the claim is three-dimensional and flattens badly: locomotion
    # separates on neck/back CORRELATION, neck-driven behaviour separates on
    # neck DOMINANCE, and stillness separates on neck SD. Any 2D pair of those
    # three collapses two clusters on top of each other — project it yourself
    # by dragging the cube and you can watch it happen. Sampled per state so
    # the rare classes stay visible next to SNIFF.
    st.markdown("**The feature space, in the three axes the ethogram turns on**")
    st.markdown('<span class="tt-quiet">Drag to rotate. Each point is one '
                'labelled second. Locomotion climbs the correlation axis; '
                'SHAKE and SCRATCH climb the dominance axis; REST and SIT '
                'collapse into the low-motion corner. This is the whole '
                'classifier argument in one object.</span>',
                unsafe_allow_html=True)
    fs = rows("""
        SELECT state, neck_back_corr, vm_neck_std, neck_dominance
        FROM ML.V_LABELLED_EPOCHS
        WHERE neck_back_corr IS NOT NULL
          AND vm_neck_std IS NOT NULL
          AND neck_dominance IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (PARTITION BY state ORDER BY RANDOM()) <= 320
    """)
    if PLOTLY and fs:
        pal = state_palette()
        by_state: dict = {}
        for r in fs:
            by_state.setdefault(r["STATE"], []).append(r)
        fig = go.Figure()
        for stt in sorted(by_state):
            pts = by_state[stt]
            fig.add_trace(go.Scatter3d(
                x=[float(r["NECK_BACK_CORR"]) for r in pts],
                y=[float(r["VM_NECK_STD"]) for r in pts],
                # log-ish squash: dominance runs to ~50 for a head shake and
                # would otherwise flatten every other class onto the floor
                z=[min(float(r["NECK_DOMINANCE"]), 8.0) for r in pts],
                mode="markers", name=stt,
                marker=dict(size=2.2, color=pal.get(stt, "#999"), opacity=0.72),
                hovertemplate=(stt + "<br>corr %{x:.2f}<br>neck sd %{y:.2f}"
                               "<br>dominance %{z:.2f}<extra></extra>")))
        fig.update_layout(
            scene=dict(
                xaxis=dict(title="neck/back corr", backgroundcolor=CARD,
                           gridcolor=BORDER, zerolinecolor=BORDER),
                yaxis=dict(title="neck SD (g)", backgroundcolor=CARD,
                           gridcolor=BORDER, zerolinecolor=BORDER),
                zaxis=dict(title="neck dominance (clipped at 8)",
                           backgroundcolor=CARD, gridcolor=BORDER,
                           zerolinecolor=BORDER),
                camera=dict(eye=dict(x=1.6, y=1.5, z=0.9))),
            paper_bgcolor=CARD, plot_bgcolor=CARD, showlegend=True,
            legend=dict(itemsizing="constant", font=dict(size=10)),
            margin=dict(l=0, r=0, t=4, b=0),
            font=dict(family="Geist, Inter, sans-serif", size=11, color=INK_2))
        fig.update_layout(height=H_LG)
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})
    else:
        empty_state("No labelled epochs to plot.",
                    "Needs the bulk load and the Gate A label map.")

    ms = rows("SELECT * FROM ML.MODEL_SUMMARY")
    if ms:
        m = ms[0]
        acc = m.get("HOLDOUT_ACCURACY")
        st.markdown(f"""
<div class="tt-card">
  <div class="tt-metric-label">held-out accuracy — dog-disjoint</div>
  <div style="font-size:44px;font-weight:700;line-height:1.05;color:{INK}">
    {fmt((acc or 0) * 100, 1)}%</div>
  <div class="tt-quiet" style="margin-top:2px">
    {m.get('CLASSIFIER')} · {fmt(m.get('HOLDOUT_DOGS'),0)} entirely unseen dogs ·
    {fmt(m.get('HOLDOUT_EPOCHS'),0)} epochs · macro F1 {fmt(m.get('MACRO_F1'),3)} ·
    weighted F1 {fmt(m.get('WEIGHTED_F1'),3)}
  </div>
  <div class="tt-quiet" style="margin-top:6px">{m.get('PROTOCOL')}</div>
</div>""", unsafe_allow_html=True)

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Feature separation**")
        # ML.V_FEATURE_SEPARATION, not the model's SHOW_FEATURE_IMPORTANCE.
        # Every introspection accessor on this account raises "Computation
        # Error in function __SHOW_*" while PREDICT works fine, so the
        # importance table is permanently empty here. The caption below says
        # exactly what this is instead of passing it off as the model's.
        st.markdown('<span class="tt-quiet">One-way ANOVA F-ratio per feature '
                    'over the labelled epochs: between-class variance of the '
                    'class means over pooled within-class variance. How well '
                    'each feature separates the ethogram states ON ITS OWN — '
                    'a different question from the split gain a tree model '
                    'reports, and computed here because model introspection '
                    'does not run on this account.</span>',
                    unsafe_allow_html=True)
        fi = rows("""SELECT feature, f_ratio FROM ML.V_FEATURE_SEPARATION
                     WHERE f_ratio IS NOT NULL ORDER BY f_ratio DESC LIMIT 25""")
        if PLOTLY and fi:
            names = [str(r["FEATURE"]) for r in fi][::-1]
            vals = [float(r["F_RATIO"] or 0) for r in fi][::-1]
            fig = go.Figure(go.Bar(
                x=vals, y=names, orientation="h",
                marker=dict(color=[ACCENT if "CORR" in n.upper() else "#D6D3D1"
                                   for n in names]),
                text=[f"{n} {v:.2f}" for n, v in zip(names, vals)], hoverinfo="text"))
            fig.update_layout(title="how well each feature separates the states "
                                    "(neck/back correlation in amber)",
                              title_font_size=11)
            chart(clean_axes(fig, y_zero_line=False), bars(len(names), row=16))
        else:
            empty_state("No feature separation yet.",
                        "Needs labelled epochs: run the bulk load and push the "
                        "Gate A label map.")

        acc_err = rows("SELECT classifier, accessors_ok, accessor_error "
                       "FROM ML.MODEL_STATUS")
        if acc_err and acc_err[0].get("ACCESSORS_OK") is False and                 acc_err[0].get("ACCESSOR_ERROR"):
            st.caption(f"Model introspection unavailable on this account — "
                       f"{acc_err[0]['CLASSIFIER']} trained and predicts "
                       f"normally. Verbatim: {acc_err[0]['ACCESSOR_ERROR'][:180]}")

        st.markdown("**The feature, justifying itself**")
        st.markdown('<span class="tt-quiet">Neck/back correlation by true label. '
                    'Locomotion should sit high; neck-dominant behaviours should '
                    'collapse toward zero. If they do not, the feature is wrong.'
                    '</span>', unsafe_allow_html=True)
        cbl = rows("SELECT * FROM STAGING.V_CORR_BY_LABEL ORDER BY avg_corr")
        if PLOTLY and cbl:
            fig = go.Figure()
            for r in cbl:
                fig.add_trace(go.Bar(
                    x=[str(r["LABEL_PRIMARY"])],
                    y=[float(r["AVG_CORR"] or 0)],
                    marker=dict(color=ACCENT if (r["AVG_CORR"] or 0) < 0.4 else "#0369A1"),
                    error_y=dict(type="data", array=[float(r["SD_CORR"] or 0)],
                                 color=BORDER, thickness=1, width=3),
                    text=[f'{r["LABEL_PRIMARY"]}<br>mean {fmt(r["AVG_CORR"],3)}'
                          f'<br>median {fmt(r["MEDIAN_CORR"],3)}'
                          f'<br>IQR {fmt(r["P25_CORR"],2)}–{fmt(r["P75_CORR"],2)}'
                          f'<br>{fmt(r["EPOCHS"],0)} epochs'],
                    hoverinfo="text", showlegend=False))
            fig.update_layout(title="CORR(vm_neck, vm_back) by annotated behaviour",
                              title_font_size=11, yaxis=dict(range=[-0.3, 1.0]))
            chart(clean_axes(fig), H_MD)

    with c2:
        st.markdown("**Contribution to deviation**")
        di = rows("SELECT * FROM ML.DRIVER_INSIGHTS LIMIT 200")
        if di:
            method = di[0].get("METHOD")
            # Built outside the f-string: SiS pins Python 3.11, where a
            # multi-line expression inside an f-string is a syntax error.
            note = ""
            if method == "SQL_CONTRIBUTION":
                note = (" — the native TOP_INSIGHTS signature was unavailable, so "
                        "this is a transparent SQL contribution decomposition: "
                        "lift × share per dimension value.")
            st.markdown(f'<span class="tt-quiet">method: <b>{method}</b>{note}'
                        f'</span>', unsafe_allow_html=True)
            if "CONTRIBUTION" in di[0]:
                top = sorted(di, key=lambda r: -abs(r.get("CONTRIBUTION") or 0))[:18]
                if PLOTLY:
                    fig = go.Figure(go.Bar(
                        x=[float(r.get("CONTRIBUTION") or 0) for r in top][::-1],
                        y=[f'{r.get("DIMENSION")} = {r.get("VALUE")}' for r in top][::-1],
                        orientation="h",
                        marker=dict(color=[ACCENT if (r.get("CONTRIBUTION") or 0) > 0
                                           else "#0369A1" for r in top][::-1]),
                        text=[f'{r.get("DIMENSION")} = {r.get("VALUE")}<br>'
                              f'contribution {fmt(r.get("CONTRIBUTION"),5)}<br>'
                              f'lift {fmt(r.get("LIFT"),4)} · share {fmt(r.get("SHARE"),3)}<br>'
                              f'{fmt(r.get("N"),0)} epochs' for r in top][::-1],
                        hoverinfo="text"))
                    fig.update_layout(title="which slices move the deviation metric",
                                      title_font_size=11)
                    chart(clean_axes(fig, y_zero_line=False), bars(len(top)))
            else:
                dataframe(di[:30], [(k, k.lower()) for k in list(di[0].keys())[:6]])
        else:
            empty_state("No driver insights.",
                        "CALL ML.SP_RUN_TOP_INSIGHTS(); check ML.FUNCTION_STATUS.")

        st.markdown("**Confusion matrix, held-out dogs**")
        cm = rows("SELECT * FROM ML.CONFUSION_MATRIX")
        if PLOTLY and cm:
            states = sorted({r["ACTUAL_STATE"] for r in cm} |
                            {r["PREDICTED_STATE"] for r in cm})
            idx = {s: i for i, s in enumerate(states)}
            z = [[0.0] * len(states) for _ in states]
            txt = [[""] * len(states) for _ in states]
            for r in cm:
                i, j = idx[r["ACTUAL_STATE"]], idx[r["PREDICTED_STATE"]]
                z[i][j] = float(r["PCT_OF_ACTUAL"] or 0)
                txt[i][j] = (f'actual {r["ACTUAL_STATE"]}<br>'
                             f'predicted {r["PREDICTED_STATE"]}<br>'
                             f'{fmt(r["N"],0)} epochs ({r["PCT_OF_ACTUAL"]}%)')
            fig = go.Figure(go.Heatmap(
                z=z, x=states, y=states, text=txt, hoverinfo="text",
                colorscale=[[0, "#FFFFFF"], [1, INK]], showscale=False,
                xgap=1, ygap=1))
            fig.update_layout(title="row-normalised, % of each true state",
                              title_font_size=11,
                              xaxis_title="predicted", yaxis_title="actual")
            chart(clean_axes(fig, y_zero_line=False), H_MD)

        pc = rows("SELECT * FROM ML.CLASS_METRICS ORDER BY support DESC")
        if pc:
            st.markdown("**Per-class, on unseen dogs**")
            html_table([{"s": r["STATE"], "p": fmt(r["PRECISION"], 3),
                         "r": fmt(r["RECALL"], 3), "f": fmt(r["F1"], 3),
                         "n": fmt(r["SUPPORT"], 0)} for r in pc],
                       [("s", "state"), ("p", "precision"), ("r", "recall"),
                        ("f", "F1"), ("n", "support")])


# ===========================================================================
# TAB 8 — SHELTER REALITY.  Allowed to be quiet.
# ===========================================================================
def _page_7():
    st.markdown("#### Shelter reality")
    punch = rows("SELECT * FROM REF.V_SHELTER_PUNCHLINE ORDER BY syndrome_code")
    intake = rows("""
        SELECT month, SUM(n) AS n,
               SUM(IFF(is_behaviour_linked, n, 0)) AS behaviour_n
        FROM REF.V_AAC_INTAKE_TREND
        WHERE month >= '2015-01-01'
        GROUP BY month ORDER BY month
    """)
    los = rows("""
        SELECT breed_group, intake_condition, median_los_days, n
        FROM REF.V_AAC_LENGTH_OF_STAY
        WHERE n >= 50 ORDER BY median_los_days DESC LIMIT 40
    """)

    if not intake and not punch:
        empty_state("No shelter data.", "Run: python scripts/austin_sync.py")
    else:
        c1, c2 = st.columns(2)
        with c1:
            if PLOTLY and intake:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=[str(r["MONTH"])[:10] for r in intake],
                    y=[float(r["N"] or 0) for r in intake], mode="lines",
                    line=dict(color="#D6D3D1", width=1.4),
                    text=[f'{str(r["MONTH"])[:7]}: {fmt(r["N"],0)} dog intakes'
                          for r in intake], hoverinfo="text"))
                fig.add_trace(go.Scatter(
                    x=[str(r["MONTH"])[:10] for r in intake],
                    y=[float(r["BEHAVIOUR_N"] or 0) for r in intake], mode="lines",
                    line=dict(color=ACCENT, width=1.8),
                    text=[f'{str(r["MONTH"])[:7]}: {fmt(r["BEHAVIOUR_N"],0)} '
                          f'behaviour-linked' for r in intake], hoverinfo="text"))
                fig.update_layout(title="Austin Animal Center dog intakes — "
                                        f"<span style='color:{ACCENT}'>"
                                        "behaviour-linked</span> against the total",
                                  title_font_size=11)
                chart(clean_axes(fig), H_MD)
            if los:
                st.markdown("**Median length of stay, days**")
                st.markdown('<span class="tt-quiet">The cost of a behavioural '
                            'label, visible in days.</span>', unsafe_allow_html=True)
                if PLOTLY:
                    # Behaviour-flagged intakes highlighted against everything
                    # else, because that is the comparison the tab is making:
                    # the same shelter, the same breeds, a longer wait.
                    top = los[:16][::-1]
                    names = [f'{r["BREED_GROUP"]} · {r["INTAKE_CONDITION"]}'
                             for r in top]
                    beh = [str(r["INTAKE_CONDITION"] or "").upper() in
                           ("BEHAVIOR", "BEHAVIOUR") for r in top]
                    fig = go.Figure(go.Bar(
                        x=[float(r["MEDIAN_LOS_DAYS"] or 0) for r in top],
                        y=names, orientation="h",
                        marker=dict(color=[ACCENT if b else "#D6D3D1"
                                           for b in beh]),
                        text=[f'{n}<br>median {fmt(r["MEDIAN_LOS_DAYS"],1)} days'
                              f'<br>{fmt(r["N"],0)} animals'
                              for n, r in zip(names, top)],
                        hoverinfo="text"))
                    fig.update_layout(
                        title="longest waits first — "
                              f"<span style='color:{ACCENT}'>behaviour</span> "
                              "intakes against every other condition",
                        title_font_size=11)
                    chart(clean_axes(fig, y_zero_line=False),
                          bars(len(names)))
                html_table([{"b": r["BREED_GROUP"], "c": r["INTAKE_CONDITION"],
                             "d": fmt(r["MEDIAN_LOS_DAYS"], 1), "n": fmt(r["N"], 0)}
                            for r in los[:18]],
                           [("b", "breed group"), ("c", "intake condition"),
                            ("d", "median LOS"), ("n", "n")])

        with c2:
            if PLOTLY and punch:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=[r["SYNDROME_CODE"] for r in punch],
                    y=[float(r["TELLTAIL_DETECTIONS"] or 0) for r in punch],
                    marker=dict(color=INK), name="TELLTAIL",
                    text=[f'{r["SYNDROME_CODE"]} {r["SYNDROME_NAME"]}<br>'
                          f'{fmt(r["TELLTAIL_DETECTIONS"],0)} detections on a collar, '
                          f'at home' for r in punch], hoverinfo="text"))
                fig.add_trace(go.Bar(
                    x=[r["SYNDROME_CODE"] for r in punch],
                    y=[float(r["SHELTER_BEHAVIOUR_RECORDS"] or 0) for r in punch],
                    marker=dict(color=ACCENT), yaxis="y2", name="shelter",
                    text=[f'{fmt(r["SHELTER_BEHAVIOUR_RECORDS"],0)} shelter records '
                          f'in the same category' for r in punch], hoverinfo="text"))
                fig.update_layout(
                    title="the same categories, detected at home "
                          f"(<span style='color:{INK}'>dark</span>) and recorded at "
                          f"intake (<span style='color:{ACCENT}'>amber</span>)",
                    title_font_size=11, barmode="group",
                    yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                linecolor=BORDER, tickfont=dict(color=ACCENT)))
                chart(clean_axes(fig), H_MD)

            st.markdown(f"""
<div class="tt-card" style="font-size:13px;line-height:1.7">
Austin publishes a decade of intake and outcome records for every animal that
passes through its shelter. Behaviour is a named outcome reason in that data,
sitting alongside aggression and medical. Austin is a no-kill shelter and over
ninety percent of its animals are adopted, transferred or returned — which is
what makes the behavioural tail pointed rather than routine.
<br><br>
The categories on the left of this chart were detected on a collar, at home,
from movement alone. The categories on the right were written down at intake,
after the relationship had already broken down. They are the same categories.
<br><br>
<b>The dog was showing the pattern for eleven days before anyone noticed. The
warehouse noticed on day two.</b>
<br><br>
<span class="tt-quiet">Austin only. Not generalised to national claims.</span>
</div>""", unsafe_allow_html=True)


# ===========================================================================
# TAB 9 — PIPELINE.  How it was built.
# ===========================================================================
def _page_8():
    st.markdown("#### Pipeline")
    if stats:
        s = stats[0]
        metric_strip([
            ("raw rows, bulk",   fmt(s.get("RAW_ROWS_BULK"), 0)),
            ("raw rows, live",   fmt(s.get("RAW_ROWS_LIVE"), 0)),
            ("epochs",           fmt(s.get("EPOCHS_CLASSIFIED"), 0)),
            ("matches",          fmt(s.get("SYNDROME_MATCHES"), 0)),
            ("on chain",         fmt(s.get("ATTESTATIONS_ONCHAIN"), 0)),
        ])

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Dynamic Table refresh lag**")
        st.markdown('<span class="tt-quiet">Declared target lag against observed. '
                    'No cron anywhere in the feature, state, transition or '
                    'baseline layers.</span>', unsafe_allow_html=True)
        lag = rows("SELECT * FROM MARTS.V_DAG_LAG ORDER BY schema_name, object_name")
        if PLOTLY and lag:
            # Observed against declared, per object. The bar is what the DAG
            # actually did; the tick is what it promised. A bar past its tick is
            # a table falling behind its target lag, and that is the one thing
            # on this page worth noticing from across a room.
            names = [f'{r["SCHEMA_NAME"]}.{r["OBJECT_NAME"]}' for r in lag][::-1]
            back = lag[::-1]
            over = [float(r["MEAN_LAG_SEC"] or 0) > float(r["TARGET_LAG_SEC"] or 0)
                    for r in back]
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=[float(r["MEAN_LAG_SEC"] or 0) for r in back], y=names,
                orientation="h",
                marker=dict(color=["#B91C1C" if o else "#D6D3D1" for o in over]),
                text=[f'{n}<br>mean {fmt(r["MEAN_LAG_SEC"],1)}s'
                      f'<br>max {fmt(r["MAXIMUM_LAG_SEC"],1)}s'
                      f'<br>target {fmt(r["TARGET_LAG_SEC"],0)}s'
                      f'<br>state {r["STATE"]}' for n, r in zip(names, back)],
                hoverinfo="text"))
            fig.add_trace(go.Scatter(
                x=[float(r["TARGET_LAG_SEC"] or 0) for r in back], y=names,
                mode="markers",
                marker=dict(color=INK, size=9, symbol="line-ns-open",
                            line=dict(width=1.6, color=INK)),
                text=[f'target {fmt(r["TARGET_LAG_SEC"],0)}s' for r in back],
                hoverinfo="text"))
            fig.update_layout(title="observed mean lag (bar) against declared "
                                    "target (tick) — seconds",
                              title_font_size=11)
            chart(clean_axes(fig, y_zero_line=False), bars(len(names)))
        if lag:
            html_table([{"o": f'{r["SCHEMA_NAME"]}.{r["OBJECT_NAME"]}',
                         "t": fmt(r["TARGET_LAG_SEC"], 0),
                         "m": fmt(r["MEAN_LAG_SEC"], 1),
                         "x": fmt(r["MAXIMUM_LAG_SEC"], 1),
                         "s": r["STATE"]} for r in lag],
                       [("o", "object"), ("t", "target s"), ("m", "mean s"),
                        ("x", "max s"), ("s", "state")])
        else:
            empty_state("No refresh history yet.",
                        "Dynamic Tables report after their first refresh.")

        st.markdown("**ML function status**")
        fs = rows("SELECT fn, status, detail, rows_out, ran_at FROM ML.FUNCTION_STATUS "
                  "QUALIFY ROW_NUMBER() OVER (PARTITION BY fn ORDER BY ran_at DESC) = 1")
        if fs:
            html_table([{"f": r["FN"], "s": r["STATUS"], "n": fmt(r["ROWS_OUT"], 0),
                         "d": str(r["DETAIL"] or "")[:80]} for r in fs],
                       [("f", "function"), ("s", "status"), ("n", "rows"),
                        ("d", "detail")])

        st.markdown("**Cortex usage**")
        us = rows("SELECT * FROM AI.V_USAGE_SUMMARY")
        if us:
            html_table([{"f": r["FN"], "c": fmt(r["TOTAL_CALLS"], 0),
                         "d": fmt(r["CALLS_LAST_24H"], 0),
                         "b": fmt(r["BATCHES"], 0),
                         "x": fmt(r["FAILED_BATCHES"], 0)} for r in us],
                       [("f", "function"), ("c", "calls total"),
                        ("d", "calls 24h"), ("b", "batches"), ("x", "failed")])
            st.markdown('<span class="tt-quiet">Trial accounts without a payment '
                        'method are capped at roughly ten credits per day of AI '
                        'Function usage. Every call here was made by a task, into '
                        'a table, deduped on the finding key.</span>',
                        unsafe_allow_html=True)

        st.markdown("**Parameters**")
        st.markdown('<span class="tt-quiet">Every threshold in the build. No magic '
                    'numbers live in SQL.</span>', unsafe_allow_html=True)
        pr = rows("SELECT key, COALESCE(TO_VARCHAR(value_num), value_str) AS v, unit, "
                  "description FROM REF.PARAMS ORDER BY key")
        if pr:
            html_table([{"k": r["KEY"], "v": r["V"], "u": r["UNIT"],
                         "d": str(r["DESCRIPTION"])[:90]} for r in pr],
                       [("k", "key"), ("v", "value"), ("u", "unit"),
                        ("d", "what it does")])

    with c2:
        st.markdown("**Task history**")
        th = rows("SELECT task_name, state, scheduled_time, duration_ms, return_value, "
                  "error_message FROM MARTS.V_TASK_HISTORY "
                  "ORDER BY scheduled_time DESC LIMIT 40")
        if th:
            html_table([{"t": r["TASK_NAME"], "s": r["STATE"],
                         "w": str(r["SCHEDULED_TIME"])[:19],
                         "d": fmt(r["DURATION_MS"], 0),
                         "r": str(r["RETURN_VALUE"] or r["ERROR_MESSAGE"] or "")[:70]}
                        for r in th],
                       [("t", "task"), ("s", "state"), ("w", "scheduled"),
                        ("d", "ms"), ("r", "returned")])
        else:
            empty_state("No task runs yet.", "Tasks resume at the end of 11_tasks.sql.")

        st.markdown("**Credit burn**")
        cb = rows("SELECT * FROM MARTS.V_CREDIT_BURN ORDER BY hour")
        if PLOTLY and cb:
            fig = go.Figure(go.Scatter(
                x=[str(r["HOUR"])[:16] for r in cb],
                y=[float(r["CUMULATIVE_CREDITS"] or 0) for r in cb],
                mode="lines", line=dict(color=ACCENT, width=1.6),
                text=[f'{str(r["HOUR"])[:16]}<br>{fmt(r["CREDITS"],4)} this hour<br>'
                      f'{fmt(r["CUMULATIVE_CREDITS"],3)} cumulative' for r in cb],
                hoverinfo="text"))
            fig.update_layout(title="cumulative credits, 7 days (trial grant is 400)",
                              title_font_size=11)
            chart(clean_axes(fig), H_SM)

        st.markdown("**On-chain attestations**")
        st.markdown('<span class="tt-quiet">Snowflake stages the claim; a Node '
                    'bridge holds the key, signs and submits. <b>The keypair never '
                    'touches Snowflake.</b> Publish the claim, never the data.'
                    '</span>', unsafe_allow_html=True)
        pq = rows("SELECT * FROM ORACLE.V_PUBLISH_STATUS ORDER BY publish_id DESC LIMIT 40")
        if pq:
            trows = []
            for r in pq:
                sig = r.get("TX_SIGNATURE")
                url = r.get("EXPLORER_URL")
                link = (f'<a href="{url}" target="_blank" class="tt-mono">'
                        f'{str(sig)[:16]}…</a>') if url and sig else "—"
                trows.append({
                    "i": r["PUBLISH_ID"], "s": r["SUBJECT"], "c": r["SYNDROME_CODE"],
                    "v": r["SEVERITY"], "st": r["STATUS"], "l": fmt(r["LATENCY_S"], 0),
                    "t": link,
                })
            html_table(trows, [("i", "#"), ("s", "subject (hashed)"), ("c", "finding"),
                               ("v", "sev"), ("st", "status"), ("l", "latency s"),
                               ("t", "solscan")])
        else:
            empty_state("Nothing queued.",
                        "ORACLE.T_ATTEST stages findings at severity ≥ 2; "
                        "start the bridge with: npm run bridge")

        st.markdown("**Build log**")
        bl = rows("SELECT script_name, status, statements_ok, statements_ko, finished_at "
                  "FROM REF.BUILD_LOG WHERE script_name <> 'T_ROOT' "
                  "ORDER BY finished_at DESC LIMIT 15")
        if bl:
            html_table([{"s": r["SCRIPT_NAME"], "t": r["STATUS"],
                         "o": fmt(r["STATEMENTS_OK"], 0), "k": fmt(r["STATEMENTS_KO"], 0),
                         "w": str(r["FINISHED_AT"])[:19]} for r in bl],
                       [("s", "script"), ("t", "status"), ("o", "ok"),
                        ("k", "failed"), ("w", "finished")])

# ---------------------------------------------------------------------------
if st.session_state.get("_errors"):
    with st.expander(f"query errors ({len(st.session_state['_errors'])})"):
        for sql, err in st.session_state["_errors"][-25:]:
            st.markdown(f'<div class="tt-mono" style="font-size:11px">'
                        f'<b>{sql}</b><br>{err}</div>', unsafe_allow_html=True)

# ===========================================================================
# PAGE 10 — ASK TELLTAIL.  Cortex, grounded in the warehouse.
#
# Not a general chatbot bolted on to name-drop an LLM. The question is answered
# from rows: a compact factual context is assembled in SQL first — the pack
# summary, the syndrome counts, the sensitivity curve, the honest accuracy —
# and AI_COMPLETE is asked to answer ONLY from that, and to say so when the
# answer is not in there. The context it was given is printed underneath, so
# any claim on screen can be checked against the table it came from.
# ===========================================================================
def _page_9():
    facts = rows("""
        SELECT 'holdout' AS topic,
               'Dog-disjoint holdout accuracy ' ||
               COALESCE(TO_VARCHAR(ROUND(100 * MAX(m.holdout_accuracy), 2)), 'n/a') ||
               '% over ' || COALESCE(TO_VARCHAR(MAX(m.holdout_dogs)), '0') ||
               ' entirely unseen dogs, macro F1 ' ||
               COALESCE(TO_VARCHAR(ROUND(MAX(m.macro_f1), 3)), 'n/a') ||
               ', classifier ' || COALESCE(MAX(m.classifier), 'n/a') AS fact
        FROM ML.MODEL_SUMMARY m
        UNION ALL
        SELECT 'syndromes',
               'Syndrome ' || syndrome_code || ' (' || ANY_VALUE(syndrome_name) ||
               ', ' || ANY_VALUE(body_system) || '): ' || COUNT(*) ||
               ' matches over ' || COUNT(DISTINCT dog_id) ||
               ' dogs, mean confidence ' || ROUND(AVG(confidence), 3)
        FROM MARTS.SYNDROME_MATCHES GROUP BY syndrome_code
        UNION ALL
        SELECT 'not_firing',
               'Syndrome ' || c.syndrome_code || ' (' || c.syndrome_name ||
               ') has ZERO matches in this corpus.'
        FROM REF.SYNDROME_CATALOGUE c
        WHERE c.syndrome_code NOT IN (SELECT syndrome_code FROM MARTS.SYNDROME_MATCHES)
        UNION ALL
        SELECT 'sensitivity',
               'Sensitivity sweep ' || syndrome_code || '/' || variant ||
               ': ' || COUNT(*) || ' matches'
        FROM MARTS.SYNDROME_SENSITIVITY GROUP BY syndrome_code, variant
        UNION ALL
        SELECT 'states',
               'State ' || state || ': ' || COUNT(*) || ' epochs (' ||
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) || '%)'
        FROM MARTS.EPOCH_STATES GROUP BY state
        UNION ALL
        SELECT 'provenance',
               'state_source ' || state_source || ': ' || COUNT(*) || ' epochs'
        FROM MARTS.EPOCH_STATES GROUP BY state_source
        UNION ALL
        SELECT 'shelter',
               'Austin shelter, ' || syndrome_name || ': ' ||
               shelter_behaviour_records || ' shelter records versus ' ||
               telltail_detections || ' collar detections'
        FROM REF.V_SHELTER_PUNCHLINE
        UNION ALL
        SELECT 'scale',
               'Corpus: ' || (SELECT COUNT(*) FROM MARTS.EPOCH_STATES) ||
               ' classified epochs over ' ||
               (SELECT COUNT(*) FROM REF.DOG_INFO) || ' dogs'
    """)
    context = chr(10).join(f"- [{r['TOPIC']}] {r['FACT']}"
                           for r in facts if r.get("FACT"))

    examples = [
        ("Which fired?",    "Which syndromes fired, and which found nothing?"),
        ("How accurate?",   "How accurate is the classifier, honestly?"),
        ("Versus Austin?",  "How does what we detect compare to the Austin "
                            "shelter outcomes?"),
        ("Model or rule?",  "What fraction of states came from the model rather "
                            "than a heuristic?"),
    ]

    # ---- submission happens in callbacks ---------------------------------
    #
    # The message box belongs UNDER the transcript, but the answer has to be in
    # the history BEFORE the transcript renders or it appears one interaction
    # late. Streamlit runs widget callbacks ahead of the script body, so both
    # the box and the example buttons only park the question in session state;
    # the body below picks it up on the same run. Clearing tt_chat_box from
    # inside its own on_change is the documented way to empty a text input.
    def _chat_ask(text: str) -> None:
        st.session_state["tt_chat_pending"] = text

    def _chat_submit() -> None:
        text = (st.session_state.get("tt_chat_box") or "").strip()
        st.session_state["tt_chat_box"] = ""
        if text:
            st.session_state["tt_chat_pending"] = text

    def _chat_clear() -> None:
        st.session_state["tt_chat"] = []

    question = (st.session_state.pop("tt_chat_pending", None) or "").strip()
    history = st.session_state.setdefault("tt_chat", [])

    if question:
        prompt = (
            "You are TELLTAIL, a veterinary telemetry analyst. Answer the "
            "question using ONLY the facts listed below, which come from a "
            "Snowflake warehouse. Quote the specific numbers you use. If the "
            "facts do not contain the answer, say exactly what is missing "
            "rather than guessing. Be concise: at most 130 words. Never imply "
            "this is a diagnosis." + chr(10) * 2 + "FACTS:" + chr(10) + context
            + chr(10) * 2 + "QUESTION: " + question
        )
        try:
            ans = rows_live(
                "SELECT SNOWFLAKE.CORTEX.COMPLETE("
                + sq(CORTEX_MODEL) + ", " + sq(prompt) + ") AS a")
            history.append({"q": question, "a": one(ans, "A", ""),
                            "n": len(facts), "ok": True})
        except Exception as exc:  # noqa: BLE001
            history.append({"q": question, "ok": False,
                            "a": "Cortex did not answer: " + str(exc)[:280],
                            "n": len(facts)})
        st.session_state["tt_chat"] = history

    # ---- layout -----------------------------------------------------------
    #
    # ONE CENTRED COLUMN, not the full page width. `layout="wide"` is right for
    # a dashboard of charts and wrong for a conversation: run a transcript
    # across 1600px and every bubble becomes a single 200-character line with a
    # 48px avatar marooned at the far left. The outer columns are empty on
    # purpose — they are the margin.
    _pad_l, mid, _pad_r = st.columns([1, 4, 1])

    with mid:
        st.markdown(
            '<div class="tt-quiet" style="font-size:12px;line-height:1.5;'
            'margin-bottom:18px">'
            'Answers come from <span class="tt-mono">AI_COMPLETE</span> over a '
            'factual context assembled from the warehouse in SQL — printed in '
            'full at the bottom of this page, so any number here can be traced '
            'to the table it came from. The model is given the facts and your '
            'question, never the conversation, so it will not follow up on '
            'itself.</div>', unsafe_allow_html=True)

        if not history:
            chat_bubble(
                "Hello, I am TELLTAIL. Ask me about the pack, the syndromes, "
                "the classifier or the pipeline.\n\nI answer only from rows in "
                "this warehouse, and the exact facts I was handed are printed "
                "under every answer. If the answer is not in them I will say "
                "which table is missing rather than invent one.",
                is_user=False,
                meta=f"grounded in {len(facts)} facts from SQL · {CORTEX_MODEL}")
        for turn in history:
            chat_bubble(turn["q"], is_user=True)
            # A bubble is left plain unless the call FAILED. The reference
            # build has no rule down the side of a message and neither does
            # this, so the one time a red edge appears it means something.
            chat_bubble(turn["a"], is_user=False,
                        hue=None if turn.get("ok") else "#B91C1C",
                        meta=(f'AI_COMPLETE · {CORTEX_MODEL} · answered from '
                              f'{turn["n"]} warehouse facts' if turn.get("ok")
                              else "no answer — the call failed, nothing was "
                                   "substituted for it"))

        # A plain st.text_input, not st.chat_input: the label sits above the
        # field and Streamlit prints its own "Press Enter to apply" hint inside
        # it, which is the input in the reference build. st.chat_input tears
        # itself out of the flow and pins to the bottom of the viewport, below
        # the page footer and outside this column.
        st.text_input("Message: ", key="tt_chat_box", on_change=_chat_submit,
                      placeholder="Ask about the pack, the syndromes, the "
                                  "classifier or the pipeline")

        # Starters, sized to their own text. These were full-width buttons and
        # five of them across a wide page looked like the primary navigation of
        # the tab rather than four suggestions under a text box.
        chips = st.columns([2, 2, 2, 2, 1, 4])
        for c, (short, full) in zip(chips, examples):
            c.button(short, key="ex_" + short, on_click=_chat_ask, args=(full,),
                     use_container_width=True)
        chips[4].button("Clear", key="chat_clear", on_click=_chat_clear,
                        use_container_width=True)

        with st.expander(f"the exact context the model was given ({len(facts)} "
                         f"facts from SQL)"):
            st.code(context or "(no facts)", language="text")


PAGE_FN = {
    "Pack": _page_0, "Live Collar": _page_1, "Ethogram": _page_2,
    "Syndromes": _page_3, "Baselines": _page_4, "Vet Note": _page_5,
    "Drivers": _page_6, "Shelter Reality": _page_7, "Pipeline": _page_8,
    "Ask TELLTAIL": _page_9,
}
PAGE_FN[PAGE]()


st.markdown(
    f'<div class="tt-quiet" style="margin-top:24px;border-top:1px solid {BORDER};'
    f'padding-top:8px">TELLTAIL · dual-IMU canine telemetry, row pattern '
    f'recognition, and a portable attestation. Data: Vehkaoja et al., '
    f'<i>Data in Brief</i> 2022, University of Helsinki — 45 dogs, 27 breeds, '
    f'100 Hz, video-annotated. Shelter data: City of Austin open data portal. '
    f'Not a diagnostic device.</div>', unsafe_allow_html=True)
