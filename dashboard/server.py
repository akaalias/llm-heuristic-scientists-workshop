#!/usr/bin/env python3
"""A tiny, dependency-free dashboard for discovery runs.

Serves the research pages read from `heuristics/discovered/runs.csv`: the
research dashboard (`/research-dashboard`, table + live chart), the research
candidate grid (`/research-grid`), and the research experiment lineage
(`/research-lineage`) — plus the restaurant (`/`) and the problem (`/problem`).
Shared CSS/JS live in `static/` and are served from `/static/`.

The run log updates itself: the server watches the CSV and pushes new rows
over Server-Sent Events (the `/events` stream), so a new experiment shows up
at the top of the table — with a brief highlight — the moment the discovery
loop appends it. No reload needed. Each page is fully server-rendered first,
so it still shows the current state with JavaScript disabled; the live stream
and chart are pure enhancement.

Usage
-----
    python -m dashboard.server                 # serve on http://localhost:8000
    python -m dashboard.server --port 9000
    python -m dashboard.server --csv path/to/runs.csv
"""

import argparse
import csv
import html
import json
import math
import os
import re
import time
import unicodedata
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from problem_definition.model import (RESTAURANT, RECIPES, MENU, MENU_SECTIONS,
                                      TEAM_SIDES, STATION_CAPACITY)
from problem_definition.scenarios import (TRAINING_BATTERY, HIDDEN_TEST, STRESS,
                                          SUNDAY_GRAVY)

HERE        = Path(__file__).parent
TEMPLATE    = HERE / "index.html"
GRID_TMPL   = HERE / "grid.html"
LINEAGE_TMPL = HERE / "lineage.html"
RESTO_TMPL  = HERE / "restaurant.html"
PROBLEM_TMPL = HERE / "problem.html"
APPROACH_TMPL = HERE / "approach.html"
STATIC_DIR  = HERE / "static"          # shared CSS/JS, served from /static/
DEFAULT_CSV = HERE.parent / "heuristics" / "discovered" / "runs.csv"

STATIC_TYPES = {".css": "text/css", ".js": "text/javascript",
                ".svg": "image/svg+xml", ".json": "application/json",
                ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
TEXT_SUFFIXES = {".css", ".js", ".svg", ".json"}   # binary assets get no charset

POLL_S      = 1.0   # how often the SSE loop checks the CSV for changes
PING_EVERY  = 10    # send an SSE comment every Nth idle poll (detects drops)

# the research pages were renamed (/dashboard → /research-dashboard, etc.); 301
# the old paths to the new so existing links and bookmarks keep working.
OLD_ROUTE_REDIRECTS = {
    "/dashboard": "/research-dashboard", "/dashboard.html": "/research-dashboard",
    "/grid":      "/research-grid",      "/grid.html":      "/research-grid",
    "/lineage":   "/research-lineage",   "/lineage.html":   "/research-lineage",
}

# (csv field, column header, css class for the cell). Order = display order.
COLUMNS = [
    ("n",              "#",        "num"),
    ("run_id",         "Run",      "mono"),
    ("model",          "Model",    "mono"),
    ("library",        "Library",  "mono"),
    ("patience",       "Patience", "num"),
    ("slug",           "Experiment", "mono"),
    ("total_lateness", "Lateness", "num"),
    ("status",         "Status",   ""),
]


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _row_key(r: dict) -> str:
    """Stable id for a row, so the page can tell which rows are new."""
    return f'{r.get("run_id", "")}|{r.get("iter", "")}'


def iter_classified(rows: list[dict]):
    """Yield (row, kind) for each row in chronological order — the single source
    of truth for how the table, chart, and lineage colour a run.

    kind is 'kept' (matched or beat the lowest lateness so far, ties included →
    a running best), 'discarded' (a valid run worse than the best so far), or
    'failed' (errored / produced no score)."""
    best: float | None = None
    for r in rows:
        val = _to_float(r.get("total_lateness", ""))
        if r.get("status", "").startswith("failed") or val is None:
            yield r, "failed"
        elif best is None or val <= best:
            best = val
            yield r, "kept"
        else:
            yield r, "discarded"


def _status_cell(raw: str) -> str:
    """Render a status value. 'success' → ink small-caps; 'failed:Err' → rust
    small-caps with the error class spelled out."""
    if raw.startswith("failed"):
        err = raw.split(":", 1)[1] if ":" in raw else ""
        label = f"failed · {err}" if err else "failed"
        return f'<span class="status st-bad">{html.escape(label)}</span>'
    return f'<span class="status st-ok">{html.escape(raw or "—")}</span>'


def render_rows(rows: list[dict]) -> str:
    """Render the <tr>s for the table body, newest first.

    `rows` is in CSV (chronological) order; the running best is computed in that
    order (see `iter_classified`), then the rows are reversed for display so the
    most recent experiment sits at the top."""
    if not rows:
        return (f'<tr class="placeholder"><td colspan="{len(COLUMNS)}" class="empty">'
                "No runs yet — start one with "
                "<code>python -m discovery.discover</code>.</td></tr>")

    classified = list(iter_classified(rows))
    out = []
    for r, kind in reversed(classified):  # display newest first
        best_row = kind == "kept"
        is_pivot = r.get("pivot") == "1"
        cells = []
        for field, _, cls in COLUMNS:
            raw = r.get(field, "") or ""
            if field == "status":
                cell = _status_cell(raw)
            elif field == "slug":
                # derived from the title — the experiment's symbol, not a CSV field
                title = r.get("title", "")
                cell = html.escape(slug(title)) if title else '<span class="faint">—</span>'
            elif field == "library":
                # show just the filename; full path on hover + in the expand panel
                if raw:
                    base = raw.rsplit("/", 1)[-1]
                    cell = f'<span title="{html.escape(raw)}">{html.escape(base)}</span>'
                else:
                    cell = '<span class="faint">—</span>'
            elif field == "total_lateness":
                txt = raw if raw else "—"
                strong = " best" if best_row else ""
                cell = f'<span class="num{strong}">{html.escape(txt)}</span>'
            else:
                cell = html.escape(raw) if raw else '<span class="faint">—</span>'
            klass = f' class="{cls}"' if cls else ""
            cells.append(f"<td{klass}>{cell}</td>")
        tr_cls = ["row"]
        if best_row:
            tr_cls.append("best-row")
        if is_pivot:
            tr_cls.append("pivot-row")
        key = html.escape(_row_key(r))
        title = ' title="new approach after a plateau"' if is_pivot else ""
        out.append(f'<tr class="{" ".join(tr_cls)}" data-key="{key}"{title}>{"".join(cells)}</tr>')
    return "".join(out)


def chart_data(rows: list[dict]) -> list[dict]:
    """One point per iteration, in chronological order, for the live chart.

    Each point is {y, kind, key, pivot}: `y` is total_lateness (None for a
    failed run), `kind` comes from `iter_classified`, and `key` matches the
    table row's data-key so the chart and table can cross-highlight."""
    return [{
        "y": None if kind == "failed" else _to_float(r.get("total_lateness", "")),
        "kind": kind,
        "key": _row_key(r),
        "pivot": r.get("pivot") == "1",
    } for r, kind in iter_classified(rows)]


def render_table(rows: list[dict]) -> str:
    """The full <table>: a fixed head plus a `#rows` body the stream replaces."""
    head = "".join(f"<th>{html.escape(h)}</th>" for _, h, _ in COLUMNS)
    return (f"<table><thead><tr>{head}</tr></thead>"
            f'<tbody id="rows">{render_rows(rows)}</tbody></table>')


def meta(rows: list[dict]) -> tuple[str, str]:
    """(sub-heading text, 'updated …' text) for the current rows."""
    if not rows:
        return "Waiting for the first discovery run.", ""
    runs = {r.get("run_id", "") for r in rows}
    latest = max((r.get("timestamp", "") for r in rows), default="")
    sub = f"{len(rows)} iterations across {len(runs)} run(s)."
    updated = f"updated {latest}" if latest else ""
    return sub, updated


def load_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="") as f:
        return list(csv.DictReader(f))


# ---- experiment detail (row-expand) ---------------------------------------

def slug(title: str) -> str:
    """The experiment's symbol: 'This is the unique title' → 'this_is_the_unique_title'."""
    s = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")
    return s or "untitled"


_PY_TOKENS = re.compile(
    r"(?P<comment>#[^\n]*)"
    r"|(?P<string>[rbfRBF]{0,2}(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'))"
    r"|(?P<number>\b\d+\.?\d*\b)"
    r"|(?P<name>\b[A-Za-z_]\w*\b)"
)
_PY_KEYWORDS = {
    "def", "return", "if", "elif", "else", "for", "while", "in", "and", "or",
    "not", "None", "True", "False", "import", "from", "as", "with", "lambda",
    "class", "try", "except", "finally", "raise", "yield", "is", "global",
    "nonlocal", "pass", "break", "continue", "assert", "del", "await", "async",
}
_TOKEN_CLASS = {"comment": "c", "string": "s", "number": "n"}


def highlight_py(code: str) -> str:
    """Minimal, dependency-free Python highlighting → escaped HTML with spans
    (k=keyword, s=string, c=comment, n=number). Never fails on odd input."""
    out, i = [], 0
    for m in _PY_TOKENS.finditer(code):
        if m.start() > i:
            out.append(html.escape(code[i:m.start()]))
        kind, text = m.lastgroup, m.group()
        esc = html.escape(text)
        if kind == "name":
            out.append(f'<span class="k">{esc}</span>' if text in _PY_KEYWORDS else esc)
        else:
            out.append(f'<span class="{_TOKEN_CLASS[kind]}">{esc}</span>')
        i = m.end()
    out.append(html.escape(code[i:]))
    return "".join(out)


def _code_from_py(text: str) -> str:
    """Strip the saved module's leading docstring and the injected import line,
    leaving just the generated heuristic code."""
    t = text.lstrip()
    if t.startswith('"""'):
        end = t.find('"""', 3)
        if end != -1:
            t = t[end + 3:]
    lines = [ln for ln in t.splitlines()
             if not ln.strip().startswith("from problem_definition.model import")]
    return "\n".join(lines).strip("\n")


# ---- schedule Gantt (inline SVG, Tufte palette) ---------------------------

# muted order hues: a pale tint fills the bar (quiet — lengths dominate) with
# the saturated hue as a thin border + the arrival/due lines. Tufte: colour
# carries the data without shouting; the dish number reads in ink on the tint.
ORDER_COLORS = ["#8a6a1e", "#3f6e6e", "#8c2f1f", "#5a4b8a", "#4a6b3a",
                "#9b6a8a", "#2f5a8c", "#a8762e", "#6b6a60", "#5e7a7a"]
PALE_COLORS  = ["#e3d6b0", "#cddedc", "#e8c9bf", "#d6cfe6", "#d3ddc4",
                "#e6d2dd", "#cdd9e8", "#ecd8b8", "#dcdad0", "#d4dede"]


def _order_of(step: str) -> int:
    try:
        return int(step.split(".")[0][1:])   # "o1.d0.s2" → 1
    except (ValueError, IndexError):
        return 0


def _dish_of(step: str) -> int:
    try:
        return int(step.split(".")[1][1:])   # "o1.d0.s2" → 0
    except (ValueError, IndexError):
        return 0


def _pack_slots(items: list[dict]) -> tuple[dict[int, int], int]:
    """First-fit pack items (with start/end) into non-overlapping rows."""
    slot_free: list[float] = []
    slot_of: dict[int, int] = {}
    for k in sorted(range(len(items)), key=lambda i: items[i]["start"]):
        s, e = items[k]["start"], items[k]["end"]
        for i, free in enumerate(slot_free):
            if free <= s + 1e-9:
                slot_of[k], slot_free[i] = i, e
                break
        else:
            slot_of[k] = len(slot_free)
            slot_free.append(e)
    return slot_of, len(slot_free)


def _station_rows(entries: list[dict]) -> list[tuple[str, list[dict]]]:
    """Pack entries into display rows: one row per station, split into parallel
    slots when a station runs concurrent steps. Returns [(label, [entry])],
    where label is 'station' or 'station #slot'. Shared by both Gantt renderers."""
    rows = []
    for st in sorted({e["station"] for e in entries}):
        sub = [e for e in entries if e["station"] == st]
        slot_of, n = _pack_slots(sub)
        for slot in range(n):
            label = f"{st} #{slot}" if n > 1 else st
            rows.append((label, [sub[j] for j in range(len(sub)) if slot_of[j] == slot]))
    return rows


def _order_palettes(entries: list[dict]) -> tuple[dict[int, str], dict[int, str]]:
    """(saturated, pale) colour maps keyed by order id, assigned in id order."""
    oids = sorted({_order_of(e["step"]) for e in entries})
    color = {oid: ORDER_COLORS[i % len(ORDER_COLORS)] for i, oid in enumerate(oids)}
    pale  = {oid: PALE_COLORS[i % len(PALE_COLORS)] for i, oid in enumerate(oids)}
    return color, pale


def _gantt_ticks(horizon: float, count: int = 6) -> list[float]:
    if horizon <= 0:
        return [0]
    raw = horizon / count
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    out, t = [], 0.0
    while t <= horizon + 1e-9:
        out.append(round(t, 3))
        t += step
    return out


def _fmtnum(t: float) -> str:
    return str(int(t)) if float(t).is_integer() else f"{t:g}"


def gantt_svg(sched: dict) -> str:
    """Render a built schedule as an inline SVG Gantt: one row per station slot,
    bars coloured by order with the dish index inside, and per-order arrival
    (dotted) / due (dashed) lines. Returns '' if there's nothing to draw."""
    entries = sched.get("entries") or []
    if not entries:
        return ""
    orders = sched.get("orders") or []
    horizon = sched.get("horizon") or max((e["end"] for e in entries), default=1) or 1

    rows = _station_rows(entries)
    color, pale = _order_palettes(entries)

    W, L, R, T, B, rowH = 720, 84, 14, 26, 26, 22   # T leaves headroom for the outcome marks
    x0, x1 = L, W - R
    plot_bottom = T + len(rows) * rowH
    H = plot_bottom + B
    xf = lambda t: x0 + (t / horizon) * (x1 - x0)

    g = []
    for t in _gantt_ticks(horizon):
        x = xf(t)
        g.append(f'<line class="gg" x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{plot_bottom}"/>')
        g.append(f'<text class="gx" x="{x:.1f}" y="{H-8}" text-anchor="middle">{_fmtnum(t)}</text>')
    # when each order actually finishes (last end across all its steps) — used to
    # flag whether it met or missed its due time
    finish: dict[int, float] = {}
    for e in entries:
        oid = _order_of(e["step"])
        finish[oid] = max(finish.get(oid, 0.0), e["end"])
    for o in orders:
        if o.get("id") in color:
            c = color[o["id"]]
            for key, dash, op in (("arrival", "1 3", "0.5"), ("due", "4 3", "0.75")):
                x = xf(o[key])
                g.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{plot_bottom}" '
                         f'stroke="{c}" stroke-width="1" stroke-dasharray="{dash}" opacity="{op}"/>')

    # outcome marks above each due line: green ✓ if on time, red × if late.
    # Orders that share a due time would land on the same x, so group by due and
    # fan the marks out horizontally (centred on the line) to keep each legible.
    my = T - 11   # marker centre, in the headroom above the plot
    by_due: dict[float, list[tuple[int, bool]]] = {}
    for o in orders:
        if o.get("id") in color and o.get("due") is not None:
            met = finish.get(o["id"], 0.0) <= o["due"] + 1e-9
            by_due.setdefault(o["due"], []).append((o["id"], met))
    for due, marks in by_due.items():
        xd, k = xf(due), len(marks)
        for i, (oid, met) in enumerate(sorted(marks)):
            cx = xd + (i - (k - 1) / 2) * 11     # fan co-due marks out, centred on the line
            if met:
                g.append(f'<path class="gmet" d="M{cx-4:.1f},{my:.1f} '
                         f'L{cx-1:.1f},{my+3.5:.1f} L{cx+4.5:.1f},{my-4:.1f}"/>')
            else:
                r = 4
                g.append(f'<path class="gmiss" d="M{cx-r:.1f},{my-r}L{cx+r:.1f},{my+r}'
                         f'M{cx+r:.1f},{my-r}L{cx-r:.1f},{my+r}"/>')
    for y, (label, items) in enumerate(rows):
        cy = T + y * rowH
        g.append(f'<text class="gy" x="{L-8}" y="{cy+rowH/2+3:.1f}" text-anchor="end">{html.escape(label)}</text>')
        for e in items:
            oid, dish = _order_of(e["step"]), _dish_of(e["step"])
            bx, bw = xf(e["start"]), max(1.2, xf(e["end"]) - xf(e["start"]))
            by, bh = cy + 3, rowH - 6
            name = e.get("dish_name") or e.get("step", "")
            tip = (f'{name} — order o{oid}, dish {dish+1} · {e["station"]} · '
                   f't {_fmtnum(e["start"])}–{_fmtnum(e["end"])}')
            g.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh}" rx="1.5" '
                     f'fill="{pale[oid]}" stroke="{color[oid]}" stroke-width="1" '
                     f'data-tip="{html.escape(tip, quote=True)}"/>')
            if bw >= 12:
                g.append(f'<text class="gd" x="{bx+bw/2:.1f}" y="{by+bh/2+3:.1f}" '
                         f'text-anchor="middle">{dish+1}</text>')
    g.append(f'<line class="ga" x1="{x0}" y1="{plot_bottom}" x2="{x1}" y2="{plot_bottom}"/>')
    return (f'<svg class="gantt" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'role="img" aria-label="schedule gantt">{"".join(g)}</svg>')


def load_schedule(csv_dir: Path, fname: str) -> dict | None:
    """Read the sidecar schedule JSON saved next to an iteration's .py. It holds
    one schedule per battery sample under `samples` (older single-schedule files
    are still accepted)."""
    if not fname:
        return None
    sp = csv_dir / (Path(fname).stem + ".schedule.json")
    if sp.exists():
        try:
            return json.loads(sp.read_text())
        except (ValueError, OSError):
            return None
    return None


def schedule_samples(sched: dict | None) -> list[dict]:
    """The per-sample schedules in a sidecar (the new `samples` list, or the
    old single schedule wrapped in a list)."""
    if not sched:
        return []
    if "samples" in sched:
        return sched["samples"]
    return [sched] if sched.get("entries") else []


def gantt_thumb(sched: dict) -> str:
    """A label-free thumbnail Gantt for the small-multiples grid: just the
    coloured bars on their station rows — no numbers, axes, labels, or tips."""
    entries = sched.get("entries") or []
    if not entries:
        return ""
    horizon = sched.get("horizon") or max((e["end"] for e in entries), default=1) or 1
    rows = _station_rows(entries)            # labels unused here — bars only
    color, pale = _order_palettes(entries)

    W, pad, rowH, barH = 240, 3, 8, 6
    x0, x1 = pad, W - pad
    H = pad * 2 + len(rows) * rowH
    xf = lambda t: x0 + (t / horizon) * (x1 - x0)

    g = []
    for y, (_label, items) in enumerate(rows):
        cy = pad + y * rowH
        for e in items:
            bx = xf(e["start"])
            bw = max(0.8, xf(e["end"]) - xf(e["start"]))
            oid = _order_of(e["step"])
            g.append(f'<rect x="{bx:.1f}" y="{cy+1:.1f}" width="{bw:.1f}" height="{barH}" rx="1" '
                     f'fill="{pale[oid]}" stroke="{color[oid]}" stroke-width="0.5"/>')
    return (f'<svg class="thumb" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'role="img" aria-label="schedule thumbnail">{"".join(g)}</svg>')


# ---- overlay stage: many experiments stacked on ONE shared grid ------------
# Geometry shared by the axis layer and every bar layer so they register
# pixel-for-pixel. A left gutter (OV_L) holds the station names, drawn once.
OV_W, OV_L, OV_R, OV_T, OV_B, OV_ROWH = 760, 104, 18, 10, 42, 20


def overlay_geometry(scheds: list[dict]) -> tuple[list[str], dict[str, int], float]:
    """The canonical layout the whole group shares: station-slot row labels, a
    station→base-row map, and ONE horizon (the max across the group, so every
    layer is drawn to the same time scale and bars actually line up)."""
    stations = sorted({e["station"] for s in scheds for e in (s.get("entries") or [])})
    maxslots: dict[str, int] = {}
    horizon = 1.0
    for s in scheds:
        ents = s.get("entries") or []
        horizon = max(horizon, s.get("horizon") or max((e["end"] for e in ents), default=1) or 1)
        for st in stations:
            sub = [e for e in ents if e["station"] == st]
            if sub:
                _so, n = _pack_slots(sub)
                maxslots[st] = max(maxslots.get(st, 1), n)
            maxslots.setdefault(st, 1)
    labels, base = [], {}
    for st in stations:
        base[st] = len(labels)
        n = maxslots[st]
        labels += [f"{st} #{slot}" if n > 1 else st for slot in range(n)]
    return labels, base, horizon


def _ov_x(horizon: float):
    x0, x1 = OV_L, OV_W - OV_R
    return lambda t: x0 + (t / horizon) * (x1 - x0)


def overlay_axis(labels: list[str], horizon: float) -> str:
    """The single, un-blended reference layer: station names down the gutter,
    faint time gridlines and a baseline. Sits atop the blended bar layers."""
    n_rows = len(labels)
    plot_bottom = OV_T + n_rows * OV_ROWH
    H = plot_bottom + OV_B
    x0, x1 = OV_L, OV_W - OV_R
    xf = _ov_x(horizon)
    g = []
    # faint gridlines + a small tick below the baseline at each labelled time;
    # numbers ride just under the ticks, the unit named once at the right.
    for t in _gantt_ticks(horizon):
        x = xf(t)
        g.append(f'<line class="ov-gg" x1="{x:.1f}" y1="{OV_T}" x2="{x:.1f}" y2="{plot_bottom}"/>')
        g.append(f'<line class="ov-tick" x1="{x:.1f}" y1="{plot_bottom}" x2="{x:.1f}" y2="{plot_bottom+4}"/>')
        g.append(f'<text class="ov-gx" x="{x:.1f}" y="{plot_bottom+17:.1f}" text-anchor="middle">{_fmtnum(t)}</text>')
    g.append(f'<text class="ov-axis-title" x="{x1:.1f}" y="{plot_bottom+33:.1f}" '
             f'text-anchor="end">minutes from first seating</text>')
    for y, label in enumerate(labels):
        cy = OV_T + y * OV_ROWH
        g.append(f'<text class="ov-gy" x="{OV_L-12}" y="{cy+OV_ROWH/2+3:.1f}" '
                 f'text-anchor="end">{html.escape(label)}</text>')
    g.append(f'<line class="ov-ga" x1="{x0}" y1="{plot_bottom}" x2="{x1}" y2="{plot_bottom}"/>')
    return (f'<svg class="ov-axis-svg" viewBox="0 0 {OV_W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'aria-hidden="true">{"".join(g)}</svg>')


# met / missed colours for the overlaid deadline lines (no ✓/× marks here —
# the colour alone carries the outcome, and 46 stacked lines tell the story).
OV_MET, OV_MISS = "#4a7a3a", "#8c2f1f"


def overlay_bars(sched: dict, labels: list[str], base: dict[str, int], horizon: float) -> str:
    """One experiment's bars, placed on the canonical rows at the shared time
    scale — a bar-only layer meant to be stacked under a blend mode."""
    entries = sched.get("entries") or []
    H = OV_T + len(labels) * OV_ROWH + OV_B
    xf = _ov_x(horizon)
    color, pale = _order_palettes(entries)
    barH = OV_ROWH - 8
    g = []
    for st in sorted({e["station"] for e in entries}):
        sub = [e for e in entries if e["station"] == st]
        slot_of, _n = _pack_slots(sub)
        for j, e in enumerate(sub):
            cy = OV_T + (base[st] + slot_of[j]) * OV_ROWH
            bx = xf(e["start"])
            bw = max(1.0, xf(e["end"]) - xf(e["start"]))
            oid = _order_of(e["step"])
            g.append(f'<rect x="{bx:.1f}" y="{cy+4:.1f}" width="{bw:.1f}" height="{barH}" rx="1.5" '
                     f'fill="{pale[oid]}" stroke="{color[oid]}" stroke-width="0.75"/>')
    return (f'<svg class="ov-bars-svg" viewBox="0 0 {OV_W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'aria-hidden="true">{"".join(g)}</svg>')


def overlay_deadlines(sched: dict, labels: list[str], horizon: float) -> str:
    """One experiment's order-deadline lines, coloured green (met) / red (late).
    Kept in its OWN layer so it can always composite Normal — the green/red stays
    legible whatever blend mode the bars use. Each order finishes at the last end
    across its steps; green if that beat the due time, red if it ran late."""
    entries = sched.get("entries") or []
    plot_bottom = OV_T + len(labels) * OV_ROWH
    H = plot_bottom + OV_B
    xf = _ov_x(horizon)
    finish: dict[int, float] = {}
    for e in entries:
        oid = _order_of(e["step"])
        finish[oid] = max(finish.get(oid, 0.0), e["end"])
    g = []
    for o in (sched.get("orders") or []):
        due = o.get("due")
        if due is None:
            continue
        met = finish.get(o.get("id"), 0.0) <= due + 1e-9
        x = xf(due)
        g.append(f'<line class="ov-due" x1="{x:.1f}" y1="{OV_T}" x2="{x:.1f}" y2="{plot_bottom}" '
                 f'stroke="{OV_MET if met else OV_MISS}" stroke-width="2" stroke-dasharray="4 3"/>')
    return (f'<svg class="ov-due-svg" viewBox="0 0 {OV_W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'aria-hidden="true">{"".join(g)}</svg>')


def _lat_key(v) -> float:
    f = _to_float(str(v))
    return f if f is not None else float("inf")


# sample name (e.g. "training_v2") → human title ("The Early Rush"), for labels.
SCENARIO_TITLES = {sc.name: (getattr(sc, "title", "") or sc.name)
                   for sc in [*TRAINING_BATTERY, HIDDEN_TEST, STRESS, SUNDAY_GRAVY]}

# sample name → the narrative blurb (the floor's-eye description of the night).
SCENARIO_BLURBS = {sc.name: (getattr(sc, "blurb", "") or "")
                   for sc in [*TRAINING_BATTERY, HIDDEN_TEST, STRESS, SUNDAY_GRAVY]}


def scenario_title(name: str) -> str:
    return SCENARIO_TITLES.get(name, name)


def scenario_blurb(name: str) -> str:
    return SCENARIO_BLURBS.get(name, "")


def render_grid(rows: list[dict], csv_dir: Path) -> str:
    """Small-multiples grid grouped BY SAMPLE: a section per battery sample,
    and under each, every experiment's schedule on that sample (best on that
    sample first). Each thumbnail links back to its row on the dashboard."""
    have = [(r, load_schedule(csv_dir, r.get("file", ""))) for r in rows]
    have = [(r, s) for r, s in have if schedule_samples(s)]
    if not have:
        return ('<p class="empty">No schedules yet — run '
                "<code>python -m discovery.discover</code> first.</p>")

    # group: sample name → [(row, that sample's schedule)], in first-seen order
    groups: dict[str, list] = {}
    order: list[str] = []
    for r, sched in have:
        for s in schedule_samples(sched):
            name = s.get("name", "sample")
            if name not in groups:
                groups[name] = []
                order.append(name)
            groups[name].append((r, s))

    sections = []
    for name in order:
        items = sorted(groups[name], key=lambda rs: _lat_key(rs[1].get("lateness")))
        cells = []
        n = len(items)
        # all layers share ONE grid (canonical station rows + a common horizon)
        # so bars register pixel-for-pixel and clusters emerge where they agree.
        labels, base, horizon = overlay_geometry([s for _r, s in items])
        bar_layers, due_layers = [], []
        for r, s in items:
            key = _row_key(r)
            title = r.get("title", "") or "Untitled"
            lat = s.get("lateness", "")
            cells.append(
                f'<a class="cell" href="/research-dashboard#exp={quote(key)}" title="{html.escape(title)}">'
                f'<div class="cell-thumb">{gantt_thumb(s)}</div>'
                f'<div class="cell-cap"><span class="cell-n">#{html.escape(r.get("n",""))}</span>'
                f'<span class="cell-title">{html.escape(title)}</span>'
                f'<span class="cell-lat">{html.escape(str(lat))}</span></div></a>')
            bar_layers.append(f'<div class="ov-layer">{overlay_bars(s, labels, base, horizon)}</div>')
            due_layers.append(f'<div class="ov-due-layer">{overlay_deadlines(s, labels, horizon)}</div>')
        nm_title = scenario_title(name)
        id_html = (f'<span class="sample-id">{html.escape(name)}</span>'
                   if nm_title != name else "")
        blurb = scenario_blurb(name)
        desc_html = (f'<p class="sample-desc">{html.escape(blurb)}</p>' if blurb else "")
        ov_id = "ov-" + re.sub(r"[^a-zA-Z0-9_-]", "-", name)
        # the blend-mode toolbar — Photoshop-style layer styles to hunt for the
        # most legible default. data-op is the per-layer opacity that mode wants.
        modes = [
            ("normal",   "Normal",     f"{1.0/n:.4f}" if n else "1"),
            ("multiply", "Multiply",   "0.55"),
        ]
        btns = "".join(
            f'<button class="ov-mode{" is-on" if i == 0 else ""}" type="button" '
            f'data-ov-mode="{m}" data-op="{op}">{html.escape(lbl)}</button>'
            for i, (m, lbl, op) in enumerate(modes))
        toolbar = f'<div class="ov-modes" role="group" aria-label="Blend mode">{btns}</div>'
        modal = (
            f'<div class="ov-modal" id="{ov_id}" hidden>'
            f'<div class="ov-backdrop" data-ov-close></div>'
            f'<div class="ov-dialog" role="dialog" aria-modal="true" aria-label="Overlay of {html.escape(nm_title)} schedules">'
            f'<header class="ov-head"><div><span class="ov-title">{html.escape(nm_title)}</span>'
            f'<span class="ov-sub">all {n} experiments, overlaid</span></div>'
            f'{toolbar}'
            f'<button class="ov-close" type="button" data-ov-close aria-label="Close">&times;</button></header>'
            f'<div class="ov-stage" data-blend="normal" style="--ov-op:{1.0/n:.4f};--ov-op-due:{1.0/n:.4f}">'
            f'{"".join(bar_layers)}'
            f'{"".join(due_layers)}'
            f'<div class="ov-axis">{overlay_axis(labels, horizon)}</div>'
            f'</div>'
            f'<p class="ov-note">All {n} schedules for this night on one grid: where many '
            f'agree the bars stack into solid blocks, lone choices stay faint. Each order&rsquo;s '
            f'deadline is a dashed line &mdash; <span class="ov-met">green where met</span>, '
            f'<span class="ov-miss">red where late</span>. <strong>Multiply</strong> drives the '
            f'consensus toward black.</p>'
            f'</div></div>')
        sections.append(
            f'<section class="sample"><h2 class="sample-h">'
            f'<span class="sample-title">{html.escape(nm_title)}{id_html}</span>'
            f'<span class="cnt"><span class="cnt-n">{n} experiments</span>'
            f'<button class="ov-btn" type="button" data-ov-open="{ov_id}">Overlay</button></span></h2>'
            f'{desc_html}'
            f'<div class="grid">{"".join(cells)}</div>{modal}</section>')
    return "".join(sections)


def experiment_detail(rows: list[dict], key: str, csv_dir: Path) -> dict | None:
    """Everything the row-expand panel needs for one experiment: symbol, title,
    summary, highlighted code (read from its .py), a Gantt per battery sample,
    and parent experiments resolved to their symbols. None if the key isn't
    found. `thumb_svg` (first sample) is also returned for the lineage popover."""
    by_key = {_row_key(r): r for r in rows}
    r = by_key.get(key)
    if r is None:
        return None

    code = ""
    fname = r.get("file", "")
    if fname:
        p = csv_dir / fname
        if p.exists():
            code = _code_from_py(p.read_text())
    samples = schedule_samples(load_schedule(csv_dir, fname))   # one per battery sample
    sched0 = samples[0] if samples else None                    # thumb (lineage popover)

    parents = []
    for pk in (r.get("parents", "") or "").split(";"):
        pk = pk.strip()
        pr = by_key.get(pk)
        if pr:
            ptitle = pr.get("title", "") or "Untitled"
            parents.append({"key": pk, "n": pr.get("n", ""),
                            "title": ptitle, "symbol": slug(ptitle)})

    title = r.get("title", "") or "Untitled heuristic"
    return {
        "key": key,
        "n": r.get("n", ""),
        "symbol": slug(title),
        "title": title,
        "summary": r.get("summary", "") or "",
        "schedules": [{"name": s.get("name", ""),
                       "title": scenario_title(s.get("name", "")),
                       "lateness": s.get("lateness", ""),
                       "svg": gantt_svg(s)} for s in samples],
        "thumb_svg": gantt_thumb(sched0) if sched0 else "",
        "code_html": highlight_py(code) if code else "",
        "parents": parents,
        "lateness": r.get("total_lateness", "") or "",
        "time": r.get("timestamp", "") or "",
        "status": r.get("status", "") or "",
        "params": {                              # the run's exact CLI inputs
            "model":       r.get("model", "") or "",
            "api":         r.get("api", "") or "",
            "iterations":  r.get("iterations", "") or "",
            "patience":    r.get("patience", "") or "",
            "meta_pivots": r.get("meta_pivots", "") or "",
            "library":     r.get("library", "") or "",
        },
    }


def research_intro() -> str:
    """Shared lead-in for the three research pages (dashboard, candidate grid,
    experiment lineage): what they are, and a link back to the restaurant and the
    problem they exist to solve. Injected into each page's <!--RESEARCH_INTRO-->
    slot so the framing reads identically across all three."""
    name = html.escape(RESTAURANT["name"])
    return (
        '<div class="research-intro"><p>'
        'These pages follow an autonomous AI as it searches for a single rule to run '
        f'the kitchen at <a href="/">{name}</a> — which dish to start next so every '
        'table is served on time. The kitchen it works within, and why getting that '
        'order right is hard, are laid out in <a href="/problem">the problem</a>.'
        '</p></div>'
    )


# the shared top-left navigation, in display order: (page key, href, label,
# indented under the approach?). Each page injects render_nav(<its key>) into its
# <!--NAV--> slot — one source of truth, so a label or link changes in one place.
NAV_ITEMS = [
    ("restaurant", "/",                   "The restaurant",              False),
    ("problem",    "/problem",            "The problem",                 False),
    ("approach",   "/approach",           "Our approach",                False),
    ("dashboard",  "/research-dashboard", "Research Dashboard",          True),
    ("grid",       "/research-grid",      "Research Candidate Grid",     True),
    ("lineage",    "/research-lineage",   "Research Experiment Lineage", True),
]


def render_nav(current: str) -> str:
    """The shared nav. `current` is the active page's key (see NAV_ITEMS): it
    renders as a non-link, marked active, so it stays visible instead of
    vanishing; the research views are indented under 'Our approach'."""
    out = []
    for key, href, label, sub in NAV_ITEMS:
        cls = " ".join(c for c, on in (("nav-sub", sub), ("nav-here", key == current)) if on)
        attr = f' class="{cls}"' if cls else ""
        out.append(f'<span{attr}>{html.escape(label)}</span>' if key == current
                   else f'<a{attr} href="{href}">{html.escape(label)}</a>')
    return f'<div class="nav">{"".join(out)}</div>'


def render_page(template: str, csv_path: Path, target: float) -> str:
    rows = load_rows(csv_path)
    sub, updated = meta(rows)
    chart = json.dumps({"points": chart_data(rows), "target": target})
    return (template
            .replace("<!--NAV-->", render_nav("dashboard"))
            .replace("<!--TABLE-->", render_table(rows))
            .replace("<!--SUB-->", html.escape(sub))
            .replace("<!--UPDATED-->", html.escape(updated))
            .replace("<!--RESEARCH_INTRO-->", research_intro())
            .replace("<!--CHARTDATA-->", chart))


def lineage_data(rows: list[dict]) -> dict:
    """Nodes for the lineage graph, in discovery (experiment-number) order. Each
    node carries its key, n, title, lateness, kind (kept/discarded/failed, like
    the chart), and the parent keys it was derived from."""
    def n_of(r):
        s = str(r.get("n", ""))
        return int(s) if s.isdigit() else 0
    nodes = []
    for r, kind in iter_classified(sorted(rows, key=n_of)):
        title = r.get("title", "") or "Untitled"
        nodes.append({
            "key": _row_key(r), "n": r.get("n", ""),
            "title": title, "symbol": slug(title),
            "summary": r.get("summary", "") or "",
            "lateness": _to_float(r.get("total_lateness", "")), "kind": kind,
            "pivot": r.get("pivot") == "1",
            "parents": [p for p in (r.get("parents", "") or "").split(";") if p],
        })
    return {"nodes": nodes}


def render_lineage_page(template: str, csv_path: Path, target: float) -> str:
    rows = load_rows(csv_path)
    data = lineage_data(rows)
    data["target"] = target
    n = len(data["nodes"])
    sub = (f"{n} experiment{'' if n == 1 else 's'}, left → right in discovery order; "
           "each arc links an experiment to the parent it built on. Hover to trace its "
           "ancestry back to the root; click to open it on the research dashboard." if n else "No experiments yet.")
    return (template
            .replace("<!--NAV-->", render_nav("lineage"))
            .replace("<!--LINEAGEDATA-->", json.dumps(data))
            .replace("<!--RESEARCH_INTRO-->", research_intro())
            .replace("<!--SUB-->", html.escape(sub)))


# ---- restaurant one-pager (public face; static metadata, no CSV) ----------

def render_ratings(ratings: dict) -> str:
    """The ratings strip. Each source becomes a labelled value with a sub-line;
    layout adapts to whichever keys RESTAURANT['ratings'] happens to carry."""
    def cell(src: str, val: str, out: str, sub: str) -> str:
        out_html = f'<span class="r-out"> / {html.escape(out)}</span>' if out else ""
        sub_html = f'<div class="r-sub">{html.escape(sub)}</div>' if sub else ""
        return (f'<div class="rating"><div class="r-src">{html.escape(src)}</div>'
                f'<div class="r-val">{html.escape(val)}{out_html}</div>{sub_html}</div>')

    out = []
    for key, r in ratings.items():
        if "award" in r:   # michelin-style: an award, not a score
            sub = f"since {r['since']}" if r.get("since") else ""
            out.append(cell(key.title(), r["award"], "", sub))
        else:              # numeric score out of a max, with a volume/source sub-line
            reviews = r.get("reviews")
            sub = f"{reviews:,} reviews" if reviews else (r.get("source", "") or "")
            label = "Critics" if key == "local_critics" else key.title()
            out.append(cell(label, f"{r.get('score','')}", str(r.get("out_of", "")), sub))
    return "".join(out)


def render_menu(recipes: dict, menu: dict, sections: list[str]) -> str:
    """The à la carte menu, grouped into sections. Driven by RECIPES (what the
    kitchen can actually cook), looking each dish up in MENU for its name, blurb
    and price; a dish with no menu copy is skipped rather than half-rendered."""
    by_section: dict[str, list[str]] = {s: [] for s in sections}
    extras: dict[str, list[str]] = {}
    for dish in recipes:
        m = menu.get(dish)
        if not m:
            continue
        item = (
            f'<div class="menu-item"><div class="menu-item-top">'
            f'<span class="mi-name">{html.escape(m["name"])}</span>'
            f'<span class="mi-dots"></span>'
            f'<span class="mi-price">&euro;{html.escape(str(m["price"]))}</span></div>'
            f'<div class="mi-blurb">{html.escape(m["blurb"])}</div></div>')
        bucket = by_section if m.get("section") in by_section else extras
        bucket.setdefault(m.get("section", "More"), []).append(item)

    ordered = sections + [s for s in extras if s not in sections]
    groups = []
    for s in ordered:
        items = by_section.get(s) or extras.get(s) or []
        if not items:
            continue
        groups.append(f'<div class="menu-group"><h3 class="menu-group-h">{html.escape(s)}</h3>'
                      f'{"".join(items)}</div>')
    return "".join(groups)


PORTRAIT_DIR = STATIC_DIR / "portraits"   # PNGs from tools.generate_portraits
SCENE_DIR    = STATIC_DIR / "scenes"      # PNGs from tools.generate_scenes
MARK_DIR     = STATIC_DIR / "marks"       # PNGs from tools.generate_mark


def _asset_url(category: str, stem: str) -> str:
    """URL for an image, preferring the small web/<stem>.webp variant (from
    tools.optimize_images) over the full-res original. '' if neither exists."""
    base = STATIC_DIR / category
    for rel in (f"web/{stem}.webp", f"web/{stem}.png", f"{stem}.png"):
        if (base / rel).is_file():
            return f"/static/{category}/{rel}"
    return ""


def _framed(inner: str, variant: str, tag: str = "span", extra: str = "") -> str:
    """Wrap a picture (or initials monogram) in the shared framed-picture markup:
    a gilt frame + wide passe-partout mat with inset shadows, styled by `.framed`
    and the `.framed--<variant>` modifier. The frame/mat live on the wrapper and a
    ::after overlay casts the mat's shadow onto the picture (see restaurant.css)."""
    cls = f"framed framed--{variant}" + (f" {extra}" if extra else "")
    return f'<{tag} class="{cls}">{inner}</{tag}>'


def scene_figure(name: str, alt: str, side: str) -> str:
    """A framed scene <figure> (engraving) if a scenes/<name> image exists, else
    '' so the section collapses to text only. `side` is 'left' or 'right'."""
    src = _asset_url("scenes", name)
    if not src:
        return ""
    img = (f'<img class="framed-img" src="{src}" '
           f'alt="{html.escape(alt)}" loading="lazy">')
    return _framed(img, "scene", tag="figure", extra=f"scene-fig scene-fig--{side}")


def mark_img(name: str, cls: str) -> str:
    """A signature mark (e.g. the fox) from marks/<name>, or '' if absent."""
    src = _asset_url("marks", name)
    if not src:
        return ""
    return f'<img class="{cls}" src="{src}" alt="" aria-hidden="true">'


def _portrait_slug(name: str) -> str:
    """'Élise Marchand' -> 'elise_marchand'. Must match tools.generate_portraits."""
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")


def _initials(name: str) -> str:
    parts = [w for w in re.split(r"\s+", name or "") if w]
    return "".join(w[0] for w in (parts[:1] + parts[-1:])).upper() if parts else "·"


def avatar(name: str, variant: str) -> str:
    """A framed portrait if static/portraits/<slug>.png exists, else a framed
    initials monogram so the layout is identical with or without generated art.
    `variant` is 'avatar' (team card) or 'chef' (featured)."""
    slug = _portrait_slug(name)
    src = _asset_url("portraits", slug) if slug else ""
    if src:
        inner = (f'<img class="framed-img" src="{src}" '
                 f'alt="{html.escape(name)}" loading="lazy">')
    else:
        inner = (f'<span class="framed-img framed-img--mono" aria-hidden="true">'
                 f'{html.escape(_initials(name))}</span>')
    return _framed(inner, variant)


def render_team(people: list[dict], sides: list[str], exclude: set[str] | None = None) -> str:
    """The team roster, grouped into Kitchen / Front of house. Each person is a
    card (name, role, years, note, bio); names in `exclude` are skipped (the chef
    is featured separately). A `side` not in `sides` falls into its own trailing
    group, and a group with nobody left in it is dropped."""
    exclude = exclude or set()
    def card(p: dict) -> str:
        since = f'<span class="m-since">since {html.escape(str(p["since"]))}</span>' if p.get("since") else ""
        note  = f'<div class="m-note">{html.escape(p["note"])}</div>' if p.get("note") else ""
        bio   = f'<div class="m-bio">{html.escape(p["bio"])}</div>' if p.get("bio") else ""
        return (f'<div class="member">'
                f'<div class="m-head">{avatar(p.get("name",""), "avatar")}'
                f'<div class="m-id"><div class="m-top">'
                f'<span class="m-name">{html.escape(p.get("name",""))}</span>{since}</div>'
                f'<div class="m-role">{html.escape(p.get("role",""))}</div></div></div>'
                f'{note}{bio}</div>')

    people = [p for p in people if p.get("name") not in exclude]
    ordered = sides + [s for s in dict.fromkeys(p.get("side", "") for p in people) if s and s not in sides]
    groups = []
    for s in ordered:
        members = [p for p in people if p.get("side", "") == s]
        if not members:
            continue
        groups.append(f'<div class="team-group"><h3 class="team-group-h">{html.escape(s)}</h3>'
                      f'<div class="team">{"".join(card(p) for p in members)}</div></div>')
    return "".join(groups)


def _chef(people: list[dict]) -> dict | None:
    """The featured chef: the first person whose role names them an owner-chef
    (falls back to any 'chef', then None)."""
    return (next((p for p in people if "chef" in p.get("role", "").lower()
                                    and "owner" in p.get("role", "").lower()), None)
            or next((p for p in people if "chef" in p.get("role", "").lower()), None))


def render_chef(people: list[dict]) -> str:
    """The featured 'The chef' block: name, role and full biography."""
    c = _chef(people)
    if not c:
        return ""
    return (f'<div class="chef">{avatar(c.get("name",""), "chef")}'
            f'<div class="chef-text">'
            f'<p class="chef-name">{html.escape(c.get("name",""))}</p>'
            f'<p class="chef-role">{html.escape(c.get("role",""))}</p>'
            f'<p class="chef-bio">{html.escape(c.get("bio","") or c.get("note",""))}</p></div></div>')


def _welcome_signature(people: list[dict]) -> str:
    """Sign the owner's note: the first person whose role names them an owner."""
    owner = next((p for p in people if "owner" in p.get("role", "").lower()), None)
    if not owner:
        return ""
    role = owner.get("role", "").split("·")[0].strip()   # 'Chef-owner', not the co-owner tail
    return f'— {owner.get("name","")}, {role}'


def _address_line(loc: dict) -> str:
    """A single-line address from the location block: '279 Water Street, at Dover
    Street · South Street Seaport · New York, NY'. Missing parts drop out."""
    street = ", ".join(p for p in (loc.get("address"), loc.get("cross_street")) if p)
    return " · ".join(p for p in (street, loc.get("neighbourhood"), loc.get("city")) if p)


def render_hours(hours: list) -> str:
    """The opening-hours rows: (days label, time range) → a days/time line each;
    a 'Closed' time is dimmed."""
    rows = []
    for days, t in hours:
        closed = " hours-closed" if str(t).strip().lower() == "closed" else ""
        rows.append(f'<div class="hours-row{closed}">'
                    f'<span class="hr-days">{html.escape(str(days))}</span>'
                    f'<span class="hr-time">{html.escape(str(t))}</span></div>')
    return "".join(rows)


def render_phone(phone: str) -> str:
    """A tappable phone number: a tel: link (US +1 for a 10-digit number)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    tel = f"+1{digits}" if len(digits) == 10 else f"+{digits}"
    return f'<a href="tel:{tel}">{html.escape(phone)}</a>'


def render_restaurant_page(template: str, csv_path: Path) -> str:
    """The public one-pager. csv_path is unused (the page is pure static
    metadata) but kept in the signature to match serve_static_page's contract."""
    r = RESTAURANT
    loc = r.get("location", {})
    return (template
            .replace("<!--NAV-->",         render_nav("restaurant"))
            .replace("<!--NAME-->",        html.escape(r["name"]))
            .replace("<!--STYLE-->",       html.escape(r["style"]))
            .replace("<!--TAGLINE-->",     html.escape(r["tagline"]))
            .replace("<!--WELCOME-->",     html.escape(r["welcome"]))
            .replace("<!--WELCOME_SIG-->", html.escape(_welcome_signature(r["people"])))
            .replace("<!--HISTORY-->",     html.escape(r["history"]))
            .replace("<!--ADDRESS-->",     html.escape(_address_line(loc)))
            .replace("<!--LOCATION-->",    html.escape(loc.get("story", "")))
            .replace("<!--HOURS-->",       render_hours(r.get("hours", [])))
            .replace("<!--RESERVATION-->", html.escape(r.get("reservation", "")))
            .replace("<!--PHONE-->",       render_phone(r.get("phone", "")))
            .replace("<!--RATINGS-->",     render_ratings(r["ratings"]))
            .replace("<!--MENU-->",        render_menu(RECIPES, MENU, MENU_SECTIONS))
            .replace("<!--CHEF-->",        render_chef(r["people"]))
            .replace("<!--TEAM-->",        render_team(r["people"], TEAM_SIDES,
                                                       exclude={(_chef(r["people"]) or {}).get("name")})))


# ---- the problem page (plain-English explainer; static, no CSV) -----------

# friendly label + note per station, in display order; bottleneck flag last.
STATION_VIEW = {
    "prep":    ("Cold prep & mise", "salads, starters, all the cold work",      False),
    "grill":   ("The grill",        "every steak and burger has to pass here",  True),
    "stove":   ("The stove",        "pasta and soup share the single burner",   True),
    "fryer":   ("The fryer",        "fries and anything fried",                 False),
    "oven":    ("The oven",         "finishing and melting",                    False),
    "plating": ("The pass",         "every dish is plated here, one at a time", False),
    "waiting": ("Holding shelf",    "resting and cooling — no cook tied up",    False),
}


# which station each test night leans on hardest (for the night card chip).
STRESS_TAG = {
    "training":    "Grill",
    "training_v2": "Fryer & cold",
    "training_v3": "Grill",
    "training_v4": "Range",
    "training_v5": "Both walls",
    "training_v6": "Fryer",
    "hidden_test": "Mixed",
    "stress":      "Grill",
    "sunday_gravy": "Range",
}


def render_stations(caps: dict) -> str:
    """The line at a glance: each station as a row of capacity 'pips' (one filled
    dot per cook/pan it can run at once — fewer dots = tighter), with the two real
    choke points flagged."""
    rows = []
    for name, (label, note, bottleneck) in STATION_VIEW.items():
        if name not in caps:
            continue
        n = caps[name]
        if n >= 99:
            pips = '<span class="pip pip--inf">&#8734;</span>'
        else:
            cls = "pip pip--bn" if bottleneck else "pip"
            pips = "".join(f'<span class="{cls}"></span>' for _ in range(n))
        tag = '<span class="bottleneck">bottleneck</span>' if bottleneck else ""
        rows.append(
            f'<div class="line-row{" line-row--bn" if bottleneck else ""}">'
            f'<span class="ln-name">{html.escape(label)}{tag}</span>'
            f'<span class="ln-pips" title="{n} at once">{pips}</span>'
            f'<span class="ln-note">{html.escape(note)}</span></div>')
    return "".join(rows)


def render_nights(scenarios: list) -> str:
    """A card per service night: its name, a chip for the station it stresses,
    and the one-line sketch of what it throws at the kitchen."""
    out = []
    for sc in scenarios:
        title = html.escape(getattr(sc, "title", "") or sc.name)
        blurb = html.escape(getattr(sc, "blurb", "") or "")
        tag = STRESS_TAG.get(sc.name, "")
        chip = f'<span class="night-tag">{html.escape(tag)}</span>' if tag else ""
        out.append(f'<div class="night"><div class="night-h"><span>{title}</span>{chip}</div>'
                   f'<div class="night-b">{blurb}</div></div>')
    return "".join(out)


def failing_figure() -> str:
    """The 'failing nights' screenshot beside the goal/constraints, if present at
    static/problem/failing-nights.(webp|png). Otherwise a labelled placeholder so
    the slot is visible while building."""
    src = _asset_url("problem", "failing-nights")
    if src:
        return (f'<figure class="fail-fig">'
                f'<img src="{src}" alt="Four service nights the kitchen badly mis-scheduled, '
                f'each piling up minutes of lateness" loading="lazy">'
                f'<figcaption>Four nights gone wrong: every <span class="x">&times;</span> is a '
                f'table served late — and the lateness adds up fast.</figcaption></figure>')
    return ('<div class="fail-fig fail-fig--empty"><span>failing nights screenshot<br>'
            '<code>static/problem/failing-nights.png</code></span></div>')


def render_problem_page(template: str, csv_path: Path) -> str:
    """The plain-English 'what we're solving' page. csv_path is unused (the page
    is descriptive only) but kept to match serve_static_page's contract."""
    return (template
            .replace("<!--NAV-->",            render_nav("problem"))
            .replace("<!--NAME-->",           html.escape(RESTAURANT["name"]))
            .replace("<!--STATIONS-->",       render_stations(STATION_CAPACITY))
            .replace("<!--FAILING_FIG-->",    failing_figure()))


def render_approach_page(template: str, csv_path: Path) -> str:
    """The 'our approach' page: the autoresearch loop and what makes it tick.
    Descriptive only — csv_path is unused but kept for the render contract."""
    return (template
            .replace("<!--NAV-->",            render_nav("approach"))
            .replace("<!--NAME-->",           html.escape(RESTAURANT["name"]))
            .replace("<!--NIGHTS_TRAIN-->",   render_nights(TRAINING_BATTERY))
            .replace("<!--NIGHTS_HELDOUT-->", render_nights([HIDDEN_TEST, STRESS, SUNDAY_GRAVY])))


def render_grid_page(template: str, csv_path: Path) -> str:
    rows = load_rows(csv_path)
    n = sum(1 for r in rows if schedule_samples(load_schedule(csv_path.parent, r.get("file", ""))))
    sub = (f"{n} experiment{'' if n == 1 else 's'} across the training battery — one "
           "section per sample, each showing every experiment's schedule on it (best "
           "on that sample first). Click any to open it on the research dashboard."
           if n else "No schedules yet.")
    return (template
            .replace("<!--NAV-->", render_nav("grid"))
            .replace("<!--GRID-->", render_grid(rows, csv_path.parent))
            .replace("<!--RESEARCH_INTRO-->", research_intro())
            .replace("<!--SUB-->", html.escape(sub)))


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, csv_path: Path, target: float, **kwargs):
        self.csv_path = csv_path
        self.target = target
        super().__init__(*args, **kwargs)

    def handle(self):
        # Browsers open and drop connections constantly (SSE reconnects, tab
        # refreshes); a client vanishing mid-request is normal, not an error.
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self):
        if self.path.startswith("/events"):
            self.stream_events()
        elif self.path.startswith("/detail"):
            self.serve_detail()
        elif self.path.startswith("/static/"):
            self.serve_static_asset()
        elif self.path in ("/research-grid", "/research-grid.html"):
            self.serve_static_page(GRID_TMPL, render_grid_page)
        elif self.path in ("/research-lineage", "/research-lineage.html"):
            self.serve_static_page(LINEAGE_TMPL, render_lineage_page, with_target=True)
        elif self.path in ("/problem", "/problem.html"):
            self.serve_static_page(PROBLEM_TMPL, render_problem_page)
        elif self.path in ("/approach", "/approach.html"):
            self.serve_static_page(APPROACH_TMPL, render_approach_page)
        elif self.path in ("/research-dashboard", "/research-dashboard.html"):
            self.serve_static_page(TEMPLATE, render_page, with_target=True)   # the run log
        elif self.path in ("/", "/index.html", "/restaurant", "/restaurant.html"):
            self.serve_static_page(RESTO_TMPL, render_restaurant_page)        # restaurant = home
        elif self.path in OLD_ROUTE_REDIRECTS:
            self.send_redirect(OLD_ROUTE_REDIRECTS[self.path])                # keep old links alive
        else:
            self.send_error(404)

    def _respond(self, body: bytes, content_type: str, cache: str = "no-store"):
        """Write a 200 with the standard headers — always fresh while iterating."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, location: str):
        """301 to `location` — used to forward the old route names to the new."""
        self.send_response(301)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_static_page(self, tmpl, render, with_target=False):
        try:
            page = (render(tmpl.read_text(), self.csv_path, self.target) if with_target
                    else render(tmpl.read_text(), self.csv_path))
        except Exception as exc:  # never let one bad render kill the server
            self.send_error(500, f"render failed: {type(exc).__name__}: {exc}")
            return
        self._respond(page.encode("utf-8"), "text/html; charset=utf-8")

    def serve_detail(self):
        key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
        detail = experiment_detail(load_rows(self.csv_path), key, self.csv_path.parent)
        if detail is None:
            self.send_error(404, "no such experiment")
            return
        self._respond(json.dumps(detail).encode("utf-8"), "application/json; charset=utf-8")

    def serve_static_asset(self):
        """Serve a shared asset (CSS/JS/SVG/JSON or an image) from static/, incl.
        subdirectories like portraits/. Path-traversal safe."""
        name = urlparse(self.path).path[len("/static/"):]
        target = (STATIC_DIR / name).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root not in target.parents or not target.is_file():
            self.send_error(404)
            return
        ctype = STATIC_TYPES.get(target.suffix, "application/octet-stream")
        if target.suffix in TEXT_SUFFIXES:
            ctype += "; charset=utf-8"
        self._respond(target.read_bytes(), ctype)

    def stream_events(self):
        """Server-Sent Events: push the rendered rows whenever the CSV changes.

        Sends an initial snapshot on connect, then watches the file's mtime and
        re-sends on every change. A periodic comment keeps the connection warm
        and surfaces client disconnects (the write raises, we return)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b"retry: 2000\n\n")  # reconnect after 2s if dropped

        last_mtime = object()  # sentinel: force an initial send
        idle = 0
        try:
            while True:
                try:
                    mtime = os.path.getmtime(self.csv_path)
                except OSError:
                    mtime = None  # no CSV yet — still send the empty snapshot once
                if mtime != last_mtime:
                    last_mtime = mtime
                    rows = load_rows(self.csv_path)
                    sub, updated = meta(rows)
                    payload = json.dumps({
                        "rows": render_rows(rows),
                        "sub": sub,
                        "updated": updated,
                        "chart": chart_data(rows),
                        "target": self.target,
                    })
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    idle = 0
                else:
                    idle += 1
                    if idle % PING_EVERY == 0:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                time.sleep(POLL_S)
        except (BrokenPipeError, ConnectionResetError):
            return  # client navigated away / closed the tab

    def log_message(self, *_):
        pass  # quiet: don't spam the terminal running the discovery loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the discovery run-log dashboard.")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help=f"runs.csv to read (default: {DEFAULT_CSV})")
    parser.add_argument("--target", type=float, default=0.0,
                        help="target lateness drawn as a threshold line (default: 0 = "
                             "zero lateness, the goal)")
    args = parser.parse_args()

    handler = partial(Handler, csv_path=args.csv, target=args.target)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"dashboard → http://{args.host}:{args.port}  (watching {args.csv})")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
