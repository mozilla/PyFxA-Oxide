"""A/A experiment: how much do benchmark medians move when nothing changes?

Runs the unchanged bench suite many times, each in a fresh pytest process like
one side of bench/compare.sh, saves every run's --benchmark-json, and reports
how often each threshold would fail a PR that changed nothing.

Usage (from the repo root, with Hatch installed):

    python3 experiments/bench_threshold.py run [--runs 100] [--out DIR] [--pytest-args "..."]
    python3 experiments/bench_threshold.py analyze DIR

``run`` also analyzes when it finishes. Results default to
.benchmarks/experiment/<timestamp>/ (git-ignored). Standard library only, so
``analyze`` works on results copied from anywhere, e.g. CI artifacts.
"""

import argparse
import csv
import datetime
import itertools
import shlex
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THRESHOLDS = (5, 10, 15, 20, 25, 30, 40, 50)  # % slowdown, as in median:N%


def bench_python():
    env = subprocess.run(
        ["hatch", "env", "find", "bench"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()
    python = Path(env) / "bin" / "python"
    if not python.exists():
        subprocess.run(["hatch", "env", "create", "bench"], cwd=REPO, check=True)
    return python


def run(args):
    out = Path(args.out or REPO / ".benchmarks" / "experiment" /
               datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    python = bench_python()
    log = out / "runs.csv"
    with log.open("w", newline="") as f:
        csv.writer(f).writerow(["run", "started", "seconds", "exit_status"])
    print(f"Writing {args.runs} runs to {out}")
    for i in range(1, args.runs + 1):
        started = time.time()
        result = subprocess.run(
            [str(python), "-m", "pytest", "bench", "--benchmark-only", "-q",
             "-p", "no:cacheprovider", f"--benchmark-json={out / f'run-{i:03d}.json'}",
             *shlex.split(args.pytest_args)],
            cwd=REPO, capture_output=True, text=True,
        )
        seconds = time.time() - started
        with log.open("a", newline="") as f:
            csv.writer(f).writerow([i, f"{started:.3f}", f"{seconds:.2f}", result.returncode])
        status = "ok" if result.returncode == 0 else f"exit {result.returncode}"
        print(f"  run {i:3d}/{args.runs}  {seconds:5.1f}s  {status}", flush=True)
        if result.returncode != 0:
            (out / f"run-{i:03d}.log").write_text(result.stdout + result.stderr)
    analyze(argparse.Namespace(dir=str(out)))


def load(directory):
    """Return ({bench: [median per run]}, {bench: [relative stddev per run]}) in run order."""
    medians, spreads = {}, {}
    for path in sorted(Path(directory).glob("run-*.json")):
        for b in json.loads(path.read_text())["benchmarks"]:
            s = b["stats"]
            medians.setdefault(b["name"], []).append(s["median"])
            spreads.setdefault(b["name"], []).append((s["stddev"] / s["median"], s["iqr"] / s["median"]))
    return medians, spreads


def pct(a, b):
    """% change from a (base) to b (PR); positive means slower."""
    return (b / a - 1) * 100


def quantile(values, q):
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def fmt_us(seconds):
    return f"{seconds * 1e6:.2f}"


def analyze(args):
    directory = Path(args.dir)
    medians, spreads = load(directory)
    if not medians:
        sys.exit(f"No run-*.json files in {directory}")
    names = sorted(medians)
    n = min(len(v) for v in medians.values())
    k = len(names)
    lines = []
    p = lines.append

    p(f"# Benchmark A/A experiment: {n} runs, {k} benchmarks\n")
    p(f"Data: `{directory}`. Every run used identical code, so every difference "
      "between runs is noise. \"Pairs\" are consecutive runs (run i as base, run i+1 "
      f"as PR), like CI's base-then-PR order: {n - 1} pairs.\n")

    # 1. Run-to-run spread of each benchmark's median.
    p("## 1. How much the median moves between runs\n")
    p("CV = standard deviation / mean of the per-run medians. σ(log) is the standard "
      "deviation of ln(median), about the same as CV for small values. Within-run "
      "columns are pytest-benchmark's stddev and IQR over one run's 50 rounds, "
      "relative to that run's median (mean over runs).\n")
    p("| Benchmark | Mean median (µs) | SD (µs) | CV | σ(log) | Min–max (µs) | Within-run SD | Within-run IQR | Lag-1 autocorr |")
    p("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    sigma = {}
    for name in names:
        m = medians[name][:n]
        mean, sd = statistics.fmean(m), statistics.stdev(m)
        logs = [math.log(x) for x in m]
        sigma[name] = statistics.stdev(logs)
        within_sd = statistics.fmean(s for s, _ in spreads[name][:n]) * 100
        within_iqr = statistics.fmean(i for _, i in spreads[name][:n]) * 100
        dev = [x - mean for x in m]
        ac = sum(a * b for a, b in zip(dev, dev[1:])) / sum(d * d for d in dev)
        p(f"| `{name}` | {fmt_us(mean)} | {fmt_us(sd)} | {sd / mean * 100:.1f}% | "
          f"{sigma[name] * 100:.1f}% | {fmt_us(min(m))}–{fmt_us(max(m))} | "
          f"{within_sd:.1f}% | {within_iqr:.1f}% | {ac:+.2f} |")
    p("\nLag-1 autocorrelation near 0 means runs are independent. Clearly positive "
      "means slow drift (thermal, background load): back-to-back base and PR runs "
      "then look more alike than runs far apart, and a single A/A pair understates "
      "the noise across days.\n")

    # 2. A/A % change distribution.
    changes = {name: [pct(a, b) for a, b in zip(medians[name][:n - 1], medians[name][1:n])]
               for name in names}
    p("## 2. % change between consecutive runs (identical code)\n")
    p("| Benchmark | Mean | SD | p50 | p90 | p95 | p99 | Max slowdown |")
    p("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name in names:
        c = changes[name]
        p(f"| `{name}` | {statistics.fmean(c):+.1f}% | {statistics.stdev(c):.1f}% | "
          f"{quantile(c, .5):+.1f}% | {quantile(c, .9):+.1f}% | {quantile(c, .95):+.1f}% | "
          f"{quantile(c, .99):+.1f}% | {max(c):+.1f}% |")
    p("")

    # 3. False failure rate by threshold.
    p("## 3. How often each threshold fails a PR that changed nothing\n")
    p("Share of consecutive pairs where the PR median is more than T% slower. "
      "\"Any bench\" is the share of pairs where at least one of the "
      f"{k} benchmarks exceeds T, i.e. how often CI would go red: the number to "
      "keep low.\n")
    p("| Threshold | " + " | ".join(f"`{name.removeprefix('test_')}`" for name in names) + " | **Any bench** |")
    p("|---:|" + "---:|" * (k + 1))
    for t in THRESHOLDS:
        row = [sum(x > t for x in changes[name]) / len(changes[name]) * 100 for name in names]
        anyb = sum(any(changes[name][i] > t for name in names) for i in range(n - 1)) / (n - 1) * 100
        p(f"| {t}% | " + " | ".join(f"{r:.0f}%" for r in row) + f" | **{anyb:.0f}%** |")
    p("")

    # 4. Recommended thresholds.
    # A/A log ratio has SD sqrt(2)·σ(log) if runs are independent. One-sided
    # false-failure rate α at threshold T: T = exp(z_α · sqrt(2) · σ) − 1.
    z_bench = 2.326  # α = 1% per benchmark
    z_suite = statistics.NormalDist().inv_cdf(1 - 0.05 / k)  # 5% per suite, Bonferroni
    all_pairs = {name: [pct(a, b) for a, b in itertools.permutations(medians[name][:n], 2)]
                 for name in names}
    p("## 4. Suggested thresholds\n")
    p(f"- **Normal, 1% per bench**: exp(2.33 · √2 · σ(log)) − 1. Assumes independent, "
      "roughly log-normal medians.\n"
      f"- **Normal, 5% per suite**: the same with z = {z_suite:.2f} (Bonferroni over {k} benchmarks), so "
      "a no-change PR goes red at most ~5% of the time.\n"
      "- **Empirical p99 / max**: from all ordered pairs of runs "
      f"({n * (n - 1)} pairs; not independent, but uses all the data).\n"
      "- **Detects (80%)**: with the suite threshold, the smallest real slowdown "
      "caught 80% of the time: exp((z + 0.84) · √2 · σ) − 1.\n")
    p("| Benchmark | Normal, 1%/bench | Normal, 5%/suite | Empirical p99 | Empirical max | Detects (80%) |")
    p("|---|---:|---:|---:|---:|---:|")
    suite = {}
    for name in names:
        s = math.sqrt(2) * sigma[name]
        suite[name] = (math.exp(z_suite * s) - 1) * 100
        p(f"| `{name}` | {(math.exp(z_bench * s) - 1) * 100:.1f}% | {suite[name]:.1f}% | "
          f"{quantile(all_pairs[name], .99):.1f}% | {max(all_pairs[name]):.1f}% | "
          f"{(math.exp((z_suite + 0.84) * s) - 1) * 100:.1f}% |")
    worst = max(suite, key=suite.get)
    p(f"\nA single suite-wide threshold has to cover the noisiest benchmark "
      f"(`{worst}`: {suite[worst]:.0f}%). Everything quieter could use a tighter one.\n")

    # CSV for further analysis.
    with (directory / "medians.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run"] + names)
        for i in range(n):
            w.writerow([i + 1] + [f"{medians[name][i]:.9f}" for name in names])

    report = "\n".join(lines)
    (directory / "report.md").write_text(report)
    print(report)
    print(f"\nWrote {directory / 'report.md'} and {directory / 'medians.csv'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    r = sub.add_parser("run", help="run the suite N times, then analyze")
    r.add_argument("--runs", type=int, default=100)
    r.add_argument("--out", help="results directory (default: .benchmarks/experiment/<timestamp>)")
    r.add_argument("--pytest-args", default="", help='extra pytest options, e.g. "--benchmark-disable-gc"')
    r.set_defaults(func=run)
    a = sub.add_parser("analyze", help="analyze an existing results directory")
    a.add_argument("dir")
    a.set_defaults(func=analyze)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
