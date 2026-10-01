"""Replay saved runs through the benchmark check at every threshold from 0% to
50%, to choose one: how often unchanged code fails (false alarms), and how
often a slowdown is caught.

Usage (from the repo root):

    python3 bench/experiments/replay.py AA_DIR [--runs-per-side N]
        [--injected LABEL=DIR ...] [--out FILE]

AA_DIR holds run-*.json of unchanged code (collect_runs.py aa). Slowdowns are
simulated by scaling the PR side's runs; each --injected DIR holds real
changed-code runs (collect_runs.py ab) to check that simulation. Uses the
check's own rule and medians (bench/check.py). Writes a self-contained HTML
report (default AA_DIR/roc.html, from roc_template.html), plus roc.csv and
summary.md, and prints the summary.
"""

import argparse
import bisect
import csv
import glob
import json
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

# The check's own rule and medians, and the default runs per side.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check  # noqa: E402
from settings import SETTINGS  # noqa: E402

THRESHOLDS = list(range(0, 51))       # % slowdown the check allows
SLOWDOWNS = [10, 20, 30, 50, 100]      # simulated true slowdowns, %
HEADLINE = [10, 20, 30, 40]            # thresholds for the summary table
BAND = (0.05, 0.95)                    # bootstrap percentiles shown as the uncertainty band
MIN_BOOTSTRAP_SAMPLES = 2000           # replays per bootstrap resample, at least
TEMPLATE = Path(__file__).with_name("roc_template.html")


def load_runs(directory):
    """Each run's medians per operation, as the check reads them."""
    paths = sorted(glob.glob(f"{directory}/run-*.json"))
    return [check.run_medians(Path(path)) for path in paths]


def side_ratios(base_runs, pr_runs, names):
    """PR median / base median per benchmark, with the check's median of runs."""
    base = check.side_medians(base_runs)
    pr = check.side_medians(pr_runs)
    return {n: pr[n] / base[n] for n in names}


def replay_ratios(runs, names, runs_per_side, samples, rng):
    """For random groups of base + PR runs: PR median / base median, per benchmark."""
    ratios = {n: [] for n in names}
    for _ in range(samples):
        pick = [runs[i] for i in rng.sample(range(len(runs)), 2 * runs_per_side)]
        for n, ratio in side_ratios(pick[:runs_per_side], pick[runs_per_side:], names).items():
            ratios[n].append(ratio)
    return ratios


def share_above(sorted_values, cut):
    """Share (%) of sorted_values strictly above cut."""
    above = len(sorted_values) - bisect.bisect_right(sorted_values, cut)
    return above / len(sorted_values) * 100


def detection_rate(sorted_ratios, slowdown, threshold):
    """Share (%) of replays the check fails if the PR is truly slowdown % slower.

    A true slowdown multiplies each replay's ratio by (1 + slowdown), so a
    replay fails if its ratio is above the check's limit divided by that.
    """
    return share_above(sorted_ratios, check.slower_ratio(threshold) / (1 + slowdown / 100))


@dataclass
class Curves:
    """False-alarm and detection rates (%), one value per threshold in THRESHOLDS."""

    false_alarms: list     # whole suite: any benchmark fails
    sorted_ratios: dict    # benchmark -> its replayed ratios, sorted
    per_bench: dict        # benchmark -> slowdown -> detection rates
    average: dict          # slowdown -> detection rates averaged over benchmarks

    def detect(self, name, slowdown, threshold):
        return detection_rate(self.sorted_ratios[name], slowdown, threshold)


def curves(ratios, names):
    """False-alarm and detection rates for every threshold and slowdown."""
    count = len(next(iter(ratios.values())))
    worst = sorted(max(ratios[n][i] for n in names) for i in range(count))
    ordered = {n: sorted(ratios[n]) for n in names}
    false_alarms = [share_above(worst, check.slower_ratio(t)) for t in THRESHOLDS]
    per_bench = {
        n: {s: [detection_rate(ordered[n], s, t) for t in THRESHOLDS] for s in SLOWDOWNS}
        for n in names
    }
    average = {
        s: [statistics.fmean(per_bench[n][s][i] for n in names) for i in range(len(THRESHOLDS))]
        for s in SLOWDOWNS
    }
    return Curves(false_alarms, ordered, per_bench, average)


def band(rep_curves):
    """Lower and upper BAND percentiles at each threshold, across bootstrap curves."""
    columns = [sorted(c) for c in zip(*rep_curves)]
    return tuple([c[int(p * (len(c) - 1))] for c in columns] for p in BAND)


def bootstrap(runs, names, runs_per_side, reps, samples, rng):
    """Uncertainty bands for the false-alarm and average detection curves."""
    fp_reps, det_reps = [], {s: [] for s in SLOWDOWNS}
    for _ in range(reps):
        resampled = [runs[rng.randrange(len(runs))] for _ in runs]
        result = curves(replay_ratios(resampled, names, runs_per_side, samples, rng), names)
        fp_reps.append(result.false_alarms)
        for s in SLOWDOWNS:
            det_reps[s].append(result.average[s])
    return band(fp_reps), {s: band(det_reps[s]) for s in SLOWDOWNS}


def headline_summary(result, names, fp_band):
    """One row per HEADLINE threshold: false alarms, and detection at T, T+5% and T+10%."""
    rows = []
    for t in HEADLINE:
        i = THRESHOLDS.index(t)
        at = {extra: {n: result.detect(n, t + extra, t) for n in names} for extra in (0, 5, 10)}
        weakest = min(at[10], key=at[10].get)
        rows.append({
            "threshold": t, "fp": result.false_alarms[i],
            "fp_lo": fp_band[0][i], "fp_hi": fp_band[1][i],
            "at_t": statistics.fmean(at[0].values()),
            "at_t5": statistics.fmean(at[5].values()),
            "at_t10": statistics.fmean(at[10].values()),
            "weakest": weakest.removeprefix("test_"), "weakest_t10": at[10][weakest],
            "at_2x": min(result.detect(n, 100, t) for n in names),
        })
    return rows


def injected_results(label, directory, names, aa, runs_per_side, samples, rng):
    """For real changed-code runs: each benchmark's true slowdown, how often the
    check caught it, and what the simulation (aa, the unchanged-code curves)
    predicted for that slowdown."""
    base, pr = load_runs(f"{directory}/base"), load_runs(f"{directory}/pr")
    errored = {n: sum(n not in run for run in pr) for n in names}  # a guard fired
    clean = [n for n in names if not errored[n]]
    suite = {t: 0 for t in HEADLINE}
    caught = {n: {t: 0 for t in HEADLINE} for n in clean}
    for _ in range(samples):
        base_medians = check.side_medians(rng.sample(base, runs_per_side))
        pr_runs = rng.sample(pr, runs_per_side)
        pr_medians = check.side_medians(pr_runs)
        guard_fired = any(n not in run for run in pr_runs for n in names)
        for t in HEADLINE:
            suite[t] += guard_fired or any(
                check.is_slower(base_medians[n], pr_medians[n], t) for n in names)
            for n in clean:
                caught[n][t] += check.is_slower(base_medians[n], pr_medians[n], t)

    rows = []
    for n in names:
        if errored[n]:
            rows.append({"name": n, "true": None, "errored": errored[n], "runs": len(pr),
                         "caught": {t: 100.0 for t in HEADLINE}, "predicted": None})
            continue
        ratio = statistics.median(r[n] for r in pr) / statistics.median(r[n] for r in base)
        true = (ratio - 1) * 100
        rows.append({
            "name": n, "true": true, "errored": 0, "runs": len(pr),
            "caught": {t: caught[n][t] / samples * 100 for t in HEADLINE},
            "predicted": {t: detection_rate(aa.sorted_ratios[n], true, t) for t in HEADLINE},
        })
    return {"label": label, "runs": [len(base), len(pr)],
            "suite": {t: v / samples * 100 for t, v in suite.items()}, "rows": rows}


def summary_markdown(summary, run_count, bench_count, runs_per_side):
    """The headline thresholds as a Markdown table."""
    lines = [
        f"Replayed {run_count} runs of unchanged code ({bench_count} benchmarks), "
        f"median of {runs_per_side} runs per side.",
        "",
        "| Threshold | False alarms (5–95% band) | Slowdown of T+10% caught "
        "| Weakest benchmark at T+10% | 2× caught, weakest |",
        "|---:|---:|---:|---|---:|",
    ]
    for r in summary:
        lines.append(
            f"| {r['threshold']}% | {r['fp']:.2f}% ({r['fp_lo']:.2f}–{r['fp_hi']:.2f}%) "
            f"| {r['at_t10']:.1f}% | `{r['weakest']}` {r['weakest_t10']:.1f}% "
            f"| {r['at_2x']:.1f}% |"
        )
    return "\n".join(lines)


def write_csv(path, result):
    """The false-alarm and average detection curves, one row per threshold."""
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["threshold_pct", "false_positive_pct"]
                        + [f"detect_avg_plus{s}_pct" for s in SLOWDOWNS])
        for i, t in enumerate(THRESHOLDS):
            writer.writerow([t, f"{result.false_alarms[i]:.3f}"]
                            + [f"{result.average[s][i]:.3f}" for s in SLOWDOWNS])


def write_html(path, data):
    """The report: roc_template.html with the data put in."""
    path.write_text(TEMPLATE.read_text().replace("__DATA__", json.dumps(data)))


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("aa_dir")
    parser.add_argument("--injected", action="append", default=[], metavar="LABEL=DIR")
    parser.add_argument("--out")
    parser.add_argument("--samples", type=int, default=20000,
                        help="random groups of runs to replay (half that per --injected)")
    parser.add_argument("--bootstrap", type=int, default=60)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--runs-per-side", type=int, default=SETTINGS.BENCH_RUNS,
                        help="runs per side (default: BENCH_RUNS in bench/settings.env)")
    return parser.parse_args()


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    runs = load_runs(args.aa_dir)
    names = sorted(set.intersection(*(set(r) for r in runs)))
    print(f"{len(runs)} unchanged-code runs, {len(names)} benchmarks")
    result = curves(replay_ratios(runs, names, args.runs_per_side, args.samples, rng), names)
    print(f"bootstrapping ({args.bootstrap} resamples)...")
    fp_band, det_band = bootstrap(runs, names, args.runs_per_side, args.bootstrap,
                                  max(MIN_BOOTSTRAP_SAMPLES, args.samples // 5), rng)
    summary = headline_summary(result, names, fp_band)
    injected = []
    for spec in args.injected:
        label, _, directory = spec.partition("=")
        print(f"injected: {label}")
        injected.append(injected_results(label, directory, names, result,
                                         args.runs_per_side, args.samples // 2, rng))

    out = Path(args.out or Path(args.aa_dir) / "roc.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    short = {n: n.removeprefix("test_") for n in names}
    write_csv(out.with_suffix(".csv"), result)
    write_html(out, {
        "runs": len(runs), "benchmarks": list(short.values()), "samples": args.samples,
        "thresholds": THRESHOLDS, "slowdowns": SLOWDOWNS, "headline": HEADLINE,
        "fp": result.false_alarms, "fp_band": fp_band, "average": result.average,
        "det_band": det_band,
        "per_bench": {short[n]: result.per_bench[n] for n in names},
        "per_bench_t10": {short[n]: {str(t): result.detect(n, t + 10, t) for t in HEADLINE}
                          for n in names},
        "summary": summary, "injected": injected, "source": str(args.aa_dir),
        "runs_per_side": args.runs_per_side,
    })
    text = summary_markdown(summary, len(runs), len(names), args.runs_per_side)
    out.with_name("summary.md").write_text(text + "\n")
    print(text)
    print(f"\nwrote {out}, {out.with_suffix('.csv')} and {out.with_name('summary.md')}")


if __name__ == "__main__":
    main()
