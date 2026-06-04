"""Runtime helpers for executing an LLM-authored heuristic.

Two helpers used by discover.py:

  - `time_limit(seconds)` — SIGALRM-based timeout, so a buggy `priority`
    that loops forever can't stall the discovery loop.
  - `compile_priority(code)` — exec the LLM's code in a fresh namespace
    with the domain dataclasses and `earliest_start` injected, then
    extract the resulting `priority` callable.

Note: this is *not* a security sandbox. `exec` gives the generated code
full Python access. Treat the discovery loop's process as throwaway.
"""

import signal
from contextlib import contextmanager

from problem_definition.model import (
    Dish, Order, State, Station, Step,
    earliest_start,
)
from discovery.placer import PriorityFn, all_steps, eligible_steps


class HeuristicTimeoutError(TimeoutError):
    pass


@contextmanager
def time_limit(seconds: int):
    """Raise HeuristicTimeoutError if the wrapped block runs longer than
    `seconds`. Unix-only (uses SIGALRM); main thread only."""
    def _handler(signum, frame):
        raise HeuristicTimeoutError(f"exceeded {seconds}s")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def compile_priority(code: str) -> PriorityFn:
    """Exec the LLM-generated code and return its `priority` callable.

    The dataclasses + the read-only helpers (`earliest_start`, `eligible_steps`,
    `all_steps`) are pre-injected so the model can refer to them (including in
    type annotations) without extra imports. The mutating placer functions
    (`place`, `construct`) are deliberately NOT injected.
    """
    ns: dict = {
        "Step": Step, "State": State, "Order": Order, "Dish": Dish, "Station": Station,
        "earliest_start": earliest_start,
        "eligible_steps": eligible_steps, "all_steps": all_steps,
    }
    exec(compile(code, "<llm-heuristic>", "exec"), ns)
    fn = ns.get("priority")
    if not callable(fn):
        raise ValueError("generated code did not define a callable `priority`")
    return fn
