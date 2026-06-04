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
These read-only helpers are available in your function's namespace — call them,
don't redefine them:
  - `earliest_start(step, state) -> float`
  - `eligible_steps(state) -> list[Step]`  (the steps runnable right now)
  - `all_steps(state) -> list[Step]`
Do NOT call the mutating placer functions (`place`, `construct`) — they are
shown below for context only and are not available to your heuristic.

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

        Example order set (ONE of several you are scored on):
        {scenario.orders}

        Objective: minimize total_lateness = sum of per-order lateness.

        IMPORTANT: your heuristic is scored on the AVERAGE total_lateness across
        SEVERAL different order sets, not just the one above. Write a general
        rule that adapts to whatever orders/dishes/due-times it's given — do not
        hard-code to the specific orders shown here.
    """).strip()


def initial_prompt(scenario: Scenario) -> str:
    return (
        problem_brief(scenario)
        + "\n\nPropose your first heuristic as a `priority(step, state)` function."
    )


def _tried_block(attempts: list[dict], limit: int = 24) -> str:
    """Format prior attempts into a compact 'already tried' list for the prompt:
    each line is the rule, its score (or that it failed). Most-recent-first,
    capped so the prompt stays focused."""
    if not attempts:
        return ""
    lines = []
    for a in attempts[:limit]:
        if a["ok"] and a["lateness"] is not None:
            score = f"lateness {a['lateness']:.1f}"
        else:
            score = "failed (no valid schedule)"
        rule = a["summary"] or a["title"]
        lines.append(f"  - {rule} — {score}")
    return "\n".join(lines)


def reproduce_prompt(scenario: Scenario, champion: dict) -> str:
    """A fresh run's FIRST prompt. Hands over the best heuristic found so far
    (the champion of earlier runs) and asks to REPRODUCE it — locking the known
    floor back in before we try to improve on it. Deliberately NOT a request for
    a new idea: that comes on later refinements and at pivots, so a run can never
    start worse than the heuristic we already had."""
    return (
        problem_brief(scenario)
        + f'\n\nThe best heuristic discovered so far (across earlier runs) is '
          f'"{champion["title"]}", with average total_lateness = {champion["lateness"]:.1f}. '
          f"It is our current champion and the bar to beat:\n\n"
        + "```python\n" + champion["code"] + "\n```\n\n"
        + "For THIS first step, reproduce it as your `priority(step, state)` "
          "function — keep its core idea intact. You may fix only clear bugs or "
          "obvious inefficiencies; do NOT redesign it or swap its dominant signal. "
          "We will try to improve on it in the next steps."
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


def breakout_prompt(scenario: Scenario, best_so_far: Optional[float], plateau_n: int,
                    tried: list[dict] | None = None) -> str:
    """Issued after a plateau: stop tweaking and try a fundamentally different
    strategy. The conversation history holds the recent attempts; we also list
    the distinct approaches tried across ALL runs (deduped) so the new direction
    avoids retreading them."""
    beat = (f"None of them beat total_lateness = {best_so_far:.1f}."
            if best_so_far is not None else
            "None of them produced a valid schedule.")
    tried_block = _tried_block(tried or [])
    catalogue = (
        f"\n\nApproaches already tried across all runs — do NOT repeat any of "
        f"these:\n{tried_block}\n"
        if tried_block else ""
    )
    return (
        problem_brief(scenario)
        + f"\n\nThe last {plateau_n} attempts have plateaued. {beat} "
          "Incremental tweaks have stopped helping."
        + catalogue
        + "\nStep BACK and propose a FUNDAMENTALLY DIFFERENT `priority(step, state)` "
          "strategy — not a variation of the recent attempts. Change the core idea: "
          "switch the dominant signal (deadline urgency ↔ slack per remaining work ↔ "
          "station congestion / bottleneck ↔ shortest- or longest-processing-time ↔ "
          "critical-path lookahead), or combine signals in a way you haven't tried yet. "
          "Be bold but keep it interpretable."
    )


def hard_breakout_prompt(
    bottleneck: Scenario,
    converged_value: float,
    per_scenario: list[tuple[str, float]],
) -> str:
    """Issued on a META-plateau: the global best has survived several pivots —
    many different signals all collapse to the SAME schedule. Inventing another
    urgency-flavoured rule won't help. So we stop leaning on the champion, show
    WHERE the lateness actually concentrates, and aim the model at that one
    bottleneck order set instead of asking for yet another generic signal.

    `bottleneck` is the worst-scoring scenario (shown in the brief); `per_scenario`
    is the converged rule's lateness on each order set."""
    breakdown = "\n".join(f"  - {name}: lateness {lat:.1f}" for name, lat in per_scenario)
    worst = bottleneck.name
    return (
        problem_brief(bottleneck)
        + f"\n\nWe have CONVERGED. Several genuinely different priority signals "
          f"(deadline urgency, slack-per-work, critical path, station congestion) "
          f"have all collapsed to the SAME schedule, scoring average total_lateness "
          f"= {converged_value:.1f}. The greedy placer produces an identical placement "
          f"regardless of which of these you pick — so proposing yet another "
          f"urgency-flavoured rule will NOT move the score.\n\n"
          f"Here is where the lateness actually sits, per order set:\n{breakdown}\n\n"
          f"Some order sets are already solved (lateness 0); the rest are not, and "
          f"the hardest is '{worst}' (shown in full above). Do NOT reproduce the "
          f"best-so-far, and do NOT just re-rank by deadline. Diagnose what makes the "
          f"unsolved order sets — '{worst}' especially — hard: too many orders "
          f"contending for one station, a long dish chain that must start early, a "
          f"tight cluster of due times. Then propose a `priority(step, state)` that "
          f"changes the placement ORDER in exactly those congested moments: look "
          f"ahead to which station will be the bottleneck and protect it, or break "
          f"ties by something other than urgency. Keep it interpretable, and keep it "
          f"general — it is still scored on the average across ALL the order sets, "
          f"not just '{worst}'."
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
        RULE: <the rule as ONE short sentence — about 6 to 14 words — that a
               line cook could memorise. Plain kitchen language only (orders,
               dishes, due times, how busy a station is). NO code, NO variable
               or function names, NO markdown, NO jargon like "step" or
               "state". e.g. "Cook the order with the least spare time first.">

        Do not write anything after the RULE line. Do not include any code.

        ```python
        {code}
        ```
    """).strip()


_TITLE_RE = re.compile(r"TITLE:\s*(.*?)\s*(?:RULE:|SUMMARY:|$)", re.DOTALL | re.IGNORECASE)
_RULE_RE  = re.compile(r"(?:RULE|SUMMARY):\s*(.*)", re.DOTALL | re.IGNORECASE)


def _one_liner(text: str, limit: int = 130) -> str:
    """Force a model fragment down to a single short, code-free line: drop any
    fenced code, keep the first non-empty line, strip quotes/backticks, and cap
    the length. Guards against the model dumping a paragraph or code block."""
    text = text.split("```")[0]                                  # drop any code block
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    s = " ".join(line.split()).strip().strip('`"').strip()
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return s


def parse_description(reply: str) -> tuple[str, str]:
    """Split a describe reply into (title, rule). The rule is hard-clamped to a
    single short, code-free line so it stays a kitchen-readable instruction.
    Degrades gracefully if the model ignores the format."""
    t = _TITLE_RE.search(reply)
    r = _RULE_RE.search(reply)
    title = _one_liner(t.group(1), limit=60) if t else ""
    rule  = _one_liner(r.group(1)) if r else _one_liner(reply)
    rule  = rule or title
    title = title or (rule[:60] if rule else "Untitled heuristic")
    return title, rule
