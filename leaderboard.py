"""Evaluate one or more heuristic .py files on every scenario.

Usage
-----
    python leaderboard.py <path> [<path> ...] [--gantt DIR]

Each <path> is either:
  - a .py file exposing a callable named `priority` (the convention used by
    files in heuristics/discovered/) or named after the file stem (the
    convention used by baselines like heuristics/baseline/edd.py), or
  - a directory: every top-level .py file in it is evaluated (skipping
    files whose names start with '_').

For each heuristic, prints one row per scenario: total_lateness if the
schedule is valid, otherwise INVALID(<n>) or ERR:<ExceptionName>. After
the leaderboard, the winning heuristic on each scenario is rendered as a
matplotlib Gantt PNG into the gantt directory (default: ./gantt/, override
with --gantt DIR).

Examples
--------
    python leaderboard.py heuristics/baseline
    python leaderboard.py heuristics/baseline heuristics/discovered
    python leaderboard.py heuristics/baseline --gantt out/charts
    python leaderboard.py heuristics/discovered/run_20260531_125241_iter1.py
"""

import argparse
import importlib.util
import sys
from pathlib import Path

from problem_definition.check import check
from problem_definition.evaluate import evaluate
from problem_definition.scenarios import ALL_SCENARIOS
from problem_definition.step_id import parse_step_id
from discovery.placer import PriorityFn, construct
from util.infra import ScheduleEntry


def collect_files(args: list[str]) -> list[Path]:
    files: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            files.extend(sorted(c for c in p.glob("*.py") if not c.name.startswith("_")))
        elif p.is_file() and p.suffix == ".py":
            files.append(p)
        else:
            print(f"[skip] not found or not a .py: {arg}")
    return files


def load_priority(path: Path) -> PriorityFn | None:
    """Import the file as a module and return its priority function.

    Preference order: a callable named `priority`, then a callable named
    after the file stem (handles baselines like edd.py exporting `edd`).
    """
    spec = importlib.util.spec_from_file_location(f"_heuristic_{path.stem}", path)
    if spec is None or spec.loader is None:
        print(f"[skip] {path.name}: cannot build import spec")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        print(f"[skip] {path.name}: import failed ({type(exc).__name__}: {exc})")
        return None
    for attr in ("priority", path.stem):
        fn = getattr(module, attr, None)
        if callable(fn):
            return fn
    print(f"[skip] {path.name}: no callable `priority` or `{path.stem}` found")
    return None


def score(fn: PriorityFn) -> dict[str, str]:
    """Run fn on every scenario; return {scenario_name: cell_text}."""
    out: dict[str, str] = {}
    for sc in ALL_SCENARIOS:
        try:
            schedule = construct(sc.orders, sc.kitchen, fn)
            violations = check(schedule, sc.orders, sc.kitchen)
            if violations:
                out[sc.name] = f"INVALID({len(violations)})"
            else:
                out[sc.name] = f"{evaluate(schedule, sc.orders):.1f}"
        except Exception as exc:
            out[sc.name] = f"ERR:{type(exc).__name__}"
    return out


def _pack_into_slots(entries: list[ScheduleEntry]) -> tuple[dict[int, int], int]:
    """First-fit pack: each entry gets the lowest-index slot whose previous
    occupant has finished by then. Returns (slot_of_entry, n_slots)."""
    slot_free: list[float] = []
    slot_of: dict[int, int] = {}
    for e in sorted(entries, key=lambda x: x.start):
        for i, free in enumerate(slot_free):
            if free <= e.start + 1e-9:
                slot_of[id(e)] = i
                slot_free[i] = e.end
                break
        else:
            slot_of[id(e)] = len(slot_free)
            slot_free.append(e.end)
    return slot_of, len(slot_free)


def save_gantt_png(schedule, scenario, heuristic_name: str, score: float, out_path: Path) -> None:
    """Render `schedule` as a matplotlib Gantt and save to `out_path`."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    stations = sorted({e.station for e in schedule})
    rows: list[tuple[str, list[ScheduleEntry]]] = []
    for station in stations:
        st_entries = [e for e in schedule if e.station == station]
        slot_of, n_slots = _pack_into_slots(st_entries)
        for slot in range(n_slots):
            label = f"{station} #{slot}" if n_slots > 1 else station
            rows.append((label, [e for e in st_entries if slot_of[id(e)] == slot]))

    order_ids = sorted({parse_step_id(e.step)[0] for e in schedule})
    cmap = plt.colormaps["tab10" if len(order_ids) <= 10 else "tab20"]
    color_of = {oid: cmap(i % cmap.N) for i, oid in enumerate(order_ids)}

    horizon = max(e.end for e in schedule)
    fig, ax = plt.subplots(figsize=(max(8, horizon * 0.25), max(3.5, len(rows) * 0.35)))

    for y, (_, st_entries) in enumerate(rows):
        for e in st_entries:
            oid, dish_idx, _ = parse_step_id(e.step)
            ax.barh(y, e.end - e.start, left=e.start, height=0.7,
                    color=color_of[oid], edgecolor="black", linewidth=0.6)
            if e.end - e.start >= 1.5:
                ax.text(e.start + (e.end - e.start) / 2, y, str(dish_idx + 1),
                        ha="center", va="center", fontsize=9)

    # Arrival (dotted) and due (dashed) for each order, in the order's color.
    for o in scenario.orders:
        if o.id in color_of:
            ax.axvline(x=o.arrival, color=color_of[o.id], linestyle=":",  alpha=0.4)
            ax.axvline(x=o.due,     color=color_of[o.id], linestyle="--", alpha=0.6)

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlabel("time")
    ax.set_title(f"{scenario.name}: {heuristic_name} (total_lateness = {score:.1f})")
    ax.grid(True, axis="x", linestyle=":", alpha=0.4)
    ax.set_xlim(0, horizon * 1.02)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def winners(rows: list[tuple[str, PriorityFn, dict[str, str]]]):
    """Yield (scenario, heuristic_name, priority_fn, score) for the winner
    on each scenario (lowest numeric total_lateness). Skips scenarios where
    no heuristic produced a numeric result."""
    for sc in ALL_SCENARIOS:
        best: tuple[float, str, PriorityFn] | None = None
        for name, fn, cells in rows:
            try:
                value = float(cells[sc.name])
            except ValueError:
                continue
            if best is None or value < best[0]:
                best = (value, name, fn)
        if best is None:
            continue
        value, name, fn = best
        yield sc, name, fn, value


def save_winners_png(rows: list[tuple[str, PriorityFn, dict[str, str]]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for sc, name, fn, value in winners(rows):
        schedule = construct(sc.orders, sc.kitchen, fn)
        out_path = out_dir / f"gantt_{sc.name}_{name}.png"
        save_gantt_png(schedule, sc, name, value, out_path)
        print(f"wrote {out_path}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate heuristic .py files on every scenario.",
    )
    parser.add_argument(
        "paths", nargs="+",
        help=".py files or directories containing heuristic .py files",
    )
    parser.add_argument(
        "--gantt", nargs="?", const="gantt", default=None, metavar="DIR",
        help="save a PNG Gantt of the winner per scenario into DIR (default: ./gantt/)",
    )
    args = parser.parse_args(argv)

    files = collect_files(args.paths)
    if not files:
        print("no files to evaluate")
        return 1

    loaded: list[tuple[str, PriorityFn, dict[str, str]]] = []
    for path in files:
        fn = load_priority(path)
        if fn is not None:
            loaded.append((path.stem, fn, score(fn)))

    if not loaded:
        print("no heuristics loaded")
        return 1

    scenarios = [sc.name for sc in ALL_SCENARIOS]
    # Sort ascending by the first scenario's score; non-numeric cells
    # (ERR / INVALID) sink to the bottom.
    def sort_key(item: tuple[str, PriorityFn, dict[str, str]]) -> tuple[int, float]:
        cell = item[2].get(scenarios[0], "")
        try:
            return (0, float(cell))
        except ValueError:
            return (1, 0.0)
    loaded.sort(key=sort_key)

    name_w = max(len("heuristic"), max(len(name) for name, _, _ in loaded))
    col_w  = max(15, max(len(s) for s in scenarios))
    header = f"{'heuristic':<{name_w}}  " + "  ".join(f"{s:>{col_w}}" for s in scenarios)
    print()
    print(header)
    print("-" * len(header))
    for name, _, cells in loaded:
        row = "  ".join(f"{cells.get(s, '-'): >{col_w}}" for s in scenarios)
        print(f"{name:<{name_w}}  {row}")

    if args.gantt is not None:
        save_winners_png(loaded, Path(args.gantt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
