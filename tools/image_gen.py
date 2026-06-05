"""Shared plumbing for the image-generation scripts (portraits, scenes, mark).

Each script supplies its own art direction and output layout, then hands a list
of `Job`s to `render_jobs()`, which owns the OpenAI client, the shared CLI flags,
the skip-if-exists / overwrite logic, and the status output. The per-script
files stay small — just the prompts and how to turn `--only` into a job list.

    export OPENAI_API_KEY=sk-...
"""

import argparse
import base64
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    """One image to render: a human label for status lines, the output path, the
    prompt, and the image size / background passed to the API."""
    label: str
    dest: Path
    prompt: str
    size: str = "1024x1024"
    background: str = "opaque"   # or "transparent"


def add_common_args(ap: argparse.ArgumentParser, *, default_quality: str = "medium") -> None:
    """Register the flags every image script shares (`--force/--quality/--model`).
    Scripts add their own `--only` (its choices/semantics differ per script)."""
    ap.add_argument("--force", action="store_true", help="overwrite images that already exist")
    ap.add_argument("--quality", default=default_quality, choices=["low", "medium", "high"],
                    help=f"image quality (cost grows with quality; default: {default_quality})")
    ap.add_argument("--model", default="gpt-image-1", help="OpenAI image model")


def render_jobs(jobs: list[Job], args, out_dir: Path) -> int:
    """Render each job to its dest, skipping existing files unless `--force`.
    One failure never aborts the batch. Returns a process exit code (0 ok,
    1 if everything failed, 2 if the SDK is missing)."""
    try:
        from openai import OpenAI
    except ImportError:
        print("The openai SDK isn't installed. Run:  pip install openai", file=sys.stderr)
        return 2

    client = OpenAI()   # reads OPENAI_API_KEY from the environment
    out_dir.mkdir(parents=True, exist_ok=True)

    made = skipped = failed = 0
    for job in jobs:
        if job.dest.exists() and not args.force:
            print(f"· skip   {job.label}  ({job.dest.name} exists)")
            skipped += 1
            continue
        print(f"… render {job.label} …", flush=True)
        try:
            res = client.images.generate(
                model=args.model, prompt=job.prompt,
                size=job.size, quality=args.quality, n=1, background=job.background,
            )
            job.dest.write_bytes(base64.b64decode(res.data[0].b64_json))
            print(f"✓ saved  {job.dest.name}  ({job.size})")
            made += 1
        except Exception as exc:   # one bad image shouldn't abort the batch
            print(f"✗ failed {job.label}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone — {made} made, {skipped} skipped, {failed} failed. → {out_dir}")
    return 1 if failed and not made else 0
