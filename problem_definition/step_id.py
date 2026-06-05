"""Canonical step-id format, shared across the placer, validator, evaluators,
and dashboard.

A step id encodes which order, dish, and recipe step a schedule entry belongs
to: "o{order}.d{dish}.s{step}", e.g. "o1.d0.s2". Keep construction and parsing
here so the format lives in exactly one place — it is otherwise easy to drift
across the half-dozen call sites that build or pick it apart.
"""


def step_id(order_id: int, dish_idx: int, step_idx: int) -> str:
    """Build a step id, e.g. step_id(1, 0, 2) -> 'o1.d0.s2'."""
    return f"o{order_id}.d{dish_idx}.s{step_idx}"


def dish_id(order_id: int, dish_idx: int) -> str:
    """Build a dish id (a step id without the step component), e.g. 'o1.d0'."""
    return f"o{order_id}.d{dish_idx}"


def parse_step_id(sid: str) -> tuple[int, int, int]:
    """Parse 'o1.d0.s2' -> (1, 0, 2). Raises ValueError on a malformed id."""
    try:
        o, d, s = sid.split(".")
        return int(o[1:]), int(d[1:]), int(s[1:])
    except (ValueError, IndexError):
        raise ValueError(f"malformed step id: {sid!r}")
