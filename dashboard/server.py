#!/usr/bin/env python3
"""A tiny, dependency-free dashboard for discovery runs.

Serves a single Tufte-styled page (`index.html`) showing the experiment
table read from `heuristics/discovered/runs.csv`, newest first.

The page updates itself: the server watches the CSV and pushes new rows over
Server-Sent Events (the `/events` stream), so a new experiment shows up at the
top of the table — with a brief highlight — the moment the discovery loop
appends it. No reload needed. The page is fully server-rendered first, so it
still shows the current state with JavaScript disabled; the live stream is
pure enhancement. No charts — just the table.

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
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

HERE        = Path(__file__).parent
TEMPLATE    = HERE / "index.html"
GRID_TMPL   = HERE / "grid.html"
LINEAGE_TMPL = HERE / "lineage.html"
DEFAULT_CSV = HERE.parent / "heuristics" / "discovered" / "runs.csv"

POLL_S      = 1.0   # how often the SSE loop checks the CSV for changes
PING_EVERY  = 10    # send an SSE comment every Nth idle poll (detects drops)

# (csv field, column header, css class for the cell). Order = display order.
COLUMNS = [
    ("n",              "#",        "num"),
    ("run_id",         "Run",      "mono"),
    ("scenario",       "Scenario", ""),
    ("model",          "Model",    "mono"),
    ("total_lateness", "Lateness", "num"),
    ("status",         "Status",   ""),
    ("timestamp",      "Time",     "faint"),
]


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _row_key(r: dict) -> str:
    """Stable id for a row, so the page can tell which rows are new."""
    return f'{r.get("run_id", "")}|{r.get("iter", "")}'


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

    `rows` is in CSV (chronological) order. Best-so-far (lowest lateness, ties
    included) is computed in that order, then the rows are reversed for display
    so the most recent experiment sits at the top."""
    if not rows:
        return (f'<tr class="placeholder"><td colspan="{len(COLUMNS)}" class="empty">'
                "No runs yet — start one with "
                "<code>python -m discovery.discover</code>.</td></tr>")

    # mark each row that matches or beats the best lateness so far (ties
    # included), computed chronologically
    best: float | None = None
    is_best = []
    for r in rows:
        val = _to_float(r.get("total_lateness", ""))
        new_best = val is not None and (best is None or val <= best)
        if new_best:
            best = val
        is_best.append(new_best)

    out = []
    for r, best_row in reversed(list(zip(rows, is_best))):  # display newest first
        is_pivot = r.get("pivot") == "1"
        cells = []
        for field, _, cls in COLUMNS:
            raw = r.get(field, "") or ""
            if field == "n":
                num = html.escape(raw) if raw else "—"
                cell = (f'<span class="pivot-n" title="new approach after a plateau">{num}</span>'
                        if is_pivot else num)
            elif field == "status":
                cell = _status_cell(raw)
            elif field == "total_lateness":
                txt = raw if raw else "—"
                strong = " best" if best_row else ""
                cell = f'<span class="num{strong}">{html.escape(txt)}</span>'
            else:
                cell = html.escape(raw) if raw else '<span class="faint">—</span>'
            klass = f' class="{cls}"' if cls else ""
            cells.append(f"<td{klass}>{cell}</td>")
        tr_cls = " best-row" if best_row else ""
        key = html.escape(_row_key(r))
        out.append(f'<tr class="row{tr_cls}" data-key="{key}">{"".join(cells)}</tr>')
    return "".join(out)


def chart_data(rows: list[dict]) -> list[dict]:
    """One point per iteration, in chronological order, for the live chart.

    Each point is {y, kind, key}: `y` is total_lateness (None for a failed
    run), `kind` is 'kept' (matched or beat the best lateness so far, ties
    included), 'discarded' (a valid run worse than the best so far), or
    'failed' (errored / produced no score). `key` matches the table row's
    data-key, so the chart and table can cross-highlight."""
    best: float | None = None
    points = []
    for r in rows:
        key = _row_key(r)
        pv = r.get("pivot") == "1"
        val = _to_float(r.get("total_lateness", ""))
        if r.get("status", "").startswith("failed") or val is None:
            points.append({"y": None, "kind": "failed", "key": key, "pivot": pv})
            continue
        if best is None or val <= best:
            best, kind = val, "kept"
        else:
            kind = "discarded"
        points.append({"y": val, "kind": kind, "key": key, "pivot": pv})
    return points


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

    rows = []  # (label, [entry])
    for st in sorted({e["station"] for e in entries}):
        sub = [e for e in entries if e["station"] == st]
        slot_of, n = _pack_slots(sub)
        for slot in range(n):
            label = f"{st} #{slot}" if n > 1 else st
            rows.append((label, [sub[j] for j in range(len(sub)) if slot_of[j] == slot]))

    oids = sorted({_order_of(e["step"]) for e in entries})
    color = {oid: ORDER_COLORS[i % len(ORDER_COLORS)] for i, oid in enumerate(oids)}
    pale  = {oid: PALE_COLORS[i % len(PALE_COLORS)] for i, oid in enumerate(oids)}

    W, L, R, T, B, rowH = 720, 84, 14, 10, 26, 22
    x0, x1 = L, W - R
    plot_bottom = T + len(rows) * rowH
    H = plot_bottom + B
    xf = lambda t: x0 + (t / horizon) * (x1 - x0)

    g = []
    for t in _gantt_ticks(horizon):
        x = xf(t)
        g.append(f'<line class="gg" x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{plot_bottom}"/>')
        g.append(f'<text class="gx" x="{x:.1f}" y="{H-8}" text-anchor="middle">{_fmtnum(t)}</text>')
    for o in orders:
        if o.get("id") in color:
            c = color[o["id"]]
            for key, dash, op in (("arrival", "1 3", "0.5"), ("due", "4 3", "0.75")):
                x = xf(o[key])
                g.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{plot_bottom}" '
                         f'stroke="{c}" stroke-width="1" stroke-dasharray="{dash}" opacity="{op}"/>')
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


def first_sample(sched: dict | None) -> dict | None:
    """The first sample's schedule — what the detail-view Gantt draws."""
    s = schedule_samples(sched)
    return s[0] if s else None


def gantt_thumb(sched: dict) -> str:
    """A label-free thumbnail Gantt for the small-multiples grid: just the
    coloured bars on their station rows — no numbers, axes, labels, or tips."""
    entries = sched.get("entries") or []
    if not entries:
        return ""
    horizon = sched.get("horizon") or max((e["end"] for e in entries), default=1) or 1
    rows = []
    for st in sorted({e["station"] for e in entries}):
        sub = [e for e in entries if e["station"] == st]
        slot_of, n = _pack_slots(sub)
        for slot in range(n):
            rows.append([sub[j] for j in range(len(sub)) if slot_of[j] == slot])

    oids = sorted({_order_of(e["step"]) for e in entries})
    pale = {oid: PALE_COLORS[i % len(PALE_COLORS)] for i, oid in enumerate(oids)}
    color = {oid: ORDER_COLORS[i % len(ORDER_COLORS)] for i, oid in enumerate(oids)}

    W, pad, rowH, barH = 240, 3, 8, 6
    x0, x1 = pad, W - pad
    H = pad * 2 + len(rows) * rowH
    xf = lambda t: x0 + (t / horizon) * (x1 - x0)

    g = []
    for y, items in enumerate(rows):
        cy = pad + y * rowH
        for e in items:
            bx = xf(e["start"])
            bw = max(0.8, xf(e["end"]) - xf(e["start"]))
            oid = _order_of(e["step"])
            g.append(f'<rect x="{bx:.1f}" y="{cy+1:.1f}" width="{bw:.1f}" height="{barH}" rx="1" '
                     f'fill="{pale[oid]}" stroke="{color[oid]}" stroke-width="0.5"/>')
    return (f'<svg class="thumb" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'role="img" aria-label="schedule thumbnail">{"".join(g)}</svg>')


def _lat_key(v) -> float:
    f = _to_float(str(v))
    return f if f is not None else float("inf")


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
        for r, s in items:
            key = _row_key(r)
            title = r.get("title", "") or "Untitled"
            lat = s.get("lateness", "")
            cells.append(
                f'<a class="cell" href="/#exp={quote(key)}" title="{html.escape(title)}">'
                f'<div class="cell-thumb">{gantt_thumb(s)}</div>'
                f'<div class="cell-cap"><span class="cell-n">#{html.escape(r.get("n",""))}</span>'
                f'<span class="cell-title">{html.escape(title)}</span>'
                f'<span class="cell-lat">{html.escape(str(lat))}</span></div></a>')
        sections.append(
            f'<section class="sample"><h2 class="sample-h">{html.escape(name)}'
            f'<span class="cnt">{len(items)} experiments</span></h2>'
            f'<div class="grid">{"".join(cells)}</div></section>')
    return "".join(sections)


def experiment_detail(rows: list[dict], key: str, csv_dir: Path) -> dict | None:
    """Everything the row-expand panel needs for one experiment: symbol, title,
    summary, explanation, highlighted code (read from its .py), and parent
    experiments resolved to their symbols. None if the key isn't found."""
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
    sched0 = first_sample(load_schedule(csv_dir, fname))   # the detail Gantt = first sample

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
        "gantt_svg": gantt_svg(sched0) if sched0 else "",
        "thumb_svg": gantt_thumb(sched0) if sched0 else "",
        "code_html": highlight_py(code) if code else "",
        "parents": parents,
        "lateness": r.get("total_lateness", "") or "",
        "time": r.get("timestamp", "") or "",
        "status": r.get("status", "") or "",
    }


def render_page(template: str, csv_path: Path, target: float) -> str:
    rows = load_rows(csv_path)
    sub, updated = meta(rows)
    chart = json.dumps({"points": chart_data(rows), "target": target})
    return (template
            .replace("<!--TABLE-->", render_table(rows))
            .replace("<!--SUB-->", html.escape(sub))
            .replace("<!--UPDATED-->", html.escape(updated))
            .replace("<!--CHARTDATA-->", chart))


def lineage_data(rows: list[dict]) -> dict:
    """Nodes for the lineage graph, in discovery (experiment-number) order. Each
    node carries its key, n, title, lateness, kind (kept/discarded/failed, like
    the chart), and the parent keys it was derived from."""
    def n_of(r):
        s = str(r.get("n", ""))
        return int(s) if s.isdigit() else 0
    best = None
    nodes = []
    for r in sorted(rows, key=n_of):
        val = _to_float(r.get("total_lateness", ""))
        if r.get("status", "").startswith("failed") or val is None:
            kind = "failed"
        elif best is None or val <= best:
            best, kind = val, "kept"
        else:
            kind = "discarded"
        title = r.get("title", "") or "Untitled"
        nodes.append({
            "key": _row_key(r), "n": r.get("n", ""),
            "title": title, "symbol": slug(title),
            "summary": r.get("summary", "") or "",
            "lateness": val, "kind": kind,
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
           "ancestry back to the root; click to open it on the dashboard." if n else "No experiments yet.")
    return (template
            .replace("<!--LINEAGEDATA-->", json.dumps(data))
            .replace("<!--SUB-->", html.escape(sub)))


def render_grid_page(template: str, csv_path: Path) -> str:
    rows = load_rows(csv_path)
    n = sum(1 for r in rows if schedule_samples(load_schedule(csv_path.parent, r.get("file", ""))))
    sub = (f"{n} experiment{'' if n == 1 else 's'} across the training battery — one "
           "section per sample, each showing every experiment's schedule on it (best "
           "on that sample first). Click any to open it on the dashboard."
           if n else "No schedules yet.")
    return (template
            .replace("<!--GRID-->", render_grid(rows, csv_path.parent))
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
        elif self.path in ("/grid", "/grid.html"):
            self.serve_static_page(GRID_TMPL, render_grid_page)
        elif self.path in ("/lineage", "/lineage.html"):
            self.serve_static_page(LINEAGE_TMPL, render_lineage_page, with_target=True)
        elif self.path in ("/", "/index.html"):
            self.serve_page()
        else:
            self.send_error(404)

    def serve_static_page(self, tmpl, render, with_target=False):
        try:
            page = (render(tmpl.read_text(), self.csv_path, self.target) if with_target
                    else render(tmpl.read_text(), self.csv_path))
        except Exception as exc:
            self.send_error(500, f"render failed: {type(exc).__name__}: {exc}")
            return
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_detail(self):
        key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
        detail = experiment_detail(load_rows(self.csv_path), key, self.csv_path.parent)
        if detail is None:
            self.send_error(404, "no such experiment")
            return
        body = json.dumps(detail).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_page(self):
        try:
            page = render_page(TEMPLATE.read_text(), self.csv_path, self.target)
        except Exception as exc:  # never let one bad render kill the server
            self.send_error(500, f"render failed: {type(exc).__name__}: {exc}")
            return
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # always serve fresh while iterating
        self.end_headers()
        self.wfile.write(body)

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
