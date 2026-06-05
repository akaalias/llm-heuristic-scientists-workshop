"""Orchestration-only types. These are how the discovery loop and runner
hand a problem instance to the placer and read back its output. The
LLM-authored heuristic never sees any of these — they live outside its
priority(step, state) contract.
"""

from dataclasses import dataclass, field


@dataclass
class OrderSpec:
    """Input declaration of a customer ticket: dishes (by name) due by a deadline.

    This is the wire format scenarios use. The placer turns each OrderSpec into
    a fully-materialized problem_definition.model.Order graph (Dish + Step objects) via
    init_state. Heuristics never see OrderSpec — they walk the runtime graph.
    """
    id:      int
    arrival: float
    due:     float
    dishes:  list[str]            # dish names (keys into problem_definition.model.RECIPES)


@dataclass
class Scenario:
    """A problem instance: a kitchen (station -> capacity) and a list of orders.

    `name` is the stable identifier (used as a key for grouping schedules,
    filenames, etc. — don't change it lightly). `title` and `blurb` are optional
    flavour: a human-readable name and a one-line, observer's-eye sketch of what
    that service looks like from the dining room. Neither is seen by the
    scheduler or the heuristic — they're purely for the dashboard.
    """
    name:    str
    kitchen: dict[str, int]
    orders:  list[OrderSpec]
    title:   str = ""
    blurb:   str = ""


@dataclass
class ScheduleEntry:
    """One row of a built schedule. Produced by the placer, consumed by check/evaluate."""
    step:    str                  # step id
    station: str
    start:   float
    end:     float
