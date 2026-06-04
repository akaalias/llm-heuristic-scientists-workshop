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
import os
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE        = Path(__file__).parent
TEMPLATE    = HERE / "index.html"
DEFAULT_CSV = HERE.parent / "heuristics" / "discovered" / "runs.csv"

POLL_S      = 1.0   # how often the SSE loop checks the CSV for changes
PING_EVERY  = 10    # send an SSE comment every Nth idle poll (detects drops)

# (csv field, column header, css class for the cell). Order = display order.
COLUMNS = [
    ("iter",           "#",        "num"),
    ("run_id",         "Run",      "mono"),
    ("scenario",       "Scenario", ""),
    ("model",          "Model",    "mono"),
    ("total_lateness", "Lateness", "num"),
    ("status",         "Status",   ""),
    ("timestamp",      "Time",     "faint"),
]


def _status_cell(raw: str) -> str:
    """Render a status value. 'success' → ink small-caps; 'failed:Err' → rust
    small-caps with the error class spelled out."""
    if raw.startswith("failed"):
        err = raw.split(":", 1)[1] if ":" in raw else ""
        label = f"failed · {err}" if err else "failed"
        return f'<span class="status st-bad">{html.escape(label)}</span>'
    return f'<span class="status st-ok">{html.escape(raw or "—")}</span>'


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _row_key(r: dict) -> str:
    """Stable id for a row, so the page can tell which rows are new."""
    return f'{r.get("run_id", "")}|{r.get("iter", "")}'


def render_rows(rows: list[dict]) -> str:
    """Render the <tr>s for the table body, newest first.

    `rows` is in CSV (chronological) order. Running-best (lowest lateness so
    far) is computed in that order, then the rows are reversed for display so
    the most recent experiment sits at the top."""
    if not rows:
        return (f'<tr class="placeholder"><td colspan="{len(COLUMNS)}" class="empty">'
                "No runs yet — start one with "
                "<code>python -m discovery.discover</code>.</td></tr>")

    # mark each row that set a new running-best (lowest) lateness — chronologically
    best: float | None = None
    is_best = []
    for r in rows:
        val = _to_float(r.get("total_lateness", ""))
        new_best = val is not None and (best is None or val < best)
        if new_best:
            best = val
        is_best.append(new_best)

    out = []
    for r, best_row in reversed(list(zip(rows, is_best))):  # newest first
        cells = []
        for field, _, cls in COLUMNS:
            raw = r.get(field, "") or ""
            if field == "status":
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

    Each point is {y, kind}: `y` is total_lateness (None for a failed run),
    `kind` is 'kept' (set a new running-best), 'discarded' (ran but didn't
    improve), or 'failed' (errored / produced no score)."""
    best: float | None = None
    points = []
    for r in rows:
        val = _to_float(r.get("total_lateness", ""))
        if r.get("status", "").startswith("failed") or val is None:
            points.append({"y": None, "kind": "failed"})
            continue
        if best is None or val < best:
            best, kind = val, "kept"
        else:
            kind = "discarded"
        points.append({"y": val, "kind": kind})
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


def render_page(template: str, csv_path: Path, target: float) -> str:
    rows = load_rows(csv_path)
    sub, updated = meta(rows)
    chart = json.dumps({"points": chart_data(rows), "target": target})
    return (template
            .replace("<!--TABLE-->", render_table(rows))
            .replace("<!--SUB-->", html.escape(sub))
            .replace("<!--UPDATED-->", html.escape(updated))
            .replace("<!--CHARTDATA-->", chart))


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, csv_path: Path, target: float, **kwargs):
        self.csv_path = csv_path
        self.target = target
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path.startswith("/events"):
            self.stream_events()
        elif self.path in ("/", "/index.html"):
            self.serve_page()
        else:
            self.send_error(404)

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
