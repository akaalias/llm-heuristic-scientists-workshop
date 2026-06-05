#!/usr/bin/env python3
"""Generate engraving-style scene illustrations of Le Petit Renard.

Two images, in the same pen-and-ink / hedcut idiom as the team portraits, saved
under dashboard/static/scenes/:

    exterior.png  — the old Bridge Cafe corner-house under the Brooklyn Bridge
    interior.png  — the dining room and open grill at the pass

The restaurant page shows each one if it exists and omits it otherwise, so this
is purely an enhancement. The scenes are invented, stylised illustrations.

Usage
-----
    export OPENAI_API_KEY=sk-...
    python -m tools.generate_scenes                    # both, skipping any that exist
    python -m tools.generate_scenes --only interior --force --quality high
"""

import argparse
import base64
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "scenes"

ENGRAVING = (
    "Black-and-white pen-and-ink engraving illustration in the Wall Street Journal "
    '"hedcut" idiom: built entirely from fine ink stippling and hatching — dots and '
    "short strokes — with no grey wash and no solid black fills, on a plain white "
    "background. Detailed, characterful, editorial newspaper-engraving look. {desc}"
)

SCENES = {
    "exterior": {
        "size": "1024x1024",
        "desc": (
            "Scene: a historic two-storey wood-clapboard corner tavern in Lower "
            "Manhattan, at the corner of Water Street and Dover Street, the stone "
            "tower and suspension cables of the Brooklyn Bridge rising just behind "
            "it. A nineteenth-century streetscape — cobblestones, a cast-iron gas "
            "lamp, a few passers-by. A small hanging signboard over the door reads "
            "“LE PETIT RENARD”. Warm and inviting."
        ),
    },
    "interior": {
        "size": "1536x1024",
        "desc": (
            "Scene: the cosy interior of a historic Lower Manhattan corner tavern (the "
            "old Bridge Cafe). Warm ochre-yellow painted plaster walls — smooth plaster, "
            "NOT exposed brick — beneath a dark pressed-tin ceiling, with a couple of "
            "slender cast-iron support columns standing in the room. Along one side, a "
            "small wooden bar lined with bottles and a few stools. Closely-set tables "
            "dressed in white and deep-burgundy cloths with dark bentwood chairs, framed "
            "pictures hung on the walls, warm glowing wall sconces and hanging lamps, and "
            "a dark wood-plank floor. At the back, tall windows with a handwritten "
            "chalkboard menu and potted plants on the sills. Lively, welcoming, intimate "
            "nineteenth-century New York bistro."
        ),
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate engraving scene images via OpenAI images.")
    ap.add_argument("--only", choices=list(SCENES), help="generate just this scene")
    ap.add_argument("--force", action="store_true", help="overwrite scenes that already exist")
    ap.add_argument("--quality", default="medium", choices=["low", "medium", "high"],
                    help="image quality (cost grows with quality; default: medium)")
    ap.add_argument("--model", default="gpt-image-1", help="OpenAI image model")
    args = ap.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("The openai SDK isn't installed. Run:  pip install openai", file=sys.stderr)
        return 2

    client = OpenAI()   # reads OPENAI_API_KEY from the environment
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    keys = [args.only] if args.only else list(SCENES)
    made = skipped = failed = 0
    for key in keys:
        spec = SCENES[key]
        dest = OUT_DIR / f"{key}.png"
        if dest.exists() and not args.force:
            print(f"· skip   {key}  ({dest.name} exists)")
            skipped += 1
            continue
        print(f"… render {key} …", flush=True)
        try:
            res = client.images.generate(
                model=args.model, prompt=ENGRAVING.format(desc=spec["desc"]),
                size=spec["size"], quality=args.quality, n=1, background="opaque",
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
