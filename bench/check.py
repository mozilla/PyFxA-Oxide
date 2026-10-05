"""Decide whether the PR passes the benchmark check.

Usage: check.py OUT_DIR THRESHOLD [NOISE_WARN] 

Reads, for each run N of each side ("baseline", "pr"): {side}-N.json
(pytest-benchmark results), {side}-N.exit (pytest's exit code) and pr-N.xml
(pytest --junitxml). Every matching file in OUT_DIR is read, so it should hold
only the runs being compared.

Fails if a PR median (the median of its per-run medians) is more than
THRESHOLD slower than the base's, a benchmark fails in any PR run, any run
exits with an error (skips are fine), or there are no PR results. Times are
per operation: batched benchmarks set extra_info["operations_per_round"].

Warns, without failing, about benchmarks whose runs on one side differ by more
than NOISE_WARN, ignoring the fastest and slowest run (as the median does):
their result is less reliable. This needs at least 4 runs per side.

Writes OUT_DIR/result.json (the facts and the verdict, for report.py), prints
the failures, and exits 0 (pass) or 1 (fail).
"""

import json
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


SIDES = ("baseline", "pr")


class Status(Enum):
    OK = "ok"
    SLOWER = "slower"   # more than the threshold slower than the base
    FAILED = "failed"   # failed or errored in a PR run
    NEW = "new"         # no baseline to compare with


@dataclass
class Row:
    """One benchmark: medians in seconds per operation (None if missing)."""

    name: str
    base: float | None
    pr: float | None
    pct: float | None   # % change, PR vs base; None if not compared
    status: Status


@dataclass
class Result:
    limit: float                                    # allowed slowdown, %
    rows: list[Row]
    exit_codes: dict[str, dict[str, int | None]]    # side -> run -> pytest exit code
    noise_limit: float | None = None                # warn above this spread, %
    noisy: dict[str, dict[str, float]] = field(default_factory=dict)  # name -> side -> spread

    def failing_rows(self):
        """Benchmarks that fail the check."""
        return [r for r in self.rows if r.status in (Status.SLOWER, Status.FAILED)]

    def failed_runs(self, side):
        """Runs that exited with an error, or didn't record an exit code."""
        return {run: code for run, code in self.exit_codes[side].items() if code != 0}

    @property
    def passed(self):
        return (bool(self.rows) and not self.failing_rows()
                and not any(self.failed_runs(side) for side in SIDES))


def per_operation(benchmark):
    batch = benchmark.get("extra_info", {}).get("operations_per_round", 1)
    return benchmark["stats"]["median"] / batch


def run_medians(path):
    """Map benchmark name to median seconds per operation for one run."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):  # missing or empty: that run didn't finish
        return {}
    return {b["name"]: per_operation(b) for b in data["benchmarks"]}


def per_run(runs):
    """Map benchmark name to its per-run medians."""
    per_bench = {}
    for run in runs:
        for name, median in run.items():
            per_bench.setdefault(name, []).append(median)
    return per_bench


def side_medians(runs):
    """Map benchmark name to the median of its per-run medians."""
    return {name: statistics.median(values) for name, values in per_run(runs).items()}


def spread(values):
    """How much the runs differ, in %, ignoring the fastest and slowest; None if
    there are fewer than 4 runs."""
    if len(values) < 4:
        return None
    middle = sorted(values)[1:-1]
    return (middle[-1] / middle[0] - 1) * 100


def slower_ratio(limit):
    """The check's rule as a ratio: a PR/base ratio above this is too slow."""
    return 1 + limit / 100


def is_slower(base, pr, limit):
    """Is the PR more than limit % slower than the base?"""
    return pr / base > slower_ratio(limit)


def failed_benchmarks(out):
    """Names of benchmarks that failed or errored in any PR run."""
    names = set()
    for path in sorted(out.glob("pr-*.xml")):
        try:
            cases = ET.parse(path).iter("testcase")
        except (OSError, ET.ParseError):  # missing or empty: that run didn't finish
            continue
        names |= {
            case.get("name")
            for case in cases
            if case.find("failure") is not None or case.find("error") is not None
        }
    return names


def exit_codes(out, side):
    """Run number -> pytest exit code, or None if the run didn't record one."""
    codes = {path.stem.split("-")[-1]: None for path in out.glob(f"{side}-*.json")}
    for path in out.glob(f"{side}-*.exit"):
        try:
            codes[path.stem.split("-")[-1]] = int(path.read_text().strip())
        except ValueError:
            codes[path.stem.split("-")[-1]] = None
    return dict(sorted(codes.items(), key=lambda kv: int(kv[0])))


def noisy_benchmarks(runs_by_side, noise_limit):
    """Benchmarks whose spread on a side is above noise_limit: name -> side -> spread."""
    noisy = {}
    for side, runs in runs_by_side.items():
        for name, values in per_run(runs).items():
            value = spread(values)
            if value is not None and value > noise_limit:
                noisy.setdefault(name, {})[side] = value
    return dict(sorted(noisy.items()))


def check(out, limit, noise_limit=None):
    runs = {side: [run_medians(p) for p in sorted(out.glob(f"{side}-*.json"))] for side in SIDES}
    base, pr = side_medians(runs["baseline"]), side_medians(runs["pr"])
    failed = failed_benchmarks(out)
    rows = []
    for name in sorted(set(pr) | failed):
        b, p = base.get(name), pr.get(name)
        pct = None
        if name in failed:
            status = Status.FAILED
        elif b is None:
            status = Status.NEW
        else:
            pct = (p - b) / b * 100
            status = Status.SLOWER if is_slower(b, p, limit) else Status.OK
        rows.append(Row(name, b, p, pct, status))
    noisy = {} if noise_limit is None else noisy_benchmarks(runs, noise_limit)
    return Result(limit, rows, {side: exit_codes(out, side) for side in SIDES}, noise_limit, noisy)


def to_json(result):
    """The result as plain data, for result.json."""
    return {
        "passed": result.passed,
        "limit": result.limit,
        "rows": [
            {"name": r.name, "base": r.base, "pr": r.pr, "pct": r.pct, "status": r.status.value}
            for r in result.rows
        ],
        "exit_codes": result.exit_codes,
        "failing_rows": [r.name for r in result.failing_rows()],
        "failed_runs": {side: result.failed_runs(side) for side in SIDES},
        "noise_limit": result.noise_limit,
        "noisy": result.noisy,
    }


def failure_lines(result):
    """One short log line per failing benchmark or run."""
    lines = []
    for row in result.failing_rows():
        name = row.name.removeprefix("test_")
        lines.append(f"slower: {name} {row.pct:+.1f}%" if row.status is Status.SLOWER
                     else f"failed: {name}")
    for side in SIDES:
        for run, code in result.failed_runs(side).items():
            what = "no exit code" if code is None else f"exit {code}"
            lines.append(f"{side} run {run}: {what}")
    if not result.rows:
        lines.append("no PR benchmark results")
    return lines


def warning_lines(result):
    """One short log line per noisy benchmark and side."""
    return [f"noisy: {name.removeprefix('test_')} runs differ by {value:.1f}% ({side})"
            for name, sides in result.noisy.items() for side, value in sides.items()]


def main(out_dir, threshold, noise_warn=None):
    out = Path(out_dir)
    noise_limit = None if noise_warn is None else float(noise_warn.rstrip("%"))
    result = check(out, float(threshold.rstrip("%")), noise_limit)
    (out / "result.json").write_text(json.dumps(to_json(result), indent=2) + "\n")
    print(f"Benchmark check {'passed' if result.passed else 'failed'} "
          f"(threshold {result.limit:g}%).", flush=True)
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for line in failure_lines(result):
        print(f"::error::{line}" if annotate else f"  {line}", flush=True)
    for line in warning_lines(result):
        print(f"::warning::{line}" if annotate else f"  warning: {line}", flush=True)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
