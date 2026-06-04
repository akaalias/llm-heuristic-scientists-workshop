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
import re
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE        = Path(__file__).parent
TEMPLATE    = HERE / "index.html"
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

    Each point is {y, kind, key}: `y` is total_lateness (None for a failed
    run), `kind` is 'kept' (matched or beat the best lateness so far, ties
    included), 'discarded' (a valid run worse than the best so far), or
    'failed' (errored / produced no score). `key` matches the table row's
    data-key, so the chart and table can cross-highlight."""
    best: float | None = None
    points = []
    for r in rows:
        key = _row_key(r)
        val = _to_float(r.get("total_lateness", ""))
        if r.get("status", "").startswith("failed") or val is None:
            points.append({"y": None, "kind": "failed", "key": key})
            continue
        if best is None or val <= best:
            best, kind = val, "kept"
        else:
            kind = "discarded"
        points.append({"y": val, "kind": kind, "key": key})
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
        "explanation": r.get("explanation", "") or "",
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
        elif self.path in ("/", "/index.html"):
            self.serve_page()
        else:
            self.send_error(404)

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
