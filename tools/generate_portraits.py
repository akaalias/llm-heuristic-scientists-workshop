#!/usr/bin/env python3
"""Generate WSJ-"hedcut"-style portraits for the Le Petit Renard team.

Each member of RESTAURANT['people'] gets a black-and-white stipple-engraving
portrait (the Wall Street Journal hedcut look), saved as a PNG under
dashboard/static/portraits/<slug>.png. The restaurant page picks them up
automatically if they exist, and shows initials otherwise — so running this is
purely an enhancement.

The team are fictional characters; these are invented, stylised likenesses.

Usage
-----
    export OPENAI_API_KEY=sk-...
    python -m tools.generate_portraits                 # all, skipping any that exist
    python -m tools.generate_portraits --only "Élise Marchand"
    python -m tools.generate_portraits --force --quality high
"""

import argparse
import base64
import re
import sys
import unicodedata
from pathlib import Path

from problem_definition.model import RESTAURANT

OUT_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "portraits"

# Per-person art direction so the faces read as distinct people. Keyed by name;
# anyone missing falls back to a generic description built from their role.
APPEARANCE = {
    "Élise Marchand":   "a woman in her late 40s, dark hair pulled back, chef's whites, composed and exacting",
    "Tomas Vidal":      "a man in his 30s, short dark beard, close-cropped hair, calm and steady",
    "Priya Nair":       "a woman in her 30s, dark hair tied back, intent and focused",
    "Marco Renzi":      "a man in his 50s, greying hair, warm laugh lines, easy-going",
    "Jo Abara":         "a person in their 20s, short cropped hair, bright and eager",
    "Camille Fournier": "a woman in her 40s, hair swept up, elegant and welcoming",
    "Luca Bianchi":     "a man in his 30s, slim glasses, neat dark hair, refined",
    "Sofia Herrera":    "a woman in her 30s, hair in a low bun, poised and attentive",
    "Noah Klein":       "a man in his late 20s, light stubble, tousled hair, friendly",
}

HEDCUT = (
    'Black-and-white portrait illustration in the classic Wall Street Journal '
    '"hedcut" style: a head-and-shoulders likeness rendered entirely in fine ink '
    'stippling and hatching — tiny dots and short pen strokes — with no solid '
    'black fills and no grey wash, on a plain white background. Refined, engraved '
    'newspaper look, high contrast. Subject: {desc}. Centred, facing slightly '
    'off-camera, with a warm, genuine smile — friendly, approachable and personable.'
)


def slug(name: str) -> str:
    """'Élise Marchand' -> 'elise_marchand' (accent-folded, filename-safe)."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")


def build_prompt(person: dict) -> str:
    desc = APPEARANCE.get(person.get("name", ""))
    if not desc:
        desc = f'a {person.get("role", "restaurant worker")}, professional and approachable'
    return HEDCUT.format(desc=f'{desc}; works as {person.get("role","")} at a New York grill bistro')


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate hedcut team portraits via OpenAI images.")
    ap.add_argument("--only", help="generate just this person (exact name match)")
    ap.add_argument("--force", action="store_true", help="overwrite portraits that already exist")
    ap.add_argument("--quality", default="medium", choices=["low", "medium", "high"],
                    help="image quality (cost grows with quality; default: medium)")
    ap.add_argument("--size", default="1024x1024",
                    choices=["1024x1024", "1024x1536", "1536x1024"], help="image size")
    ap.add_argument("--model", default="gpt-image-1", help="OpenAI image model")
    args = ap.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("The openai SDK isn't installed. Run:  pip install openai", file=sys.stderr)
        return 2

    client = OpenAI()   # reads OPENAI_API_KEY from the environment
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    people = RESTAURANT["people"]
    if args.only:
        people = [p for p in people if p.get("name") == args.only]
        if not people:
            print(f"No team member named {args.only!r}.", file=sys.stderr)
            return 1

    made = skipped = failed = 0
    for p in people:
        name = p.get("name", "")
        dest = OUT_DIR / f"{slug(name)}.png"
        if dest.exists() and not args.force:
            print(f"· skip   {name}  ({dest.name} exists)")
            skipped += 1
            continue
        print(f"… render {name} …", flush=True)
        try:
            res = client.images.generate(
                model=args.model, prompt=build_prompt(p),
                size=args.size, quality=args.quality, n=1, background="opaque",
            )
            dest.write_bytes(base64.b64decode(res.data[0].b64_json))
            print(f"✓ saved  {dest.relative_to(OUT_DIR.parent.parent.parent)}")
            made += 1
        except Exception as exc:   # one bad portrait shouldn't abort the batch
            print(f"✗ failed {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone — {made} made, {skipped} skipped, {failed} failed. → {OUT_DIR}")
    return 1 if failed and not made else 0


if __name__ == "__main__":
    raise SystemExit(main())
