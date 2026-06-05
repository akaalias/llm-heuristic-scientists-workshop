"""Schedule scoring. Returns total_lateness, the sum of per-order lateness.

Lateness for an order = max(0, completion - due). The kitchen wants this
number as low as possible.
"""

from problem_definition.model import RECIPES
from problem_definition.step_id import step_id
from util.infra   import OrderSpec, ScheduleEntry


def evaluate(schedule: list[ScheduleEntry], orders: list[OrderSpec]) -> float:
    by_sid = {entry.step: entry for entry in schedule}

    total = 0.0
    for order in orders:
        oid = order.id
        order_ends: list[float] = []
        for d_idx, dish_name in enumerate(order.dishes):
            for s_idx, _ in enumerate(RECIPES[dish_name]):
                sid = step_id(oid, d_idx, s_idx)
                if sid in by_sid:
                    order_ends.append(by_sid[sid].end)
        if order_ends:
            total += max(0.0, max(order_ends) - order.due)
    return total
