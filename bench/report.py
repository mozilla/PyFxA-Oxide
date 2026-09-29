"""Print a Markdown table of base vs PR medians with the % change.

Usage: report.py OUT_DIR THRESHOLD

Reads baseline.json and pr.json (pytest-benchmark --benchmark-json) and pr.xml
(pytest --junitxml) from OUT_DIR. This only reports; pytest's exit code
decides whether the job fails.
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def medians(path):
    """Map benchmark name to median seconds."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):  # missing or empty: that run didn't finish
        return {}
    return {b["name"]: b["stats"]["median"] for b in data["benchmarks"]}


def failed(path):
    """Names of tests that failed or errored."""
    try:
        cases = ET.parse(path).iter("testcase")
    except (OSError, ET.ParseError):  # missing or empty: that run didn't finish
        return set()
    return {
        case.get("name")
        for case in cases
        if case.find("failure") is not None or case.find("error") is not None
    }


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


def format_report(rows, limit):
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
    lines = [
        f"Fails if a median is more than {limit:g}% slower than the base, or a benchmark fails.",
        "",
        line(header),
        "| " + " | ".join(rule) + " |",
        *(line(row) for row in cells),
    ]
    return "\n".join(lines)


def main(out_dir, threshold):
    out = Path(out_dir)
    limit = float(threshold.rstrip("%"))
    base, pr = medians(out / "baseline.json"), medians(out / "pr.json")
    rows = compare(base, pr, failed(out / "pr.xml"), limit)
    print(format_report(rows, limit))


if __name__ == "__main__":
    main(*sys.argv[1:])
