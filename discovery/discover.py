"""Discovery loop: ask an LLM for a scheduling heuristic, run it through
the simulator, feed total_lateness back, repeat.

Each iteration is persisted under heuristics/discovered/ (one .py file
plus a row in runs.csv).

Usage
-----
    export HF_TOKEN=hf_xxx                       # or set it in .env
    python -m discovery.discover                 # 5 iterations on TRAINING (HF default)

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
from heuristics.discovered        import RUNS_CSV, save_iteration
from util.infra                   import ScheduleEntry
from discovery.placer             import PriorityFn, construct, init_state
from discovery.prompts            import (
    SYSTEM, describe_prompt, extract_code, initial_prompt, parse_description, refine_prompt,
)
from discovery.runtime            import compile_priority, time_limit

load_dotenv()


MODEL          = "openai/gpt-oss-120b"  # default: Hugging Face Inference
ITERATIONS     = 10
SCENARIOS      = TRAINING_BATTERY   # heuristics are scored on the mean across these
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


def evaluate_battery(priority_fn: PriorityFn) -> tuple[float, list[ScheduleEntry]]:
    """Battle-test a heuristic across the whole training battery: returns the
    MEAN total_lateness over all scenarios (so a rule can't just overfit one
    layout), plus the schedule on the FIRST scenario — which the dashboard
    always draws as the Gantt, keeping that diagram consistent."""
    total, first_schedule = 0.0, None
    for i, sc in enumerate(SCENARIOS):
        schedule = build_schedule(priority_fn, sc)
        total += evaluate(schedule, sc.orders)
        if i == 0:
            first_schedule = schedule
    return total / len(SCENARIOS), first_schedule


def discover(model: str = MODEL, base_url: str | None = None,
             iterations: int = ITERATIONS) -> None:
    client = build_client(model, base_url)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    where  = base_url if base_url else "Hugging Face"
    print(f"discovering with model={model} via {where} ({iterations} iterations)")

    history = [{"role": "system", "content": SYSTEM}]
    prompt  = initial_prompt(GANTT_SCENARIO)

    best_value: float | None = None
    best_code:  str   | None = None
    best_iter:  int   | None = None
    prev_value, prev_error = None, None

    for it in range(1, iterations + 1):
        print(f"\n=== iteration {it} ===")

        # provenance: this proposal is shaped by the previous iteration and the
        # best-so-far at this point (captured BEFORE we update best below).
        parent_best_iter = best_iter

        if it > 1:
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
        try:
            with time_limit(EVAL_TIMEOUT_S):
                fn              = compile_priority(code)
                value, schedule = evaluate_battery(fn)   # mean over the battery; schedule = scenario 0
        except Exception as exc:
            prev_error  = traceback.format_exc(limit=3)
            error_class = type(exc).__name__
            print(f"--- failed ---\n{prev_error}")
        else:
            prev_value = value
            print(f"--- total_lateness = {value:.1f}")
            if best_value is None or value < best_value:
                best_value, best_code, best_iter = value, code, it
                print(f"--- new best (iter {it}) ---")

        # Capture the schedule so the dashboard can draw a Gantt without ever
        # executing the heuristic itself (successful iterations only).
        schedule_data = None
        if schedule is not None and value is not None:
            # map each step id to its dish name from the materialized graph
            # (orders are OrderSpecs whose `dishes` are just name strings)
            _state = init_state(GANTT_SCENARIO.orders, GANTT_SCENARIO.kitchen)
            dish_name = {s.id: s.dish.name
                         for o in _state.orders for d in o.dishes for s in d.steps}
            schedule_data = {
                "horizon": max((e.end for e in schedule), default=0),
                "orders":  [{"id": o.id, "arrival": o.arrival, "due": o.due}
                            for o in GANTT_SCENARIO.orders],
                "entries": [{"step": e.step, "station": e.station,
                             "start": e.start, "end": e.end,
                             "dish_name": dish_name.get(e.step, "")} for e in schedule],
            }

        parents = []
        if it > 1:
            parents.append(f"{run_id}|{it - 1}")          # the previous iteration
        if parent_best_iter is not None:
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
        )

    print("\n=== best heuristic ===")
    if best_code is None:
        print(f"no successful heuristic discovered (run_id={run_id})")
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
    args = parser.parse_args()
    discover(model=args.model, base_url=normalize_api(args.api), iterations=args.iterations)


if __name__ == "__main__":
    main()
