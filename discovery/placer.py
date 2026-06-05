"""Greedy task placer driven by a priority function.

A heuristic is `priority(step, state) -> float` (higher = place next). The
placer repeatedly picks the highest-priority eligible step (one whose prereq
is None or already placed) and commits it to its station at the earliest
time allowed by its prereq, the station's free slots, and the order's
arrival.

There is no global clock — each step's start/finish times are computed at
the moment it is placed. The heuristic sees the full state (placements so
far, station availability, all orders/dishes/steps) and decides which step
to commit next.
"""

from typing import Callable

from problem_definition.model import (
    RECIPES,
    Dish,
    Order,
    State,
    Station,
    Step,
    earliest_start,
)
from problem_definition.step_id import step_id
from util.infra import OrderSpec, ScheduleEntry


PriorityFn = Callable[[Step, State], float]


def init_state(orders: list[OrderSpec], kitchen: dict[str, int]) -> State:
    state_orders: list[Order] = []
    for spec in orders:
        oid = spec.id
        order = Order(id=oid, arrival=spec.arrival, due=spec.due)
        for d_idx, dish_name in enumerate(spec.dishes):
            dish = Dish(name=dish_name, order=order)
            order.dishes.append(dish)
            prev_step: Step | None = None
            for s_idx, (duration, station) in enumerate(RECIPES[dish_name]):
                step = Step(
                    id        = step_id(oid, d_idx, s_idx),
                    duration  = float(duration),
                    station   = station,
                    dish      = dish,
                    prereq    = prev_step,
                )
                dish.steps.append(step)
                prev_step = step
        state_orders.append(order)

    stations = {
        name: Station(capacity=cap, slot_free_times=[0.0] * cap)
        for name, cap in kitchen.items()
    }
    return State(stations=stations, orders=state_orders)


def all_steps(state: State) -> list[Step]:
    return [s for o in state.orders for d in o.dishes for s in d.steps]


def eligible_steps(state: State) -> list[Step]:
    """Unplaced steps whose prereq is None or already placed."""
    return [
        s for s in all_steps(state)
        if s.started_at is None
        and (s.prereq is None or s.prereq.started_at is not None)
    ]


def place(state: State, step: Step) -> ScheduleEntry:
    """Commit a step to its station at the earliest feasible time."""
    station = state.stations[step.station]
    start = earliest_start(step, state)
    end   = start + step.duration
    slot_idx = min(range(len(station.slot_free_times)), key=lambda i: station.slot_free_times[i])
    station.slot_free_times[slot_idx] = end
    step.started_at  = start
    step.finished_at = end
    return ScheduleEntry(step=step.id, station=step.station, start=start, end=end)


def construct(orders: list[OrderSpec], kitchen: dict[str, int], priority_fn: PriorityFn) -> list[ScheduleEntry]:
    state = init_state(orders, kitchen)
    schedule: list[ScheduleEntry] = []
    total = len(all_steps(state))
    while len(schedule) < total:
        runnable = eligible_steps(state)
        if not runnable:
            raise RuntimeError("no progress possible (cycle or infeasible inputs?)")
        # Primary key: user-defined priority. Tie-break: prefer the step that
        # can start soonest — among equivalents, never starve the kitchen.
        best = max(runnable, key=lambda s: (
            priority_fn(s, state),
            -earliest_start(s, state),
        ))
        schedule.append(place(state, best))
    return schedule
