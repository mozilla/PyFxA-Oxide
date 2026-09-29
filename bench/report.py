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
    if not path.exists():
        return {}
    return {b["name"]: b["stats"]["median"] for b in json.loads(path.read_text())["benchmarks"]}


def failed(path):
    """Names of tests that failed or errored."""
    if not path.exists():
        return set()
    return {
        case.get("name")
        for case in ET.parse(path).iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    }


def main(out_dir, threshold):
    out = Path(out_dir)
    limit = float(threshold.rstrip("%"))
    base, pr = medians(out / "baseline.json"), medians(out / "pr.json")
    pr_failed = failed(out / "pr.xml")

    print(f"Fails if a median is more than {limit:g}% slower than the base, or a benchmark fails.\n")
    print("| Benchmark | Base median | PR median | Change | Status |")
    print("|---|---:|---:|---:|---|")
    for name in sorted(set(pr) | pr_failed):
        b, p = base.get(name), pr.get(name)
        change = "—"
        if name in pr_failed:
            status = "⚠️ failed"
        elif b is None:
            status = "🆕 no baseline"
        else:
            pct = (p - b) / b * 100
            change = f"{pct:+.1f}%"
            status = f"❌ over {limit:g}%" if pct > limit else "✅ ok"
        base_s = f"{b * 1e6:.2f} µs" if b is not None else "—"
        pr_s = f"{p * 1e6:.2f} µs" if p is not None else "—"
        print(f"| `{name}` | {base_s} | {pr_s} | {change} | {status} |")


if __name__ == "__main__":
    main(*sys.argv[1:])
