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
data baked into the page), the schedule grid, the lineage diagram, the whole
restaurant page, and the row-expand experiment detail (baked into details.json,
since Pages has no live `/detail` endpoint). What does NOT: the SSE live updates
— those keep working locally, just not on the static copy.

Usage:
    python -m tools.export_site
    python -m tools.export_site --csv path/to/runs.csv --out docs --target 0
"""

import argparse
import json
import shutil
from pathlib import Path

from dashboard import server

ROOT        = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs"

# (output filename, template path, render fn, passes a `target` arg)
PAGES = [
    ("index.html",     server.RESTO_TMPL,   server.render_restaurant_page, False),  # landing = restaurant
    ("problem.html",   server.PROBLEM_TMPL, server.render_problem_page,    False),
    ("dashboard.html", server.TEMPLATE,     server.render_page,            True),   # the run log
    ("grid.html",      server.GRID_TMPL,    server.render_grid_page,       False),
    ("lineage.html",   server.LINEAGE_TMPL, server.render_lineage_page,    True),
]


def rewrite_html(s: str) -> str:
    """Server-absolute URLs → relative, so pages work under /<repo>/ on Pages
    and link to each other as static files."""
    return (s
            .replace('="/static/',            '="static/')                  # css/js/img refs
            .replace('href="/dashboard#exp=', 'href="dashboard.html#exp=')  # grid deep-links
            .replace('href="/dashboard"',     'href="dashboard.html"')
            .replace('href="/grid"',          'href="grid.html"')
            .replace('href="/lineage"',       'href="lineage.html"')
            .replace('href="/problem"',       'href="problem.html"')
            .replace('href="/restaurant"',    'href="index.html"')          # restaurant = index
            .replace('href="/"',              'href="index.html"'))


def rewrite_js(s: str) -> str:
    """Patch the JS deep-link into the dashboard (lineage.js builds
    `"/dashboard#exp=" + key`) so it targets the static dashboard page, and point
    the row-expand at the baked details.json (there is no live server on Pages)."""
    return (s
            .replace('"/dashboard#exp="', '"dashboard.html#exp="')
            .replace('const DETAILS_URL = null;', 'const DETAILS_URL = "details.json";'))


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

    # keep Pages lean: drop full-res originals that have a web/ variant — the
    # pages reference the small web variant, so docs only needs to ship that one.
    for category in ("portraits", "scenes", "marks", "problem"):
        webdir = out / "static" / category / "web"
        if not webdir.is_dir():
            continue
        web_stems = {p.stem for p in webdir.iterdir() if p.is_file()}
        for orig in (out / "static" / category).glob("*.png"):
            if orig.stem in web_stems:
                orig.unlink()

    # 2) render each page with the current data, then relativise its URLs
    for name, tmpl, render, needs_target in PAGES:
        html = (render(tmpl.read_text(), args.csv, args.target) if needs_target
                else render(tmpl.read_text(), args.csv))
        (out / name).write_text(rewrite_html(html))
        print(f"  ✓ {name}")

    # 3) bake the row-expand experiment detail into a static JSON map, so the
    #    dashboard's expand works on Pages without the live `/detail` endpoint
    rows = server.load_rows(args.csv)
    details = {}
    for r in rows:
        key = server._row_key(r)
        d = server.experiment_detail(rows, key, args.csv.parent)
        if d is not None:
            details[key] = d
    (out / "details.json").write_text(json.dumps(details))
    print(f"  ✓ details.json ({len(details)} experiments)")

    # 4) tell Pages to serve the files as-is (no Jekyll processing)
    (out / ".nojekyll").write_text("")

    print(f"\nStatic site → {out}")
    print("Enable once on GitHub: Settings → Pages → Deploy from a branch → "
          "branch, folder /docs.")


if __name__ == "__main__":
    main()
