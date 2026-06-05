#!/usr/bin/env python3
"""Make web-sized variants of the generated images.

The portraits/scenes are 1024px+ source PNGs (~2 MB each) but the page shows
them at ~50–130px. This writes small WebP variants next to the originals, under
static/<category>/web/<name>.webp, which the page prefers (it falls back to the
full-res PNG if a variant is missing). The originals stay put as the source of
truth — re-run this after (re)generating any image.

Portraits and scenes are stored greyscale (the page already renders them
monochrome via `filter:saturate(0)`), which shrinks them further; marks keep
their alpha. A typical portrait drops from ~1.8 MB to ~30–60 KB.

Usage:
    python -m tools.optimize_images
    python -m tools.optimize_images --force --quality 80
"""

import argparse
import sys
from pathlib import Path

from PIL import Image

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"

# category → (longest-edge px for the web variant, output mode)
CONFIG = {
    "portraits": (360, "L"),     # shown ≤130px; greyscale (page is monochrome)
    "scenes":    (900, "L"),     # shown up to a column width; greyscale
    "marks":     (320, "RGBA"),  # keep transparency
    "problem":   (1600, "RGB"),  # dense coloured screenshots — keep them legible in-column
}


def optimize(src: Path, out: Path, max_edge: int, mode: str, quality: int) -> tuple[int, int]:
    """Resize+convert one image; return (src_bytes, out_bytes)."""
    with Image.open(src) as im:
        im = im.convert(mode)
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, "WEBP", quality=quality, method=6)
    return src.stat().st_size, out.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate web-sized WebP image variants.")
    ap.add_argument("--force", action="store_true", help="rebuild variants that already exist")
    ap.add_argument("--quality", type=int, default=82, help="WebP quality 0–100 (default: 82)")
    args = ap.parse_args()

    made = skipped = 0
    saved_from = saved_to = 0
    for category, (max_edge, mode) in CONFIG.items():
        cat_dir = STATIC / category
        if not cat_dir.is_dir():
            continue
        for src in sorted(cat_dir.glob("*.png")):
            out = cat_dir / "web" / f"{src.stem}.webp"
            if out.exists() and not args.force:
                skipped += 1
                continue
            try:
                a, b = optimize(src, out, max_edge, mode, args.quality)
                saved_from += a; saved_to += b
                print(f"  ✓ {category}/{src.name:28} {a//1024:>5} KB → {b//1024:>4} KB")
                made += 1
            except Exception as exc:
                print(f"  ✗ {category}/{src.name}: {type(exc).__name__}: {exc}", file=sys.stderr)

    if made:
        print(f"\n{made} variant(s): {saved_from//1024//1024} MB → {saved_to//1024} KB"
              f"  ({skipped} already current). → static/*/web/")
    else:
        print(f"Nothing to do ({skipped} already current). Use --force to rebuild.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
