#!/usr/bin/env python3
"""Export the dashboard as a static site for GitHub Pages.

Renders the live, server-rendered pages to plain HTML with the CURRENT run data
baked in, rewrites the server-absolute URLs to relative ones (so the site works
under a project Pages subpath like https://<user>.github.io/<repo>/), and copies
the shared static assets. Output goes to docs/ — point GitHub Pages at
"Deploy from a branch → /docs".

This sits ON TOP of the normal workflow: keep running `python -m dashboard.server`
to build and design locally; run this when you want to publish a snapshot.

What carries over to the static mirror: the run table, the chart (drawn from
data baked into the page), the schedule grid, the lineage diagram, and the whole
restaurant page. What does NOT (they need the live server): the SSE live updates
and the row-expand experiment detail — those keep working locally, just not on
the static copy.

Usage:
    python -m tools.export_site
    python -m tools.export_site --csv path/to/runs.csv --out docs --target 0
"""

import argparse
import shutil
from pathlib import Path

from dashboard import server

ROOT        = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs"

# (output filename, template path, render fn, passes a `target` arg)
PAGES = [
    ("index.html",      server.TEMPLATE,     server.render_page,            True),
    ("grid.html",       server.GRID_TMPL,    server.render_grid_page,       False),
    ("lineage.html",    server.LINEAGE_TMPL, server.render_lineage_page,    True),
    ("restaurant.html", server.RESTO_TMPL,   server.render_restaurant_page, False),
]


def rewrite_html(s: str) -> str:
    """Server-absolute URLs → relative, so pages work under /<repo>/ on Pages
    and link to each other as static files."""
    return (s
            .replace('="/static/',        '="static/')               # css/js/img refs
            .replace('href="/#exp=',      'href="index.html#exp=')    # grid deep-links
            .replace('href="/grid"',      'href="grid.html"')
            .replace('href="/lineage"',   'href="lineage.html"')
            .replace('href="/restaurant"', 'href="restaurant.html"')
            .replace('href="/"',          'href="index.html"'))


def rewrite_js(s: str) -> str:
    """Patch the one JS deep-link that points at the dashboard root (lineage.js
    builds `"/#exp=" + key`) so it targets the static index instead."""
    return s.replace('"/#exp="', '"index.html#exp="')


def main() -> None:
    ap = argparse.ArgumentParser(description="Export the dashboard to a static site for GitHub Pages.")
    ap.add_argument("--csv", type=Path, default=server.DEFAULT_CSV,
                    help=f"runs.csv to render (default: {server.DEFAULT_CSV})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"output directory (default: {DEFAULT_OUT})")
    ap.add_argument("--target", type=float, default=0.0,
                    help="target lateness for the chart threshold line (default: 0)")
    args = ap.parse_args()

    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1) shared assets (css / js / portraits / scenes / marks)
    shutil.copytree(server.STATIC_DIR, out / "static")
    for js in ("dashboard.js", "lineage.js"):
        p = out / "static" / js
        if p.exists():
            p.write_text(rewrite_js(p.read_text()))

    # 2) render each page with the current data, then relativise its URLs
    for name, tmpl, render, needs_target in PAGES:
        html = (render(tmpl.read_text(), args.csv, args.target) if needs_target
                else render(tmpl.read_text(), args.csv))
        (out / name).write_text(rewrite_html(html))
        print(f"  ✓ {name}")

    # 3) tell Pages to serve the files as-is (no Jekyll processing)
    (out / ".nojekyll").write_text("")

    print(f"\nStatic site → {out}")
    print("Enable once on GitHub: Settings → Pages → Deploy from a branch → "
          "branch, folder /docs.")


if __name__ == "__main__":
    main()
