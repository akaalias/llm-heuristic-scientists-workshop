"""Independent constraint validator. Verifies any schedule, regardless of how it was built."""

from problem_definition.model import RECIPES
from problem_definition.step_id import step_id
from util.infra   import OrderSpec, ScheduleEntry


def check(schedule: list[ScheduleEntry], orders: list[OrderSpec], kitchen: dict[str, int]) -> list[str]:
    """Return a list of violation strings. Empty list means the schedule is valid.

    Checks:
      - every expected step appears exactly once
      - steps run on their required station
      - durations match the recipe
      - intra-dish precedence is respected
      - steps do not start before their order arrives
      - station capacity is never exceeded
    """
    EPS = 1e-9
    violations: list[str] = []

    expected: dict[str, dict] = {}
    for order in orders:
        oid = order.id
        for d_idx, dish_name in enumerate(order.dishes):
            prev_sid: str | None = None
            for s_idx, (duration, station) in enumerate(RECIPES[dish_name]):
                sid = step_id(oid, d_idx, s_idx)
                expected[sid] = {
                    "duration": float(duration),
                    "station":  station,
                    "prev":     prev_sid,
                    "arrival":  order.arrival,
                }
                prev_sid = sid

    by_sid: dict[str, ScheduleEntry] = {}
    for entry in schedule:
        if entry.step in by_sid:
            violations.append(f"step {entry.step} scheduled more than once")
        by_sid[entry.step] = entry

    for sid, e in expected.items():
        if sid not in by_sid:
            violations.append(f"step {sid} not scheduled")
            continue
        s = by_sid[sid]
        if abs((s.end - s.start) - e["duration"]) > EPS:
            violations.append(
                f"step {sid} duration {s.end - s.start:.3f} != expected {e['duration']:.3f}"
            )
        if s.station != e["station"]:
            violations.append(
                f"step {sid} on station {s.station} but recipe needs {e['station']}"
            )
        if s.start < e["arrival"] - EPS:
            violations.append(
                f"step {sid} starts at {s.start:.3f} before arrival {e['arrival']}"
            )
        if e["prev"] is not None and e["prev"] in by_sid:
            prev_end = by_sid[e["prev"]].end
            if s.start < prev_end - EPS:
                violations.append(
                    f"step {sid} starts at {s.start:.3f} before prereq {e['prev']} ends at {prev_end:.3f}"
                )

    for station, capacity in kitchen.items():
        events: list[tuple[float, int]] = []
        for entry in schedule:
            if entry.station == station:
                events.append((entry.start, +1))
                events.append((entry.end,   -1))
        # At the same instant, process finishes (-1) before starts (+1)
        events.sort(key=lambda e: (e[0], e[1]))
        in_use = 0
        for t, delta in events:
            in_use += delta
            if in_use > capacity:
                violations.append(
                    f"station {station} over capacity at t={t}: {in_use} > {capacity}"
                )
                break

    return violations
