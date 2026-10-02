"""Summarize a benchmark comparison as readable Markdown: write it to
OUT_DIR/report.md and print it.

Usage: report.py OUT_DIR

Reads OUT_DIR/result.json, which must give the verdict ("passed"), the
threshold ("limit"), one row per benchmark ("rows": name, base, pr, pct,
status), each side's run exit codes ("exit_codes"), and what fails
("failing_rows", "failed_runs").
"""

import json
import sys
from pathlib import Path

# Status labels; "slower" includes the threshold, so label() builds it.
LABELS = {"ok": "✅ ok", "failed": "⚠️ failed", "new": "🆕 no baseline"}
SIDE_NAMES = {"baseline": "Baseline", "pr": "PR"}


def short(name):
    """Benchmark name without the test_ prefix."""
    return name.removeprefix("test_")


def us(seconds):
    return "—" if seconds is None else f"{seconds * 1e6:.2f} µs"


def label(status, limit):
    return f"❌ over {limit:g}%" if status == "slower" else LABELS[status]


def reasons(result):
    """Why the check fails, one sentence each; empty if it passes."""
    rows = {row["name"]: row for row in result["rows"]}
    lines = []
    for name in result["failing_rows"]:
        row = rows[name]
        if row["status"] == "slower":
            lines.append(f"{short(name)} is {row['pct']:+.1f}% slower than the base")
        else:
            lines.append(f"{short(name)} failed in a PR run")
    for side, side_name in SIDE_NAMES.items():
        total = len(result["exit_codes"][side])
        by_code = {}
        for run, code in result["failed_runs"][side].items():
            by_code.setdefault(code, []).append(run)
        for code, runs in by_code.items():
            what = "didn't record an exit code" if code is None else f"failed (pytest exit {code})"
            plural = "s" if len(runs) > 1 else ""
            lines.append(f"{side_name} run{plural} {', '.join(runs)} of {total} {what}")
    if not result["rows"]:
        lines.append("No PR benchmark results")
    return lines


def table(rows, limit):
    """The comparison table, one row per benchmark."""
    header = ["Benchmark", "Base median", "PR median", "Change", "Status"]
    right = [False, True, True, True, False]  # right-align the numbers
    cells = [
        [f"`{short(r['name'])}`", us(r["base"]), us(r["pr"]),
         "—" if r["pct"] is None else f"{r['pct']:+.1f}%", label(r["status"], limit)]
        for r in rows
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
    return [line(header), "| " + " | ".join(rule) + " |", *(line(row) for row in cells)]


def render(result):
    """The verdict, the reasons it fails (if any), and the table."""
    lines = ["**✅ Passed**" if result["passed"] else "**❌ Failed**", ""]
    why = reasons(result)
    if why:
        lines += [f"- {reason}" for reason in why] + [""]
    base_runs, pr_runs = (len(result["exit_codes"][side]) for side in SIDE_NAMES)
    lines += [
        f"Fails if a median is more than {result['limit']:g}% slower than the base, "
        "or a benchmark or run fails.",
        f"Each median is the median of {base_runs} base and {pr_runs} PR runs, run alternately.",
        "Times are per operation: benchmarks that time a batch are divided by the batch size.",
        "",
    ]
    if result["rows"]:
        lines += table(result["rows"], result["limit"])
    else:
        lines.append("No PR benchmark results; check the job log.")
    return "\n".join(lines)


def main(out_dir):
    out = Path(out_dir)
    text = render(json.loads((out / "result.json").read_text()))
    (out / "report.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main(*sys.argv[1:])
