"""Shared filesystem locations for the tools/ scripts, so the repo root and the
static asset dir are defined in exactly one place."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "dashboard" / "static"
