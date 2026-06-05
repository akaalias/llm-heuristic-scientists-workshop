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

from tools.image_gen import Job, add_common_args, render_jobs
from tools.paths import STATIC_DIR

OUT_DIR = STATIC_DIR / "marks"

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
    add_common_args(ap, default_quality="high")   # high — it's a small logo
    args = ap.parse_args()

    keys = [args.only] if args.only else list(MARKS)
    jobs = [
        Job(label=key, dest=OUT_DIR / f"{key}.png", prompt=MARKS[key]["prompt"],
            size=MARKS[key]["size"], background="transparent")
        for key in keys
    ]
    return render_jobs(jobs, args, OUT_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
