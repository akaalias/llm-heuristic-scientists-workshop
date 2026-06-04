# Bliss — LLM Heuristic Scientists Workshop

A tiny restaurant-kitchen scheduling problem. Your job is to discover good
priority heuristics — by hand, by prompting an LLM, or both — and beat the
baselines on a held-out scenario.

The problem: orders arrive over time, each containing dishes, each a chain
of steps that compete for limited station capacity. The objective is to
minimize **total_lateness** = sum of `max(0, finish - due)` per order.

A heuristic is a single Python function:

```python
def priority(step, state) -> float:
    ...
```

The placer greedily picks the highest-priority eligible step at each tick.
See `problem_definition/model.py` and `discovery/placer.py` for the full contract.

### How the placer uses it

At each step, the placer calls `priority` on every currently-eligible step
(one whose `prereq` is `None` or already placed), picks the maximum, and
commits it to its station at the earliest feasible time:

```python
# placer.py, paraphrased
while unplaced:
    runnable = eligible_steps(state)
    best = max(runnable, key=lambda s: (
        priority_fn(s, state),            # your heuristic
        -earliest_start(s, state),        # built-in tie-break
    ))
    place(state, best)                    # mutates state
```

Two consequences:

- **Your function gets called a lot** (≈ `len(runnable) × total_steps`
  times per schedule), so keep it cheap — avoid sorting/iterating the
  whole graph if a constant-time view will do.
- **Ties are broken by earliest possible start time** (lower → sooner)
  before any random ordering, so you never need to add jitter for
  determinism.

### What your `priority(step, state)` sees

The object graph is `Order -> dishes -> Dish -> steps -> Step`, with
back-references so you can navigate it in any direction.

`step: Step` — the candidate step being scored:

| field         | meaning                                                       |
|---------------|---------------------------------------------------------------|
| `id`          | unique string id (`"o1.d0.s2"`)                               |
| `duration`    | how long it takes                                             |
| `station`     | which station it needs (`"grill"`, `"prep"`, ...)             |
| `dish`        | back-ref to its `Dish` (and from there `step.dish.order`)     |
| `prereq`      | the step that must finish first, or `None` for the first step |
| `started_at`  | start time once placed, else `None`                           |
| `finished_at` | end time once placed, else `None`                             |

`state: State` — everything placed so far:

| field      | meaning                                                          |
|------------|------------------------------------------------------------------|
| `stations` | dict `name -> Station(capacity, slot_free_times: list[float])`   |
| `orders`   | every `Order` (with its `dishes` and `steps`, placed or not)     |

`Order` has `id`, `arrival`, `due`, `dishes`. Walk
`state.orders -> o.dishes -> d.steps` for global views (e.g. remaining
work per order, congestion on a station).

A helper is in scope:

```python
earliest_start(step, state) -> float
```

returns `max(prereq finish, earliest free slot on the station, order arrival)`
— the soonest this step *could* start if you placed it next.

Contract: treat both arguments as read-only — the placer mutates them
between calls. Higher return value = place this step sooner.

## Setup

```bash
uv sync                          # installs huggingface-hub, matplotlib, python-dotenv
echo "HF_TOKEN=hf_xxx" > .env    # your Hugging Face inference token
```

The `HF_TOKEN` is only needed for the default (Hugging Face) path. To run
entirely against a local model instead, skip it and see
[Using a local LLM (LM Studio)](#using-a-local-llm-lm-studio).

## 1. Look at the baselines

```bash
.venv/bin/python leaderboard.py heuristics/baseline
```

Runs `random`, `EDD`, `SPT`, `LPT`, `least_slack` on every scenario
(`TRAINING`, `HIDDEN_TEST`, `STRESS`) and prints a sorted leaderboard.

## 2. Discover heuristics with an LLM

```bash
.venv/bin/python -m discovery.discover
```

Runs `ITERATIONS = 5` rounds against `MODEL = openai/gpt-oss-120b` on the
`TRAINING` scenario. Each iteration:

1. asks the model for a `priority` function,
2. compiles & runs it through the placer (bounded by `EVAL_TIMEOUT_S`),
3. feeds the score back, asks for a refinement.

Knobs are module-level constants at the top of `discovery/discover.py`
(`MODEL`, `ITERATIONS`, `SCENARIO`, `EVAL_TIMEOUT_S`, `MAX_TOKENS`). The
model and endpoint can also be overridden per-run from the CLI (see below).

Each iteration's code is saved to `heuristics/discovered/run_<ts>_iter<N>.py`
(success or failure) and one row is appended to
`heuristics/discovered/runs.csv`.

### Using a local LLM (LM Studio)

You can run discovery against a model on your own machine instead of Hugging
Face — no token, no rate limits, no per-call cost. The default behavior is
unchanged; local mode is fully opt-in via two flags.

**1. Install LM Studio.** Download it from <https://lmstudio.ai> and install
(macOS / Windows / Linux).

**2. Get a model.** In LM Studio's **Search** tab, download a model that fits
your machine — e.g. `openai/gpt-oss-20b`. Larger models give better
heuristics but need more RAM/VRAM.

**3. Start the local server.** Open the **Developer** (or **Local Server**)
tab, load the model, and click **Start Server**. LM Studio exposes an
OpenAI-compatible API, by default at `http://localhost:1234` (serving the API
under `/v1`). To reach it from another machine on your LAN, enable "Serve on
local network" and note the host's IP (e.g. `192.168.2.152`).

**4. Run discovery against it.** Pass the server URL and the model id shown in
LM Studio:

```bash
.venv/bin/python -m discovery.discover \
    --api http://192.168.2.152:1234 \
    --model openai/gpt-oss-20b
```

- `--api` is the server base URL. A trailing `/v1` is appended automatically,
  so `http://host:1234` and `http://host:1234/v1` both work. Use
  `http://localhost:1234` if LM Studio runs on the same machine.
- `--model` is the model id exactly as LM Studio lists it.
- `HF_TOKEN` is **not** required in local mode. If your server needs an API
  key, set `LLM_API_KEY` in the environment (LM Studio ignores it).

Without `--api`, discovery uses Hugging Face Inference with `MODEL` and
`HF_TOKEN` exactly as before.

## 3. Evaluate any heuristics on every scenario

```bash
# all baselines + every discovered heuristic
.venv/bin/python leaderboard.py heuristics/baseline heuristics/discovered

# a specific file
.venv/bin/python leaderboard.py heuristics/discovered/run_20260531_125241_iter1.py

# a directory
.venv/bin/python leaderboard.py heuristics/discovered
```

Accepts any mix of `.py` files and directories. Each file must expose
either `priority` (the discovery convention) or a callable named after the
file stem (the baseline convention, e.g. `edd.py` → `edd`). Output is
sorted ascending by the first scenario; `ERR` / `INVALID` rows sink to
the bottom.

### Inspect the winning schedule

Add `--gantt [DIR]` to save a colored PNG Gantt of the winner per
scenario (default dir: `gantt/`).

```bash
.venv/bin/python leaderboard.py heuristics/baseline --gantt
.venv/bin/python leaderboard.py heuristics/baseline --gantt figs/
```

In the rendered Gantt: color = order, in-bar digit = dish index within
that order (1-based), dotted vertical line = order arrival, dashed
vertical line = order due. Multi-capacity stations (e.g. `prep`) split
into `prep #0`, `prep #1`, …

## Repo layout

```
leaderboard.py           # score heuristics on every scenario (+ PNG Gantt)
discovery/
    discover.py          # the LLM discovery loop
    prompts.py           # system prompt + refinement prompt
    placer.py            # greedy list-placer driven by a priority fn
    runtime.py           # time_limit + compile_priority (exec'd LLM code)
problem_definition/
    model.py             # Order, Dish, Step, State, earliest_start
    evaluate.py          # total_lateness scoring
    check.py             # constraint validator
    scenarios.py         # TRAINING, HIDDEN_TEST, STRESS
util/infra.py            # OrderSpec, Scenario, ScheduleEntry
heuristics/baseline/     # random, EDD, SPT, LPT, least_slack
heuristics/discovered/   # LLM-authored heuristics + runs.csv (gitignored)
gantt/                   # generated PNG Gantts (gitignored)
```

## Writing your own heuristic

Drop a file under `heuristics/baseline/` (or anywhere) with this shape:

```python
from problem_definition.model import State, Step, earliest_start


def my_heuristic(step: Step, state: State) -> float:
    # higher = place this step next
    return -earliest_start(step, state)
```

Then evaluate it:

```bash
.venv/bin/python leaderboard.py path/to/my_heuristic.py
```

## Tips

- The discovery loop trains on `TRAINING` only. Use `leaderboard.py`
  to check generalization to `HIDDEN_TEST` and `STRESS`.
- `STRESS` is grill-heavy by design — mention bottleneck stations in
  your prompt if you want the LLM to attack it.
