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

from tools.image_gen import Job, add_common_args, render_jobs
from tools.paths import STATIC_DIR

OUT_DIR = STATIC_DIR / "scenes"

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
    add_common_args(ap)
    args = ap.parse_args()

    keys = [args.only] if args.only else list(SCENES)
    jobs = [
        Job(label=key, dest=OUT_DIR / f"{key}.png",
            prompt=ENGRAVING.format(desc=SCENES[key]["desc"]), size=SCENES[key]["size"])
        for key in keys
    ]
    return render_jobs(jobs, args, OUT_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
