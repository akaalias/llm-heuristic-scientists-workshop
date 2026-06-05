"""Domain model that the LLM-authored heuristic sees and reasons about.

A heuristic is a function `priority(step: Step, state: State) -> float`
(higher priority = place this step next). The object graph is:

    Order -> dishes -> Dish -> steps -> Step

Each Step knows its `dish` (back-ref) and its `prereq` (the step before it
in the same dish, or None if it's the dish's first step). There are no
cross-dish dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Restaurant identity — flavour / UI metadata. Not used by the scheduler;
# it gives the kitchen a face for the dashboard and the /grid page.
# ---------------------------------------------------------------------------

RESTAURANT: dict = {
    "name":    "Le Petit Renard",
    "tagline": "From the card, off the coals.",
    "style":   "Small-format à la carte grill / gastro-bistro",
    # Owner's note — the warm, first-person intro shown at the top of the site.
    "welcome": (
        "Welcome to Le Petit Renard. We lit our grill in the old Bridge Cafe — the "
        "little clapboard corner-house that has stood at Water and Dover Streets, in "
        "the shadow of the Brooklyn Bridge, since the 1790s. We kept the creaking "
        "floors and the pressed-tin ceiling, and we cook everything to order over a "
        "single fire. Pull up a chair, order off the card, and let us take care of "
        "the rest — one plate, one table, one perfect bit of timing at a time."
    ),
    # The plain-spoken concept description. Not shown on the site right now (the
    # welcome leads instead), but kept for the about/grid pages later on.
    "description": (
        "A 34-seat neighbourhood grill working a single, lean line. Everything "
        "is fired to order from the card — no set menus, no coursing — so a "
        "table's steak and salad are cooked as independent tickets that simply "
        "land together. The kitchen lives and dies by two stations: the grill, "
        "where every burger and steak queues, and the single range that pasta "
        "and soup fight over. Get the sequencing right and tickets fly; get it "
        "wrong and the pass backs up."
    ),
    "history": (
        "Opened in 2017 in the old Bridge Cafe building at the foot of the "
        "Brooklyn Bridge, Le Petit Renard started as a six-stool counter built "
        "around a single wood-fired grill. The line "
        "never grew much wider — the room did. The team kept the one-grill, "
        "one-range setup on purpose: it forces discipline at the pass and keeps "
        "the menu honest. The house obsession with ticket timing eventually "
        "turned into a side project to let an algorithm sequence the board."
    ),
    # `side` groups the roster into Kitchen vs Front of house on the site.
    # `note` is the one-line role tag; `bio` is the fuller biography. The
    # chef-owner is the one featured in the dedicated "The chef" section.
    "people": [
        {"name": "Élise Marchand",  "role": "Chef-owner",        "side": "Kitchen", "since": 2017,
         "note": "Runs the pass; founded the room around a single grill.",
         "bio": ("Élise trained under Jean-Pierre Vasseur at Le Bristol in Paris, then "
                 "staged at elBulli under Ferran Adrià before coming home to open Le Petit "
                 "Renard in 2017. Her cooking weds Parisian rigour to elBulli's restless "
                 "curiosity — and then strips it back to one fire and honest produce. Her "
                 "vision: that a neighbourhood grill can keep fine-dining timing without "
                 "the fuss — every plate sent at its peak, every table treated like the "
                 "only one in the room.")},
        {"name": "Tomas Vidal",     "role": "Sous-chef",         "side": "Kitchen", "since": 2018,
         "note": "Second on the line; owns prep and the cold station.",
         "bio": ("Came up through Basque asadors before joining Élise in 2018. Quiet, "
                 "fast and unflappable on prep — the quiet engine room of the line.")},
        {"name": "Priya Nair",      "role": "Grillardin",        "side": "Kitchen", "since": 2019,
         "note": "Holds the bottleneck — steaks and burgers, one grill.",
         "bio": ("Five years on live-fire stations across London and Lisbon taught Priya "
                 "to read coals like a thermometer. She owns the grill — and never lets "
                 "it back up.")},
        {"name": "Marco Renzi",     "role": "Saucier",           "side": "Kitchen", "since": 2020,
         "note": "Single range; pasta and soup are his to juggle.",
         "bio": ("Trentino-born and trattoria-trained, Marco turned to the single range "
                 "at Le Petit Renard in 2020. He treats every pot of pasta as if his nonna "
                 "were watching over his shoulder.")},
        {"name": "Jo Abara",        "role": "Friturier / commis","side": "Kitchen", "since": 2021,
         "note": "Fryer plus second pair of hands on prep.",
         "bio": ("Joined straight from culinary school in 2021 — the newest hands on the "
                 "line and the quickest study Élise has hired. Works the fryer and backs "
                 "up the cold station.")},
        {"name": "Camille Fournier","role": "Maître d'hôtel · co-owner", "side": "Front of house", "since": 2017,
         "note": "Élise's partner out front; runs the room, the book and the door.",
         "bio": ("Élise's partner in life and in the room. Camille left a career in "
                 "hospitality management to co-found Le Petit Renard, and remembers every "
                 "regular's name and their usual table.")},
        {"name": "Luca Bianchi",    "role": "Sommelier",         "side": "Front of house", "since": 2019,
         "note": "Pairs the cellar to the grill; knows every bottle's story.",
         "bio": ("Built the cellar from a single shelf into the list it is today. Luca "
                 "champions small growers and can pair the grill just about blindfolded.")},
        {"name": "Sofia Herrera",   "role": "Chef de rang",      "side": "Front of house", "since": 2020,
         "note": "Senior server; keeps the floor and the pass in step.",
         "bio": ("A decade on the floor across three cities. Sofia moves like the dining "
                 "room runs on rails, and keeps the floor and the pass in step.")},
        {"name": "Noah Klein",      "role": "Bartender",         "side": "Front of house", "since": 2021,
         "note": "Aperitifs and the last round; the bar's steady hand.",
         "bio": ("Came in for a summer shift in 2021 and never left. Noah runs aperitifs, "
                 "amari and a steady last round from the corner of the room.")},
    ],
    "ratings": {
        "michelin":      {"award": "Bib Gourmand", "since": 2021},
        "google":        {"score": 4.6, "out_of": 5, "reviews": 1284},
        "tripadvisor":   {"score": 4.5, "out_of": 5, "reviews": 612},
        "local_critics": {"score": 8.7, "out_of": 10, "source": "City Table Guide"},
    },
    # Service hours, shown in the "Visit us" section. (days label, time range).
    "hours": [
        ("Tuesday – Thursday", "5:30 – 10:30 pm"),
        ("Friday – Saturday",  "5:00 pm – midnight"),
        ("Sunday",             "5:00 – 9:30 pm"),
        ("Monday",             "Closed"),
    ],
    "phone": "(212) 227-0710",          # a New York landline for reservations
    "reservation": (
        "The room is small and it fills up. We hold a few seats for walk-ins, but do "
        "call ahead and we'll have a table waiting for you."
    ),
    # Where the room actually sits — the old Bridge Cafe corner-house in NYC.
    "location": {
        "address":       "279 Water Street",
        "cross_street":  "at Dover Street",
        "neighbourhood": "South Street Seaport",
        "city":          "New York, NY",
        "predecessor":   "the Bridge Cafe",
        "story": (
            "Le Petit Renard lives in the old Bridge Cafe — the little clapboard "
            "corner-house at 279 Water Street, tucked beneath the Brooklyn Bridge at "
            "the edge of the South Street Seaport. The wood-frame building dates to "
            "the 1790s and spent two centuries as a grocery, a saloon, and finally "
            "what was long reputed to be one of New York's oldest drinking houses — "
            "until floodwater from Hurricane Sandy shut its doors in 2012. We took "
            "the corner on, kept the creaking floors and the pressed-tin ceiling, and "
            "lit the grill again."
        ),
    },
}


# Public-facing menu copy, keyed by the same dish names as RECIPES. This is the
# customer-facing face of the recipes — names, a line of copy, a price, and a
# section — with no cooking steps. The website lists whatever the kitchen can
# actually make (RECIPES), looking each dish up here for its presentation.
MENU: dict[str, dict] = {
    "steak":  {"name": "Grilled Steak",   "section": "From the grill",
               "blurb": "A proper cut, seared over open coals and rested before it reaches the pass.",
               "price": 29},
    "burger": {"name": "Maison Burger",   "section": "From the grill",
               "blurb": "Char-grilled, finished in the oven, stacked simply. The room's signature.",
               "price": 18},
    "pasta":  {"name": "Pasta of the Day", "section": "From the range",
               "blurb": "Made to order on the range — whatever the saucier is proud of that night.",
               "price": 17},
    "soup":   {"name": "Soup of the Day",  "section": "From the range",
               "blurb": "The day's pot, simmered slow and ladled hot.",
               "price": 9},
    "salad":  {"name": "Garden Salad",     "section": "Cold & sides",
               "blurb": "Crisp leaves from the cold station, dressed the moment you order.",
               "price": 8},
    "fries":  {"name": "Hand-Cut Fries",   "section": "Cold & sides",
               "blurb": "Twice-cooked, golden, salted at the pass.",
               "price": 6},
}

MENU_SECTIONS = ["From the grill", "From the range", "Cold & sides"]   # display order

TEAM_SIDES = ["Kitchen", "Front of house"]   # how RESTAURANT['people'] is grouped on the site


# ---------------------------------------------------------------------------
# Static configuration
# ---------------------------------------------------------------------------

Recipe = list[tuple[float, str]]    # ordered chain of (duration, station) steps


RECIPES: dict[str, Recipe] = {
    "burger": [(3, "prep"), (8, "grill"), (2, "oven"), (2, "plating")],
    "fries":  [(3, "prep"), (5, "fryer"), (1, "plating")],
    "salad":  [(4, "prep"), (2, "prep"), (1, "plating")],
    "steak":  [(2, "prep"), (12, "grill"), (5, "waiting"), (2, "plating")],
    "pasta":  [(8, "stove"), (6, "stove"), (2, "stove"), (1, "plating")],
    "soup":   [(3, "prep"), (10, "stove"), (1, "plating")],
}

# "waiting" represents passive steps (resting meat, cooling) — no chef occupied.
STATION_CAPACITY: dict[str, int] = {
    "prep":    2,
    "grill":   1,
    "oven":    1,
    "fryer":   1,
    "stove":   1,
    "plating": 1,
    "waiting": 99,
}


# ---------------------------------------------------------------------------
# Runtime state — an Order -> Dish -> Step object graph, built once by the
# placer and then mutated (timing fields, station slot_free_times) as steps
# are placed.
# ---------------------------------------------------------------------------

@dataclass
class Order:
    id:      int
    arrival: float
    due:     float
    dishes:  list[Dish]      = field(default_factory=list)


@dataclass
class Dish:
    """A dish belonging to a specific order.

    `order` is a back-reference to the parent Order.
    `steps` are in execution order; step[i].prereq == step[i-1].
    """
    name:  str                                  # recipe name (key into RECIPES)
    order: Order             = field(repr=False, compare=False)
    steps: list[Step]        = field(default_factory=list)


@dataclass
class Step:
    """One cooking step belonging to a dish.

    `dish` is a back-reference to the parent Dish.
    `prereq` is the step that must finish before this one starts, or None
    if this is the dish's first step.
    A step is "placed" once the placer commits it; that's when started_at
    and finished_at get set. Before placement, both are None.
    """
    id:          str
    duration:    float
    station:     str
    dish:        Dish               = field(repr=False, compare=False)
    prereq:      Step | None        = field(default=None, repr=False, compare=False)
    started_at:  float | None       = None
    finished_at: float | None       = None


@dataclass
class Station:
    """A cooking station with finite capacity. slot_free_times[i] is the
    earliest time at which slot i becomes free; initialised to all zeros
    and updated each time a step is placed on the station.
    """
    capacity:        int
    slot_free_times: list[float] = field(default_factory=list)


@dataclass
class State:
    stations: dict[str, Station]    # keyed by station name
    orders:   list[Order]


# ---------------------------------------------------------------------------
# Helper available to heuristics
# ---------------------------------------------------------------------------

def earliest_start(step: Step, state: State) -> float:
    """Earliest time `step` could start if placed next, given placements so far.

      = max(prereq finish_time (0 if no prereq),
            earliest free slot on its station,
            order arrival time)
    """
    prereq_finish = 0.0
    if step.prereq is not None and step.prereq.finished_at is not None:
        prereq_finish = step.prereq.finished_at
    station = state.stations[step.station]
    station_free = min(station.slot_free_times) if station.slot_free_times else 0.0
    return max(prereq_finish, station_free, step.dish.order.arrival)
