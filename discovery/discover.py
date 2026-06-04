"""Discovery loop: ask an LLM for a scheduling heuristic, run it through
the simulator, feed total_lateness back, repeat.

Each iteration is persisted under heuristics/discovered/ (one .py file
plus a row in runs.csv).

Usage
-----
    export HF_TOKEN=hf_xxx                       # or set it in .env
    python -m discovery.discover                 # default iterations on TRAINING (HF)
    python -m discovery.discover --iterations 30 --patience 6

    # against a local LM Studio / OpenAI-compatible server:
    python -m discovery.discover \
        --api http://192.168.2.152:1234 \
        --model openai/gpt-oss-20b
"""

import argparse
import os
import traceback
from datetime import datetime

from dotenv import load_dotenv
from huggingface_hub import InferenceClient

from problem_definition.check     import check
from problem_definition.evaluate  import evaluate
from problem_definition.scenarios import TRAINING_BATTERY
from heuristics.discovered        import RUNS_CSV, find_champion, prior_attempts, save_iteration
from util.infra                   import ScheduleEntry
from discovery.placer             import PriorityFn, construct, init_state
from discovery.prompts            import (
    SYSTEM, breakout_prompt, describe_prompt, extract_code, hard_breakout_prompt,
    initial_prompt, parse_description, refine_prompt, reproduce_prompt,
)
from discovery.runtime            import compile_priority, time_limit

load_dotenv()


MODEL          = "openai/gpt-oss-120b"  # default: Hugging Face Inference
ITERATIONS     = 10
PLATEAU_PATIENCE = 3    # iterations with no improvement → ask for a new approach
META_PLATEAU_PIVOTS = 2 # consecutive pivots with no global improvement → hard breakout
SCENARIOS      = TRAINING_BATTERY   # heuristics are scored on the mean across these
SCEN_BY_NAME   = {sc.name: sc for sc in SCENARIOS}  # name → scenario, for bottleneck lookup
GANTT_SCENARIO = SCENARIOS[0]       # always the first — keeps the diagram consistent
EVAL_TIMEOUT_S = 5     # bound buggy priority() so it can't hang the workshop
MAX_TOKENS     = 1500  # cap on assistant reply length per iteration


def build_client(model: str, base_url: str | None) -> InferenceClient:
    """Return an InferenceClient for either Hugging Face (default) or a local
    OpenAI-compatible server (when `base_url` is given, e.g. LM Studio).

    HF path uses HF_TOKEN. Local path uses LLM_API_KEY if set, else a dummy
    key (LM Studio ignores it) — HF_TOKEN is not required when going local."""
    if base_url:
        return InferenceClient(base_url=base_url, api_key=os.environ.get("LLM_API_KEY", "lm-studio"))
    return InferenceClient(model=model, token=os.environ["HF_TOKEN"])


def normalize_api(url: str | None) -> str | None:
    """LM Studio (and most OpenAI-compatible servers) serve the API under
    `/v1`. Accept a bare host:port and append `/v1` if it's not already there,
    so `--api http://host:1234` and `--api http://host:1234/v1` both work."""
    if not url:
        return None
    url = url.rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def describe(client: InferenceClient, model: str, code: str) -> tuple[str, str]:
    """One-off call naming a heuristic and distilling it into a memorable
    kitchen instruction → (title, rule). Kept out of the refinement history so
    it can't steer the next proposal. Never raises — a failed description must
    not abort the run."""
    try:
        reply = client.chat_completion(
            messages=[
                {"role": "system", "content": "You turn kitchen-scheduling heuristics into memorable rules of thumb a line cook could follow."},
                {"role": "user",   "content": describe_prompt(code)},
            ],
            model=model, max_tokens=MAX_TOKENS,
        ).choices[0].message.content
        return parse_description(reply)
    except Exception as exc:
        return "Untitled heuristic", f"(rule unavailable: {type(exc).__name__})"


def build_schedule(priority_fn: PriorityFn, scenario) -> list[ScheduleEntry]:
    """Run the placer for `scenario`; verify the result is valid."""
    schedule   = construct(scenario.orders, scenario.kitchen, priority_fn)
    violations = check(schedule, scenario.orders, scenario.kitchen)
    if violations:
        raise ValueError(f"schedule violates constraints: {violations[:3]}")
    return schedule


def evaluate_battery(priority_fn: PriorityFn) -> tuple[float, list[dict]]:
    """Battle-test a heuristic across the whole training battery. Returns the
    MEAN total_lateness (so a rule can't just overfit one layout) and a
    serialised schedule per sample — each with its own orders, placed steps,
    dish names, and per-sample lateness — for the dashboard to draw. The first
    sample (TRAINING) is the one the detail-view Gantt uses."""
    total = 0.0
    samples: list[dict] = []
    for sc in SCENARIOS:
        schedule = build_schedule(priority_fn, sc)
        lat = evaluate(schedule, sc.orders)
        total += lat
        st = init_state(sc.orders, sc.kitchen)
        dish_name = {s.id: s.dish.name for o in st.orders for d in o.dishes for s in d.steps}
        samples.append({
            "name":     sc.name,
            "lateness": round(lat, 1),
            "horizon":  max((e.end for e in schedule), default=0),
            "orders":   [{"id": o.id, "arrival": o.arrival, "due": o.due} for o in sc.orders],
            "entries":  [{"step": e.step, "station": e.station, "start": e.start, "end": e.end,
                          "dish_name": dish_name.get(e.step, "")} for e in schedule],
        })
    return total / len(SCENARIOS), samples


def discover(model: str = MODEL, base_url: str | None = None,
             iterations: int = ITERATIONS, patience: int = PLATEAU_PATIENCE,
             meta_pivots: int = META_PLATEAU_PIVOTS) -> None:
    client = build_client(model, base_url)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    where  = base_url if base_url else "Hugging Face"
    print(f"discovering with model={model} via {where} ({iterations} iterations, "
          f"patience={patience}, meta-pivots={meta_pivots})")

    best_value:   float | None = None
    best_code:    str   | None = None
    best_iter:    int   | None = None        # which iteration holds the best (None = carried-over champion)
    best_samples: list[dict] | None = None   # per-scenario schedules of the current best

    # connect to the past: carry over the best heuristic found across earlier
    # runs. ELITISM — re-evaluate it on the CURRENT battery and adopt it as the
    # run's standing best, so the run can never end worse than what we already
    # had, and the plateau detector measures against the true bar to beat.
    champion = find_champion()
    tried: list[dict] = []
    if champion and champion["code"]:
        tried = prior_attempts(exclude_key=champion["key"])  # everything else we've tested
        try:
            with time_limit(EVAL_TIMEOUT_S):
                champ_value, champ_samples = evaluate_battery(compile_priority(champion["code"]))
            best_value, best_code, best_samples = champ_value, champion["code"], champ_samples
            print(f"carrying over champion {champion['key']} ('{champion['title']}'), "
                  f"re-scored {champ_value:.1f} on the current battery — retained as the bar to beat; "
                  f"{len(tried)} prior approach(es) catalogued for pivots")
        except Exception as exc:
            print(f"champion {champion['key']} no longer evaluates on this battery "
                  f"({type(exc).__name__}) — starting cold")
            champion = None

    history = [{"role": "system", "content": SYSTEM}]
    prompt  = (reproduce_prompt(GANTT_SCENARIO, champion)
               if (champion and champion["code"]) else initial_prompt(GANTT_SCENARIO))

    prev_value, prev_error = None, None
    since_improve = 0          # iterations since the best last moved (plateau detector)
    pivots_since_best = 0      # consecutive pivots since the best last moved (meta-plateau)
    branch_best_iter:  int   | None = None   # best of the CURRENT direction (reset at each pivot)
    branch_best_value: float | None = None

    for it in range(1, iterations + 1):
        print(f"\n=== iteration {it} ===")

        # provenance: a proposal builds on the previous iteration and the best of
        # the CURRENT direction (reset at each pivot) — so after a pivot it
        # builds on the new branch, not the old champion we deliberately left.
        parent_best_iter = branch_best_iter

        pivot = False
        if it > 1:
            if since_improve >= patience:
                # stuck in a dead end — keep the plateaued attempt as a parent
                # (history is retained) but ask for a fundamentally new approach
                pivot = True             # this experiment is a deliberate change of direction
                if pivots_since_best >= meta_pivots and best_samples:
                    # META-plateau: we've already pivoted repeatedly and the global
                    # best never moved — different signals keep collapsing to the same
                    # schedule. Stop inventing signals; aim at the bottleneck scenario.
                    per = [(s["name"], s["lateness"]) for s in best_samples]
                    bname  = max(per, key=lambda x: x[1])[0]
                    bscen  = SCEN_BY_NAME.get(bname, GANTT_SCENARIO)
                    print(f"--- meta-plateau: best {best_value:.1f} unchanged across "
                          f"{pivots_since_best} pivots → HARD breakout on '{bname}' ---")
                    prompt = hard_breakout_prompt(bscen, best_value, per)
                else:
                    print(f"--- plateau: {since_improve} iterations without improvement → new approach ---")
                    prompt = breakout_prompt(GANTT_SCENARIO, best_value, since_improve, tried)
                pivots_since_best += 1  # count this pivot toward the meta-plateau detector
                since_improve = 0      # give the new direction a fresh patience window
                branch_best_iter, branch_best_value = None, None  # fresh lineage — drop the old branch
            else:
                prompt = refine_prompt(GANTT_SCENARIO, prev_value, prev_error, best_value)

        history.append({"role": "user", "content": prompt})
        reply = client.chat_completion(
            messages=history, model=model, max_tokens=MAX_TOKENS
        ).choices[0].message.content
        history.append({"role": "assistant", "content": reply})

        code = extract_code(reply)
        print("--- proposed heuristic ---")
        print(code)

        prev_value, prev_error = None, None
        value:       float | None = None
        error_class: str   | None = None
        schedule = None
        improved = False
        samples = None
        try:
            with time_limit(EVAL_TIMEOUT_S):
                fn             = compile_priority(code)
                value, samples = evaluate_battery(fn)   # mean over the battery + per-sample schedules
        except Exception as exc:
            prev_error  = traceback.format_exc(limit=3)
            error_class = type(exc).__name__
            print(f"--- failed ---\n{prev_error}")
        else:
            prev_value = value
            print(f"--- total_lateness = {value:.1f}")
            if best_value is None or value < best_value:
                best_value, best_code, best_iter = value, code, it
                best_samples = samples           # remember the schedule that achieves it
                improved = True
                print(f"--- new best (iter {it}) ---")
            if branch_best_value is None or value < branch_best_value:
                branch_best_value, branch_best_iter = value, it   # best of the current direction

        since_improve = 0 if improved else since_improve + 1
        if improved:
            pivots_since_best = 0   # the global best moved — reset the meta-plateau counter

        # Persist the per-sample schedules so the dashboard can draw Gantts
        # without ever executing the heuristic (successful iterations only).
        schedule_data = {"samples": samples} if (samples and value is not None) else None

        parents = []
        if it > 1:
            parents.append(f"{run_id}|{it - 1}")          # the previous (plateaued) iteration
        elif champion is not None:
            parents.append(champion["key"])               # run 1 connects to the past champion
        # normal iterations also build on the best-so-far; a PIVOT deliberately
        # drops that anchor — it should bring a fresh idea, not lean on the old
        # champion — so it keeps only the most recent parent.
        if parent_best_iter is not None and not pivot:
            bkey = f"{run_id}|{parent_best_iter}"          # the best-so-far it built on
            if bkey not in parents:
                parents.append(bkey)

        title, summary = describe(client, model, code)
        print(f"--- {title} ---\n{summary}")

        save_iteration(
            run_id         = run_id,
            scenario       = GANTT_SCENARIO.name,
            model          = model,
            iteration      = it,
            code           = code,
            total_lateness = value,
            error          = error_class,
            title          = title,
            summary        = summary,
            parents        = parents,
            schedule       = schedule_data,
            pivot          = pivot,
        )

    print("\n=== best heuristic ===")
    if best_code is None:
        print(f"no successful heuristic discovered (run_id={run_id})")
    elif best_iter is None:
        print(f"the carried-over champion ({champion['key']}, total_lateness = "
              f"{best_value:.1f}) was not beaten this run")
        print(best_code)
    else:
        print(f"from iteration {best_iter}, total_lateness = {best_value:.1f}")
        print(best_code)
    print(f"\nrun_id={run_id}, see {RUNS_CSV}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover scheduling heuristics with an LLM (HF by default, "
                    "or a local OpenAI-compatible server via --api)."
    )
    parser.add_argument(
        "--api", metavar="URL", default=None,
        help="Base URL of a local/OpenAI-compatible server (e.g. LM Studio at "
             "http://192.168.2.152:1234). '/v1' is appended if omitted. "
             "When unset, uses Hugging Face Inference with HF_TOKEN.",
    )
    parser.add_argument(
        "--model", default=MODEL,
        help=f"Model id to request (default: {MODEL}). For a local server, use "
             "the model id shown in LM Studio, e.g. openai/gpt-oss-20b.",
    )
    parser.add_argument(
        "--iterations", type=int, default=ITERATIONS,
        help=f"number of refinement iterations to run (default: {ITERATIONS})",
    )
    parser.add_argument(
        "--patience", type=int, default=PLATEAU_PATIENCE,
        help=f"iterations with no improvement before pivoting to a new approach "
             f"(default: {PLATEAU_PATIENCE})",
    )
    parser.add_argument(
        "--meta-pivots", type=int, default=META_PLATEAU_PIVOTS,
        help=f"consecutive pivots with no global improvement before the hard "
             f"breakout that targets the bottleneck scenario (default: {META_PLATEAU_PIVOTS})",
    )
    args = parser.parse_args()
    discover(model=args.model, base_url=normalize_api(args.api),
             iterations=args.iterations, patience=args.patience,
             meta_pivots=args.meta_pivots)


if __name__ == "__main__":
    main()
