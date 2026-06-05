#!/usr/bin/env python3
"""Generate Le Petit Renard's signature mark(s) — the little scheming fox.

Engraving/hedcut-style line-art on a transparent background, saved under
dashboard/static/marks/. The restaurant page shows the fox at the top of the
menu if it exists, and omits it otherwise.

Usage
-----
    export OPENAI_API_KEY=sk-...
    python -m tools.generate_mark                       # all marks, skipping existing
    python -m tools.generate_mark --only fox --force --quality high
"""

import argparse
import base64
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "marks"

MARKS = {
    "fox": {
        "size": "1024x1024",
        "prompt": (
            "A late-18th-century French copperplate stipple engraving of a fox, in the "
            "manner of an antique natural-history plate (à la Buffon's Histoire "
            "Naturelle, c.1780s): a finely observed, naturalistic red fox rendered "
            "entirely in delicate stipple dots and fine engraved hatching, elegant and "
            "anatomically real — not a cartoon, no cute exaggeration. The fox stands or "
            "sits in profile with a sly, knowing expression, glancing with quiet cunning "
            "toward a small covered serving dish of food it plainly covets. Refined "
            "antique engraving line-work, warm sepia-black ink, aged-print character, no "
            "lettering, on a fully transparent background. A single centred emblem."
        ),
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the fox signature mark via OpenAI images.")
    ap.add_argument("--only", choices=list(MARKS), help="generate just this mark")
    ap.add_argument("--force", action="store_true", help="overwrite marks that already exist")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high"],
                    help="image quality (default: high — it's a small logo)")
    ap.add_argument("--model", default="gpt-image-1", help="OpenAI image model")
    args = ap.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("The openai SDK isn't installed. Run:  pip install openai", file=sys.stderr)
        return 2

    client = OpenAI()   # reads OPENAI_API_KEY from the environment
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    keys = [args.only] if args.only else list(MARKS)
    made = skipped = failed = 0
    for key in keys:
        spec = MARKS[key]
        dest = OUT_DIR / f"{key}.png"
        if dest.exists() and not args.force:
            print(f"· skip   {key}  ({dest.name} exists)")
            skipped += 1
            continue
        print(f"… render {key} …", flush=True)
        try:
            res = client.images.generate(
                model=args.model, prompt=spec["prompt"],
                size=spec["size"], quality=args.quality, n=1, background="transparent",
            )
            dest.write_bytes(base64.b64decode(res.data[0].b64_json))
            print(f"✓ saved  {dest.name}  ({spec['size']})")
            made += 1
        except Exception as exc:
            print(f"✗ failed {key}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone — {made} made, {skipped} skipped, {failed} failed. → {OUT_DIR}")
    return 1 if failed and not made else 0


if __name__ == "__main__":
    raise SystemExit(main())
