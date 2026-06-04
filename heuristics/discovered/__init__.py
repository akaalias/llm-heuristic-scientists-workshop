"""LLM-discovered priority heuristics. One .py file per iteration, plus a row
per iteration appended to runs.csv. Successful iterations expose a
`priority(task, state)` function and are importable like the baselines;
failed iterations are still saved (so students can inspect what went wrong)
and may not be importable.

`save_iteration` is the persistence entry-point called by the discovery loop.
"""

import csv
from datetime import datetime
from pathlib import Path


HERE       = Path(__file__).parent
RUNS_CSV   = HERE / "runs.csv"
CSV_FIELDS = [
    "n", "timestamp", "run_id", "scenario", "model", "iter",
    "total_lateness", "status", "file",
    "title", "summary", "parents",
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
) -> Path:
    """Save one iteration's code as a .py module and append a summary row
    to runs.csv. Returns the .py path.

    On success: pass `total_lateness=<value>` and `error=None`.
    On failure: pass `total_lateness=None` and `error=<exception class name>`.

    `title` names the rule, `summary` is the one-line kitchen instruction;
    `parents` is a list of `run_id|iter` keys it was derived from (provenance).
    """
    HERE.mkdir(parents=True, exist_ok=True)
    n = _next_index()
    status = "success" if error is None else f"failed:{error}"
    py_path = HERE / f"run_{run_id}_iter{iteration}.py"
    parents_str = ";".join(parents or [])

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
        })
    return py_path
