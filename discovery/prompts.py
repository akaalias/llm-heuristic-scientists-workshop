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
    """Ask the model to name a heuristic and turn it into a memorable, one-line
    instruction the kitchen team could follow to decide what to cook next.
    Returned in a fixed, easily-parsed format."""
    return textwrap.dedent(f"""
        Below is a priority heuristic for the restaurant-kitchen scheduler.
        Distill it into something a line cook could remember and act on.

        Reply in EXACTLY this format and nothing else:

        TITLE: <a short, distinctive name for the rule, 2-6 words>
        RULE: <ONE short, memorable instruction the kitchen team can follow to
               decide which step to cook next — imperative and concrete, in
               plain kitchen terms (orders, due times, remaining work, busy
               stations), no code. e.g. "Start the order with the least slack
               per minute of work still left.">

        ```python
        {code}
        ```
    """).strip()


_TITLE_RE = re.compile(r"TITLE:\s*(.*?)\s*(?:RULE:|SUMMARY:|$)", re.DOTALL | re.IGNORECASE)
_RULE_RE  = re.compile(r"(?:RULE|SUMMARY):\s*(.*)", re.DOTALL | re.IGNORECASE)

def parse_description(reply: str) -> tuple[str, str]:
    """Split a describe reply into (title, rule), each flattened to a single
    line. Degrades gracefully if the model ignores the format."""
    def grab(rx):
        m = rx.search(reply)
        return " ".join(m.group(1).split()) if m else ""
    title, rule = grab(_TITLE_RE), grab(_RULE_RE)
    if not (title or rule):
        rule = " ".join(reply.split())[:200]
    rule = rule or title
    title = title or (rule.split(". ")[0][:60] if rule else "Untitled heuristic")
    return title, rule
