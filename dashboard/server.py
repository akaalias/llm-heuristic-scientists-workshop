#!/usr/bin/env python3
"""A tiny, dependency-free dashboard for discovery runs.

Serves a single Tufte-styled page (`index.html`) showing the experiment
table read live from `heuristics/discovered/runs.csv`. The CSV is re-read on
every request, so leaving this running next to a discovery loop gives you a
just-refresh-the-browser view of progress. No JavaScript, no charts — just
the table.

Usage
-----
    python -m dashboard.server                 # serve on http://localhost:8000
    python -m dashboard.server --port 9000
    python -m dashboard.server --csv path/to/runs.csv
"""

import argparse
import csv
import html
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE        = Path(__file__).parent
TEMPLATE    = HERE / "index.html"
DEFAULT_CSV = HERE.parent / "heuristics" / "discovered" / "runs.csv"

# (csv field, column header, css class for the cell). Order = display order.
# Any extra columns present in the CSV but not listed here are appended as-is.
COLUMNS = [
    ("iter",           "#",        "num"),
    ("run_id",         "Run",      "mono"),
    ("scenario",       "Scenario", ""),
    ("model",          "Model",    "mono"),
    ("total_lateness", "Lateness", "num"),
    ("status",         "Status",   ""),
    ("timestamp",      "Time",     "faint"),
    ("file",           "File",     "mono faint"),
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


def render_table(rows: list[dict]) -> str:
    """Build the <table> (or an empty-state note) from the CSV rows."""
    if not rows:
        return ('<p class="empty">No runs yet — start one with '
                "<code>python -m discovery.discover</code>.</p>")

    # render exactly the base-schema columns; ignore any others a CSV may carry
    headers = COLUMNS

    # mark each row that set a new running-best (lowest) lateness
    best: float | None = None
    is_best = []
    for r in rows:
        val = _to_float(r.get("total_lateness", ""))
        new_best = val is not None and (best is None or val < best)
        if new_best:
            best = val
        is_best.append(new_best)

    head = "".join(f"<th>{html.escape(h)}</th>" for _, h, _ in headers)

    body_rows = []
    for r, best_row in zip(rows, is_best):
        cells = []
        for field, _, cls in headers:
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
        tr_cls = ' class="best-row"' if best_row else ""
        body_rows.append(f"<tr{tr_cls}>{''.join(cells)}</tr>")

    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table>")


def load_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="") as f:
        return list(csv.DictReader(f))


def render_page(template: str, csv_path: Path) -> str:
    rows = load_rows(csv_path)
    runs = {r.get("run_id", "") for r in rows}
    latest = max((r.get("timestamp", "") for r in rows), default="")
    sub = (f"{len(rows)} iterations across {len(runs)} run(s)."
           if rows else "Waiting for the first discovery run.")
    updated = f"updated {html.escape(latest)}" if latest else ""
    return (template
            .replace("<!--TABLE-->", render_table(rows))
            .replace("<!--SUB-->", html.escape(sub))
            .replace("<!--UPDATED-->", updated))


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, csv_path: Path, **kwargs):
        self.csv_path = csv_path
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            page = render_page(TEMPLATE.read_text(), self.csv_path)
        except Exception as exc:  # never let one bad render kill the server
            self.send_error(500, f"render failed: {type(exc).__name__}: {exc}")
            return
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # quiet: don't spam the terminal running the discovery loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the discovery run-log dashboard.")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help=f"runs.csv to read (default: {DEFAULT_CSV})")
    args = parser.parse_args()

    handler = partial(Handler, csv_path=args.csv)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"dashboard → http://{args.host}:{args.port}  (reading {args.csv})")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
