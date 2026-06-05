"""Multi-metric scoring of a schedule. The evaluator is objective-agnostic;
choose which metric to optimize when comparing heuristics.
"""

from problem_definition.model import RECIPES
from problem_definition.step_id import dish_id, step_id
from util.infra   import OrderSpec, ScheduleEntry


def evaluate(schedule: list[ScheduleEntry], orders: list[OrderSpec]) -> dict:
    by_sid: dict[str, ScheduleEntry] = {entry.step: entry for entry in schedule}

    dish_completion:  dict[str, float] = {}
    order_completion: dict[int, float] = {}

    for order in orders:
        oid = order.id
        order_ends: list[float] = []
        for d_idx, dish_name in enumerate(order.dishes):
            slot = dish_id(oid, d_idx)
            dish_ends: list[float] = []
            for s_idx, _ in enumerate(RECIPES[dish_name]):
                sid = step_id(oid, d_idx, s_idx)
                if sid in by_sid:
                    dish_ends.append(by_sid[sid].end)
            if dish_ends:
                dish_completion[slot] = max(dish_ends)
                order_ends.extend(dish_ends)
        if order_ends:
            order_completion[oid] = max(order_ends)

    due_by_oid = {o.id: o.due for o in orders}
    per_order_lateness = {
        oid: max(0.0, order_completion[oid] - due_by_oid[oid])
        for oid in order_completion
    }
    total_lateness = sum(per_order_lateness.values())
    max_lateness   = max(per_order_lateness.values()) if per_order_lateness else 0.0
    num_late       = sum(1 for v in per_order_lateness.values() if v > 0)

    sync_penalty = 0.0
    for order in orders:
        oid = order.id
        ends = [
            dish_completion[dish_id(oid, d_idx)]
            for d_idx, _ in enumerate(order.dishes)
            if dish_id(oid, d_idx) in dish_completion
        ]
        if len(ends) > 1:
            sync_penalty += max(ends) - min(ends)

    makespan       = max(order_completion.values()) if order_completion else 0.0
    avg_completion = (sum(order_completion.values()) / len(order_completion)) if order_completion else 0.0

    return {
        "total_lateness":       total_lateness,
        "max_lateness":         max_lateness,
        "num_late":             num_late,
        "sync_penalty":         sync_penalty,
        "makespan":             makespan,
        "avg_completion":       avg_completion,
        "per_order_lateness":   per_order_lateness,
        "per_order_completion": order_completion,
        "per_dish_completion":  dish_completion,
    }
