"""Problem instances. Each scenario bundles a kitchen and a set of orders.

Every scenario is a night Le Petit Renard actually has — a real beat of the
service, read off the hours in model.py (a quiet Tuesday open, the early
Seaport crowd before the show, the Saturday peak in the long five-till-midnight
window, a cold night when the whole room wants the range). Times are still
abstract minutes from the start of the seating, not wall-clock; the narrative
lives in the names and blurbs, the tension lives in the orders and deadlines.

The battery the discovery loop trains on (TRAINING_BATTERY) is a DELIBERATE
spread of bottleneck archetypes:

    grill-bound   (steaks + burgers — Priya's single fire)
    range-bound   (pasta + soup — Marco's single range)
    fryer / cold  (fries, soup, salad — the quick-and-light early rush)
    both at once  (the late seating, where neither station has slack)

The point is generalisation: a heuristic that only learns "protect the grill"
must lose on the range-heavy and fryer-heavy nights, so the winners have to
reason about whichever station is actually the wall. The two leaderboard
nights, HIDDEN_TEST and STRESS, are deliberately HELD OUT of the training
battery — they're the unseen covers that tell us whether a method generalised
or merely memorised.
"""

from problem_definition.model import STATION_CAPACITY
from util.infra   import OrderSpec, Scenario


# ---- the calm open: also the Gantt source for the dashboard ----------------
# Kept gentle and representative on purpose — it's the first battery sample, so
# the detail-view schedule diagram is always drawn from this familiar night.

TRAINING = Scenario(
    name    = "training",
    title   = "The House Service",
    blurb   = ("The doors open on a quiet Tuesday and the room fills gently. Two "
               "tables order the moment they sit — a burger and fries, a steak and "
               "salad — and two more drift in over the next ten minutes. Easy "
               "tickets on their own, except two burgers and a steak are all bound "
               "for Priya's single grill."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival=0, due=25, dishes=["burger", "fries"]),
        OrderSpec(id=2, arrival=0, due=30, dishes=["steak",  "salad"]),
        OrderSpec(id=3, arrival=5, due=35, dishes=["pasta"]),
        OrderSpec(id=4, arrival=8, due=40, dishes=["burger", "salad"]),
    ],
)


# ---- the two held-out leaderboard nights (NOT in the training battery) ------

HIDDEN_TEST = Scenario(
    name    = "hidden_test",
    title   = "The Unseen Cover",
    blurb   = ("A held-out Thursday the kitchen never rehearsed on: five varied "
               "tables, a mix of grill, range and cold orders trickling in over ten "
               "minutes, with one heavy three-dish ticket buried in the middle. The "
               "true test of whether a method generalises or merely memorised the "
               "nights it trained on."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival= 0, due=22, dishes=["pasta",  "salad"]),
        OrderSpec(id=2, arrival= 0, due=28, dishes=["burger"]),
        OrderSpec(id=3, arrival= 3, due=30, dishes=["steak",  "fries", "salad"]),
        OrderSpec(id=4, arrival= 6, due=35, dishes=["soup"]),
        OrderSpec(id=5, arrival=10, due=45, dishes=["burger", "fries"]),
    ],
)

# Grill-heavy with tight deadlines — the Saturday peak, designed to punish
# bottleneck-blind heuristics.
STRESS = Scenario(
    name    = "stress",
    title   = "The Saturday Crush",
    blurb   = ("Saturday at full tilt, the long five-till-midnight window at its "
               "peak. A packed room all wanting red meat — steaks and burgers "
               "stacked on nearly every ticket with the tightest deadlines of the "
               "week. The grill is swamped, and any cook who ignores the bottleneck "
               "buries the whole service."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival=0, due=22, dishes=["burger", "steak"]),
        OrderSpec(id=2, arrival=0, due=25, dishes=["steak",  "fries"]),
        OrderSpec(id=3, arrival=2, due=28, dishes=["burger"]),
        OrderSpec(id=4, arrival=4, due=32, dishes=["steak"]),
        OrderSpec(id=5, arrival=6, due=35, dishes=["burger", "salad"]),
    ],
)


# ---- the training battery: hand-authored bottleneck archetypes --------------
# One night per wall. Hand-written (not generated) so every ticket and its
# blurb are deliberately matched: what the floor sees is what the scheduler
# gets. TRAINING (above) stays first so the Gantt is always the same example.

# A quick, light early seating: the fryer never stops and the range is several
# pots deep, but the grill barely fires. Punishes a grill-only heuristic.
TRAINING_V2 = Scenario(
    name    = "training_v2",
    title   = "The Early Rush",
    blurb   = ("The first Seaport crowd piles in off the bridge wanting something "
               "quick before the show — baskets of fries, bowls of the day's soup, "
               "a salad to share. Light on the grill, but the fryer never stops and "
               "Marco's range is three pots deep, every ticket on a short fuse."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival= 0, due=18, dishes=["fries", "salad"]),
        OrderSpec(id=2, arrival= 0, due=20, dishes=["soup"]),
        OrderSpec(id=3, arrival= 2, due=22, dishes=["fries", "soup"]),
        OrderSpec(id=4, arrival= 4, due=24, dishes=["salad", "fries"]),
        OrderSpec(id=5, arrival= 6, due=26, dishes=["soup",  "salad"]),
        OrderSpec(id=6, arrival= 8, due=30, dishes=["burger"]),
    ],
)

# A small table that hits the grill hard: two steaks and two burgers almost on
# top of each other, four cuts of meat queued at a single fire.
TRAINING_V3 = Scenario(
    name    = "training_v3",
    title   = "The Grill Jam",
    blurb   = ("A small table that hits the grill hard. Two steaks and two burgers "
               "land almost on top of each other — four cuts of meat queued at a "
               "single fire while the salad and fries wait on the cold side. "
               "Mis-order Priya's grill and every plate lands late."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival=0, due=26, dishes=["steak",  "salad"]),
        OrderSpec(id=2, arrival=0, due=24, dishes=["burger", "fries"]),
        OrderSpec(id=3, arrival=3, due=30, dishes=["steak"]),
        OrderSpec(id=4, arrival=6, due=34, dishes=["burger"]),
    ],
)

# A cold, wet night: comfort food by the bowl and the plate. Marco's single
# range is the whole game; here the grill is the station with slack to spare.
TRAINING_V4 = Scenario(
    name    = "training_v4",
    title   = "The Range Run",
    blurb   = ("A cold, wet night and the whole room wants comfort: pasta after "
               "pasta, the day's soup by the bowl. Marco's single range is the whole "
               "game — five pots deep with barely a burner to spare — while the odd "
               "salad and basket of fries slip by on the side."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival=0, due=22, dishes=["soup",  "salad"]),
        OrderSpec(id=2, arrival=0, due=26, dishes=["pasta"]),
        OrderSpec(id=3, arrival=4, due=30, dishes=["soup",  "fries"]),
        OrderSpec(id=4, arrival=6, due=32, dishes=["pasta", "salad"]),
        OrderSpec(id=5, arrival=9, due=36, dishes=["soup"]),
    ],
)

# The hardest training night: late tables, short clocks, and BOTH walls buried
# at once — grill and range loaded together, no slack to hide a bad call.
TRAINING_V5 = Scenario(
    name    = "training_v5",
    title   = "The Late Seating",
    blurb   = ("The last tables of the night sit down late and still want to eat "
               "fast. Grill and range are both buried — steaks and burgers stacked "
               "on one side, pasta and soup on the other — and every clock is short. "
               "Both bottlenecks bite at once; there's no slack left to hide a bad "
               "call."),
    kitchen = dict(STATION_CAPACITY),
    orders  = [
        OrderSpec(id=1, arrival= 0, due=20, dishes=["burger", "soup"]),
        OrderSpec(id=2, arrival= 2, due=24, dishes=["steak",  "fries"]),
        OrderSpec(id=3, arrival= 4, due=26, dishes=["pasta",  "salad"]),
        OrderSpec(id=4, arrival= 7, due=28, dishes=["burger"]),
        OrderSpec(id=5, arrival=10, due=30, dishes=["steak"]),
        OrderSpec(id=6, arrival=12, due=34, dishes=["soup",   "salad"]),
    ],
)


ALL_SCENARIOS = [TRAINING, HIDDEN_TEST, STRESS]   # the leaderboard set

# A heuristic that only wins on TRAINING is overfit. The discovery loop scores
# each proposal on the AVERAGE lateness across this battery of bottleneck
# archetypes, so the winners have to generalise. TRAINING stays first so the
# schedule diagram is always drawn from the same, familiar example.
TRAINING_VARIANTS = [TRAINING_V2, TRAINING_V3, TRAINING_V4, TRAINING_V5]
TRAINING_BATTERY  = [TRAINING, *TRAINING_VARIANTS]   # TRAINING first = Gantt source
