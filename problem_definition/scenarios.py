"""Problem instances. Each scenario bundles a kitchen and a set of orders."""

import random

from problem_definition.model import RECIPES, STATION_CAPACITY
from util.infra   import OrderSpec, Scenario


TRAINING = Scenario(
    name    = "training",
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


def _generate_scenario(name: str, seed: int, n_orders: int) -> Scenario:
    rng = random.Random(seed)
    orders = []
    for i in range(1, n_orders + 1):
        arrival  = rng.choice([0, 0, 2, 4, 6, 8, 10])
        n_dishes = rng.choice([1, 1, 2, 2, 3])
        dishes   = [rng.choice(_RECIPE_NAMES) for _ in range(n_dishes)]
        due      = arrival + rng.randint(16, 34)
        orders.append(OrderSpec(id=i, arrival=arrival, due=due, dishes=dishes))
    return Scenario(name=name, kitchen=dict(STATION_CAPACITY), orders=orders)


TRAINING_VARIANTS = [
    _generate_scenario(f"training_v{k}", seed=1000 + k, n_orders=4 + (k % 3))
    for k in range(1, 6)
]
TRAINING_BATTERY = [TRAINING, *TRAINING_VARIANTS]   # TRAINING first = Gantt source
