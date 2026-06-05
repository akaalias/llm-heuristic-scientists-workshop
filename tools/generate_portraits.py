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
import re
import sys
import unicodedata

from problem_definition.model import RESTAURANT
from tools.image_gen import Job, add_common_args, render_jobs
from tools.paths import STATIC_DIR

OUT_DIR = STATIC_DIR / "portraits"

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
    ap.add_argument("--size", default="1024x1024",
                    choices=["1024x1024", "1024x1536", "1536x1024"], help="image size")
    add_common_args(ap)
    args = ap.parse_args()

    people = RESTAURANT["people"]
    if args.only:
        people = [p for p in people if p.get("name") == args.only]
        if not people:
            print(f"No team member named {args.only!r}.", file=sys.stderr)
            return 1

    jobs = [
        Job(label=p.get("name", ""), dest=OUT_DIR / f'{slug(p.get("name", ""))}.png',
            prompt=build_prompt(p), size=args.size)
        for p in people
    ]
    return render_jobs(jobs, args, OUT_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
