"""Compare base and PR benchmark medians, print a Markdown table, and exit 1
if any PR median is more than THRESHOLD slower.

Usage: report.py OUT_DIR THRESHOLD

Reads baseline-N.json and pr-N.json (pytest-benchmark --benchmark-json, one
per run) and pr-N.xml (pytest --junitxml) from OUT_DIR. Each side's value for
a benchmark is the median of its per-run medians, so one unusually slow or
fast run on either side can't decide the result.

Exit status: 0 if every compared benchmark is within the threshold, 1 if any
is slower. Failed pytest runs are judged by compare.sh from pytest's own exit
status; they are also marked in the table.
"""

import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def run_medians(path):
    """Map benchmark name to median seconds for one run."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):  # missing or empty: that run didn't finish
        return {}
    return {b["name"]: b["stats"]["median"] for b in data["benchmarks"]}


def side_medians(out, side):
    """Map benchmark name to the median of its per-run medians, and the run count."""
    runs = [run_medians(p) for p in sorted(out.glob(f"{side}-*.json"))]
    per_bench = {}
    for run in runs:
        for name, median in run.items():
            per_bench.setdefault(name, []).append(median)
    return {name: statistics.median(values) for name, values in per_bench.items()}, len(runs)


def failed(out):
    """Names of tests that failed or errored in any PR run."""
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


def compare(base, pr, pr_failed, limit):
    """One row per PR benchmark: (name, base median, PR median, % change, status).

    Medians and % change are None when missing. Status is "failed", "new" (no
    baseline), "slower" (over the limit) or "ok".
    """
    rows = []
    for name in sorted(set(pr) | pr_failed):
        b, p = base.get(name), pr.get(name)
        pct = None
        if name in pr_failed:
            status = "failed"
        elif b is None:
            status = "new"
        else:
            pct = (p - b) / b * 100
            status = "slower" if pct > limit else "ok"
        rows.append((name, b, p, pct, status))
    return rows


def format_report(rows, limit, runs):
    """Render the rows from compare() as Markdown."""
    if not rows:
        return "No PR benchmark results; check the job log."

    labels = {
        "ok": "✅ ok",
        "slower": f"❌ over {limit:g}%",
        "failed": "⚠️ failed",
        "new": "🆕 no baseline",
    }

    def us(seconds):
        return "—" if seconds is None else f"{seconds * 1e6:.2f} µs"

    header = ["Benchmark", "Base median", "PR median", "Change", "Status"]
    right = [False, True, True, True, False]  # right-align the numbers
    cells = [
        [f"`{name.removeprefix('test_')}`", us(b), us(p),
         "—" if pct is None else f"{pct:+.1f}%", labels[status]]
        for name, b, p, pct, status in rows
    ]
    # Pad every column (except the last, which holds emoji) so the raw
    # Markdown lines up in a terminal or log; it still renders as a table.
    widths = [max(len(row[i]) for row in [header] + cells) for i in range(len(header) - 1)]

    def line(row):
        padded = [
            cell.rjust(w) if r else cell.ljust(w)
            for cell, w, r in zip(row, widths, right)
        ]
        return "| " + " | ".join(padded + row[len(widths):]) + " |"

    rule = ["-" * (w - 1) + ":" if r else "-" * w for w, r in zip(widths, right)] + ["---"]
    base_runs, pr_runs = runs
    lines = [
        f"Fails if a median is more than {limit:g}% slower than the base, or a benchmark fails.",
        f"Each median is the median of {base_runs} base and {pr_runs} PR runs, run alternately.",
        "",
        line(header),
        "| " + " | ".join(rule) + " |",
        *(line(row) for row in cells),
    ]
    return "\n".join(lines)


def main(out_dir, threshold):
    out = Path(out_dir)
    limit = float(threshold.rstrip("%"))
    (base, base_runs), (pr, pr_runs) = side_medians(out, "baseline"), side_medians(out, "pr")
    rows = compare(base, pr, failed(out), limit)
    print(format_report(rows, limit, (base_runs, pr_runs)))
    return 1 if any(status == "slower" for *_, status in rows) else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
