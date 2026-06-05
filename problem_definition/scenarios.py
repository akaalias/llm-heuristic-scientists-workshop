"""Problem instances. Each scenario bundles a kitchen and a set of orders."""

import random

from problem_definition.model import RECIPES, STATION_CAPACITY
from util.infra   import OrderSpec, Scenario


TRAINING = Scenario(
    name    = "training",
    title   = "The House Service",
    blurb   = ("A calm opening seating. Two tables order the moment the doors open — "
               "one a burger and fries, the other a steak and salad — and two more "
               "drift in over the next ten minutes. Unremarkable tickets, except that "
               "two burgers and a steak are all bound for the single grill."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival=0, due=25, dishes=["burger", "fries"]),
        OrderSpec(id=2, arrival=0, due=30, dishes=["steak",  "salad"]),
        OrderSpec(id=3, arrival=5, due=35, dishes=["pasta"]),
        OrderSpec(id=4, arrival=8, due=40, dishes=["burger", "salad"]),
    ],
)

HIDDEN_TEST = Scenario(
    name    = "hidden_test",
    title   = "The Unseen Cover",
    blurb   = ("A held-out night the kitchen never rehearsed on: five varied tables, "
               "a mix of grill, range and cold orders trickling in over ten minutes. "
               "The true test of whether a method generalises or merely memorised."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival= 0, due=22, dishes=["pasta",  "salad"]),
        OrderSpec(id=2, arrival= 0, due=28, dishes=["burger"]),
        OrderSpec(id=3, arrival= 3, due=30, dishes=["steak",  "fries", "salad"]),
        OrderSpec(id=4, arrival= 6, due=35, dishes=["soup"]),
        OrderSpec(id=5, arrival=10, due=45, dishes=["burger", "fries"]),
    ],
)

# Grill-heavy with tight deadlines — designed to punish bottleneck-blind heuristics.
STRESS = Scenario(
    name    = "stress",
    title   = "The Saturday Crush",
    blurb   = ("A full house all wanting red meat. Steaks and burgers stack up on "
               "every ticket with the tightest deadlines of the week — the grill is "
               "swamped, and any cook who ignores the bottleneck buries the service."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival=0, due=22, dishes=["burger", "steak"]),
        OrderSpec(id=2, arrival=0, due=25, dishes=["steak",  "fries"]),
        OrderSpec(id=3, arrival=2, due=28, dishes=["burger"]),
        OrderSpec(id=4, arrival=4, due=32, dishes=["steak"]),
        OrderSpec(id=5, arrival=6, due=35, dishes=["burger", "salad"]),
    ],
)

ALL_SCENARIOS = [TRAINING, HIDDEN_TEST, STRESS]


# ---- training battery -------------------------------------------------------
# A heuristic that only wins on TRAINING is overfit. The discovery loop scores
# each proposal on the AVERAGE lateness across this battery of order
# combinations, so the winners have to generalise. Generated deterministically
# (fixed seeds) so runs are reproducible; TRAINING stays first so the schedule
# diagram is always drawn from the same, familiar example.

_RECIPE_NAMES = list(RECIPES)


def _generate_scenario(name: str, seed: int, n_orders: int,
                       title: str = "", blurb: str = "") -> Scenario:
    rng = random.Random(seed)
    orders = []
    for i in range(1, n_orders + 1):
        arrival  = rng.choice([0, 0, 2, 4, 6, 8, 10])
        n_dishes = rng.choice([1, 1, 2, 2, 3])
        dishes   = [rng.choice(_RECIPE_NAMES) for _ in range(n_dishes)]
        due      = arrival + rng.randint(16, 34)
        orders.append(OrderSpec(id=i, arrival=arrival, due=due, dishes=dishes))
    return Scenario(name=name, kitchen=dict(STATION_CAPACITY), orders=orders,
                    title=title, blurb=blurb)


# Per-variant flavour, keyed by k (the loop index below). Seeds/order-counts are
# unchanged, so the generated tickets are identical — these only name the night
# and sketch what it looks like from the floor.
_VARIANT_FLAVOUR = {
    2: ("The Fryer Rush",
        "A sudden mid-service wave — six tables almost at once, nearly all of them "
        "wanting fries or soup. Baskets pile up at the fryer and pots crowd the "
        "range, every one of them on a short clock."),
    3: ("The Grill Jam",
        "A small table that hits hard: one party orders two steaks and a burger "
        "together, three slabs of protein queued at a single grill while a "
        "pasta-and-steak order waits behind them."),
    4: ("The Late Seating",
        "Tables that all sit down late and still want to eat fast. A lone order of "
        "fries lands with barely any time on its clock, and the heaviest ticket of "
        "the night — salad, burger and pasta — arrives dead last."),
    5: ("The Pasta Run",
        "A range-heavy night: three of the first tables all order off the stove at "
        "once, so the saucier becomes the bottleneck — while a burger-and-salad on "
        "a short fuse threatens to slip if the grill looks away."),
}


TRAINING_VARIANTS = [
    _generate_scenario(f"training_v{k}", seed=1000 + k, n_orders=4 + (k % 3),
                       title=_VARIANT_FLAVOUR[k][0], blurb=_VARIANT_FLAVOUR[k][1])
    for k in range(2, 6)   # v1 (seed 1001) dropped — it was the dominant lateness bottleneck
]
TRAINING_BATTERY = [TRAINING, *TRAINING_VARIANTS]   # TRAINING first = Gantt source
