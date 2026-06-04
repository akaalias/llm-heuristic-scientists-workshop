"""Prompt construction and LLM-reply parsing for the discovery loop.

Kept separate from `discover.py` so the loop stays focused on orchestration.
"""

import re
import textwrap
from pathlib import Path
from typing import Optional

from util.infra import Scenario


HERE       = Path(__file__).resolve().parent
REPO_ROOT  = HERE.parent
PLACER_SRC = (HERE / "placer.py").read_text()
MODEL_SRC  = (REPO_ROOT / "problem_definition" / "model.py").read_text()


SYSTEM = f"""\
You are a scheduling researcher. You propose interpretable priority
heuristics for a restaurant-kitchen scheduling problem and express each
one as a single Python function:

    def priority(step, state) -> float

The placer is a list-builder: at each iteration it picks the highest-priority
eligible step (one whose prereq is None or already placed) and commits it to
its station at the earliest feasible time. Higher priority = place this
step next.

Rules:
  - reply with one fenced ```python``` block, nothing else
  - the block must define exactly one top-level function named `priority`
  - no imports beyond the Python stdlib; no I/O; no randomness
  - the heuristic must be read-only: do not mutate step or state

The shape of `step` and `state` is defined by problem_definition/model.py below; the
placement loop is in placer.py. Treat both as the authoritative spec.
A helper `earliest_start(step, state) -> float` is available in the global
namespace (and defined in problem_definition/model.py).

===== problem_definition/model.py =====
{MODEL_SRC}
===== placer.py =====
{PLACER_SRC}"""


def problem_brief(scenario: Scenario) -> str:
    return textwrap.dedent(f"""
        Problem semantics:
          - An order contains one or more dishes. A dish is a linear chain of steps
            (step i depends on step i-1 via `step.prereq`). No dependencies across dishes.
          - Lateness is measured per ORDER: an order finishes when all its
            dishes finish, and lateness = max(0, finish - due).
          - Stations have limited capacity (see STATION_CAPACITY above);
            "waiting" is passive (resting/cooling) and doesn't occupy a chef.

        Training orders:
        {scenario.orders}

        Objective: minimize total_lateness = sum of per-order lateness.
    """).strip()


def initial_prompt(scenario: Scenario) -> str:
    return (
        problem_brief(scenario)
        + "\n\nPropose your first heuristic as a `priority(step, state)` function."
    )


def refine_prompt(
    scenario: Scenario,
    prev_value: Optional[float],
    prev_error: Optional[str],
    best_so_far: Optional[float],
) -> str:
    """The previous heuristic is already in the conversation history as the
    last assistant message, so we don't re-embed its code here — just the
    feedback on how it scored."""
    if prev_error:
        feedback = f"Your previous attempt raised an exception:\n{prev_error}"
    else:
        feedback = (
            f"Your previous attempt scored total_lateness = {prev_value:.1f}\n"
            f"Best total_lateness so far: {best_so_far:.1f}"
        )
    return (
        problem_brief(scenario)
        + "\n\n" + feedback
        + "\n\nPropose an improved `priority(step, state)` function. "
          "Diagnose what likely hurt the previous attempt and fix it. "
          "Keep the function interpretable."
    )


_CODE_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)

def extract_code(reply: str) -> str:
    m = _CODE_RE.search(reply)
    return (m.group(1) if m else reply).strip()


def describe_prompt(code: str) -> str:
    """Ask the model to describe a heuristic in plain kitchen terms: a short
    title, a one-line summary, and a fuller paragraph. Returned in a fixed,
    easily-parsed format."""
    return textwrap.dedent(f"""
        Below is a priority heuristic for the restaurant-kitchen scheduler.
        Describe it for a human reader in plain kitchen terms (orders, dishes,
        steps, stations like grill/prep, due times, lateness) — describe the
        BEHAVIOUR and strategy, not the code syntax.

        Reply in EXACTLY this format and nothing else:

        TITLE: <a short, distinctive name for the strategy, 2-6 words>
        SUMMARY: <one sentence: what this heuristic does, in plain terms>
        EXPLANATION: <2-4 sentences on the rule it uses and the idea behind it>

        ```python
        {code}
        ```
    """).strip()


_TITLE_RE   = re.compile(r"TITLE:\s*(.*?)\s*(?:SUMMARY:|EXPLANATION:|$)", re.DOTALL | re.IGNORECASE)
_SUMMARY_RE = re.compile(r"SUMMARY:\s*(.*?)\s*(?:EXPLANATION:|$)", re.DOTALL | re.IGNORECASE)
_EXPLAIN_RE = re.compile(r"EXPLANATION:\s*(.*)", re.DOTALL | re.IGNORECASE)

def parse_description(reply: str) -> tuple[str, str, str]:
    """Split a describe reply into (title, summary, explanation), each
    flattened to a single line. Degrades gracefully if the model ignores the
    format: falls back to the first sentence as title/summary."""
    def grab(rx):
        m = rx.search(reply)
        return " ".join(m.group(1).split()) if m else ""
    title, summary, explanation = grab(_TITLE_RE), grab(_SUMMARY_RE), grab(_EXPLAIN_RE)
    if not (title or summary or explanation):
        flat = " ".join(reply.split())
        summary = flat[:200]
        explanation = flat
    summary = summary or explanation.split(". ")[0][:200]
    title = title or (summary.split(". ")[0][:60] if summary else "Untitled heuristic")
    explanation = explanation or summary
    return title, summary, explanation
