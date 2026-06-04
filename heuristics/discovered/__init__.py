"""LLM-discovered priority heuristics. One .py file per iteration, plus a row
per iteration appended to runs.csv. Successful iterations expose a
`priority(task, state)` function and are importable like the baselines;
failed iterations are still saved (so students can inspect what went wrong)
and may not be importable.

`save_iteration` is the persistence entry-point called by the discovery loop.
"""

import csv
import json
from datetime import datetime
from pathlib import Path


HERE       = Path(__file__).parent
RUNS_CSV   = HERE / "runs.csv"
CSV_FIELDS = [
    "n", "timestamp", "run_id", "scenario", "model", "iter",
    "total_lateness", "status", "file",
    "title", "summary", "parents", "pivot",
]


def _next_index() -> int:
    """The next global experiment number, using runs.csv as the source of
    truth: max existing `n` + 1 (falls back to the row count for older files
    written before `n` existed, or 1 when there's no file yet).

    This is a persisted, monotonic counter across all runs — unlike the
    per-run `iter`, which restarts at 1 each run."""
    if not RUNS_CSV.exists():
        return 1
    with RUNS_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))
    highest = 0
    for r in rows:
        try:
            highest = max(highest, int(r.get("n") or 0))
        except ValueError:
            pass
    return (highest or len(rows)) + 1


def _code_from_py(text: str) -> str:
    """Strip a saved module's leading docstring + injected import, leaving the
    generated heuristic code."""
    t = text.lstrip()
    if t.startswith('"""'):
        end = t.find('"""', 3)
        if end != -1:
            t = t[end + 3:]
    lines = [ln for ln in t.splitlines()
             if not ln.strip().startswith("from problem_definition.model import")]
    return "\n".join(lines).strip("\n")


def find_champion() -> dict | None:
    """The best heuristic recorded so far across ALL runs (lowest total_lateness
    among successes), with its code read back from its .py. None if there's no
    prior success. Used to seed a new run — connecting it to the past."""
    if not RUNS_CSV.exists():
        return None
    with RUNS_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))
    best, best_lat = None, None
    for r in rows:
        if r.get("status") != "success":
            continue
        try:
            lat = float(r.get("total_lateness", ""))
        except (ValueError, TypeError):
            continue
        if best is None or lat < best_lat:
            best, best_lat = r, lat
    if best is None:
        return None
    code = ""
    fname = best.get("file") or ""
    if fname and (HERE / fname).exists():
        code = _code_from_py((HERE / fname).read_text())
    return {
        "key":      f'{best.get("run_id", "")}|{best.get("iter", "")}',
        "code":     code,
        "lateness": best_lat,
        "title":    best.get("title", "") or "Untitled heuristic",
    }


def save_iteration(
    run_id:         str,
    scenario:       str,
    model:          str,
    iteration:      int,
    code:           str,
    total_lateness: float | None,
    error:          str   | None,
    title:          str = "",
    summary:        str = "",
    parents:        list[str] | None = None,
    schedule:       dict | None = None,
    pivot:          bool = False,
) -> Path:
    """Save one iteration's code as a .py module and append a summary row
    to runs.csv. Returns the .py path.

    On success: pass `total_lateness=<value>` and `error=None`.
    On failure: pass `total_lateness=None` and `error=<exception class name>`.

    `title` names the rule, `summary` is the one-line kitchen instruction;
    `parents` is a list of `run_id|iter` keys it was derived from (provenance).
    `schedule`, if given, is the built schedule (orders + placed steps), saved
    as a sidecar .schedule.json so the dashboard can draw a Gantt without ever
    executing the heuristic.
    """
    HERE.mkdir(parents=True, exist_ok=True)
    n = _next_index()
    status = "success" if error is None else f"failed:{error}"
    py_path = HERE / f"run_{run_id}_iter{iteration}.py"
    parents_str = ";".join(parents or [])

    if schedule is not None:
        (HERE / f"run_{run_id}_iter{iteration}.schedule.json").write_text(json.dumps(schedule))

    lateness_line = (
        f"  total_lateness: {total_lateness:.1f}\n" if total_lateness is not None else ""
    )
    title_line   = f"  Title:          {title}\n" if title else ""
    summary_line = f"  Rule:           {summary}\n" if summary else ""
    parents_line = f"  Parents:        {parents_str}\n" if parents_str else ""
    header = (
        f'"""Discovered priority heuristic.\n\n'
        f'  Experiment:     {n}\n'
        f'  Run:            {run_id}\n'
        f'  Iteration:      {iteration}\n'
        f'  Scenario:       {scenario}\n'
        f'  Model:          {model}\n'
        f'  Status:         {status}\n'
        f'{lateness_line}'
        f'{title_line}'
        f'{summary_line}'
        f'{parents_line}'
        f'"""\n\n'
        "from problem_definition.model import State, Step, earliest_start  # noqa: F401\n\n\n"
    )
    py_path.write_text(header + code + "\n")

    write_header = not RUNS_CSV.exists()
    with RUNS_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "n":              n,
            "timestamp":      datetime.now().isoformat(timespec="seconds"),
            "run_id":         run_id,
            "scenario":       scenario,
            "model":          model,
            "iter":           iteration,
            "total_lateness": f"{total_lateness:.1f}" if total_lateness is not None else "",
            "status":         status,
            "file":           py_path.name,
            "title":          title,
            "summary":        summary,
            "parents":        parents_str,
            "pivot":          "1" if pivot else "",
        })
    return py_path
