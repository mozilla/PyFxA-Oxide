"""False-positive vs detection trade-off for the benchmark gate, by threshold.

Replays saved runs of unchanged code through the gate's rule (median of
RUNS per side, fail if a PR median is more than T% slower) to measure:

- false positives: how often the suite fails with no real change;
- detection: how often a benchmark fails when its PR side is uniformly
  slowed by +S% (simulated by scaling the PR runs);
- optionally, real injected changes (A/B runs) to check the simulation.

Usage (from the repo root):

    python3 experiments/bench_roc.py AA_DIR [--injected LABEL=DIR ...] [--out FILE]

AA_DIR holds run-*.json from `bench_threshold.py run`. Each injected DIR holds
base/run-*.json and pr/run-*.json. Writes a self-contained HTML report
(default AA_DIR/roc.html) and roc.csv beside it. Standard library only.
"""

import argparse
import bisect
import csv
import glob
import json
import random
import statistics
from pathlib import Path

THRESHOLDS = list(range(0, 51))       # % slowdown the gate allows
SLOWDOWNS = [10, 20, 30, 50, 100]      # simulated true slowdowns, %
HEADLINE = [10, 20, 30, 40]            # thresholds for the summary table
RUNS = 3                               # runs per side, as in bench/compare.sh


def load_runs(directory):
    runs = []
    for path in sorted(glob.glob(f"{directory}/run-*.json")):
        try:
            data = json.loads(Path(path).read_text())
        except ValueError:
            data = {"benchmarks": []}
        runs.append({b["name"]: b["stats"]["median"] for b in data["benchmarks"]})
    return runs


def med3(values):
    return statistics.median(values)


def replay_ratios(runs, names, samples, rng):
    """For random groups of RUNS base + RUNS PR runs: PR median / base median, per benchmark."""
    ratios = {n: [] for n in names}
    for _ in range(samples):
        pick = rng.sample(range(len(runs)), 2 * RUNS)
        base, pr = pick[:RUNS], pick[RUNS:]
        for n in names:
            ratios[n].append(med3([runs[i][n] for i in pr]) / med3([runs[i][n] for i in base]))
    return ratios


def curves(ratios, names):
    """False-positive and detection rates (%) for every threshold and slowdown."""
    count = len(next(iter(ratios.values())))
    worst = sorted(max(ratios[n][i] for n in names) for i in range(count))
    ordered = {n: sorted(ratios[n]) for n in names}

    def above(values, cut):  # share (%) of sorted values strictly above cut
        return (len(values) - bisect.bisect_right(values, cut)) / len(values) * 100

    fp = [above(worst, 1 + t / 100) for t in THRESHOLDS]

    def detect(name, slow, t):
        return above(ordered[name], (1 + t / 100) / (1 + slow / 100))

    per_bench = {n: {s: [detect(n, s, t) for t in THRESHOLDS] for s in SLOWDOWNS} for n in names}
    average = {s: [statistics.fmean(per_bench[n][s][i] for n in names) for i in range(len(THRESHOLDS))]
               for s in SLOWDOWNS}
    return fp, per_bench, average, detect


def bootstrap(runs, names, reps, samples, rng):
    """5th–95th percentile bands for the false-positive and average detection curves."""
    fp_reps, det_reps = [], {s: [] for s in SLOWDOWNS}
    for _ in range(reps):
        resampled = [runs[rng.randrange(len(runs))] for _ in runs]
        fp, _, average, _ = curves(replay_ratios(resampled, names, samples, rng), names)
        fp_reps.append(fp)
        for s in SLOWDOWNS:
            det_reps[s].append(average[s])

    def band(rep_curves):
        cols = list(zip(*rep_curves))
        return ([sorted(c)[int(0.05 * (len(c) - 1))] for c in cols],
                [sorted(c)[int(0.95 * (len(c) - 1))] for c in cols])

    return band(fp_reps), {s: band(det_reps[s]) for s in SLOWDOWNS}


def injected_results(label, directory, names, aa_ratios, rng, samples):
    """True slowdown per benchmark, the gate's catch rate, and the simulation's prediction."""
    base, pr = load_runs(f"{directory}/base"), load_runs(f"{directory}/pr")
    rows = []
    suite = {t: 0 for t in HEADLINE}
    for _ in range(samples):
        b, p = rng.sample(range(len(base)), RUNS), rng.sample(range(len(pr)), RUNS)
        failed_any = any(n not in pr[i] for n in names for i in p)  # errored: a guard fired
        worst = max(
            (med3([pr[i][n] for i in p]) / med3([base[i][n] for i in b]))
            for n in names if all(n in pr[i] for i in p)
        ) if not failed_any else float("inf")
        for t in HEADLINE:
            suite[t] += failed_any or worst > 1 + t / 100
    for n in names:
        errored = sum(n not in r for r in pr)
        if errored:
            rows.append({"name": n, "true": None, "errored": errored, "runs": len(pr),
                         "caught": {t: 100.0 for t in HEADLINE}, "predicted": None})
            continue
        true = (statistics.median(r[n] for r in pr) / statistics.median(r[n] for r in base) - 1) * 100
        caught = {}
        for t in HEADLINE:
            hits = 0
            for _ in range(samples // 4):
                b, p = rng.sample(range(len(base)), RUNS), rng.sample(range(len(pr)), RUNS)
                hits += med3([pr[i][n] for i in p]) / med3([base[i][n] for i in b]) > 1 + t / 100
            caught[t] = hits / (samples // 4) * 100
        cut = {t: (1 + t / 100) / (1 + true / 100) for t in HEADLINE}
        ordered = sorted(aa_ratios[n])
        predicted = {t: (len(ordered) - bisect.bisect_right(ordered, cut[t])) / len(ordered) * 100 for t in HEADLINE}
        rows.append({"name": n, "true": true, "errored": 0, "runs": len(pr),
                     "caught": caught, "predicted": predicted})
    return {"label": label, "runs": [len(base), len(pr)],
            "suite": {t: v / samples * 100 for t, v in suite.items()}, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("aa_dir")
    parser.add_argument("--injected", action="append", default=[], metavar="LABEL=DIR")
    parser.add_argument("--out")
    parser.add_argument("--samples", type=int, default=20000)
    parser.add_argument("--bootstrap", type=int, default=60)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    runs = load_runs(args.aa_dir)
    names = sorted(set.intersection(*(set(r) for r in runs)))
    print(f"{len(runs)} unchanged-code runs, {len(names)} benchmarks")
    ratios = replay_ratios(runs, names, args.samples, rng)
    fp, per_bench, average, detect = curves(ratios, names)
    print(f"bootstrapping ({args.bootstrap} resamples)...")
    fp_band, det_band = bootstrap(runs, names, args.bootstrap, max(2000, args.samples // 5), rng)

    summary = []
    for t in HEADLINE:
        i = THRESHOLDS.index(t)
        at = {rel: {n: detect(n, t + rel, t) for n in names} for rel in (0, 5, 10)}
        weakest = min(at[10], key=at[10].get)
        summary.append({
            "threshold": t, "fp": fp[i], "fp_lo": fp_band[0][i], "fp_hi": fp_band[1][i],
            "at_t": statistics.fmean(at[0].values()), "at_t5": statistics.fmean(at[5].values()),
            "at_t10": statistics.fmean(at[10].values()), "weakest": weakest.removeprefix("test_"),
            "weakest_t10": at[10][weakest],
            "at_2x": min(detect(n, 100, t) for n in names),
        })
    injected = []
    for spec in args.injected:
        label, _, directory = spec.partition("=")
        print(f"injected: {label}")
        injected.append(injected_results(label, directory, names, ratios, rng, args.samples // 2))

    out = Path(args.out or Path(args.aa_dir) / "roc.html")
    with (out.with_suffix(".csv")).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["threshold_pct", "false_positive_pct"] + [f"detect_avg_plus{s}_pct" for s in SLOWDOWNS])
        for i, t in enumerate(THRESHOLDS):
            w.writerow([t, f"{fp[i]:.3f}"] + [f"{average[s][i]:.3f}" for s in SLOWDOWNS])
    data = {
        "runs": len(runs), "benchmarks": [n.removeprefix("test_") for n in names], "samples": args.samples,
        "thresholds": THRESHOLDS, "slowdowns": SLOWDOWNS, "headline": HEADLINE,
        "fp": fp, "fp_band": fp_band, "average": average, "det_band": det_band,
        "per_bench": {n.removeprefix("test_"): per_bench[n] for n in names},
        "per_bench_t10": {n.removeprefix("test_"): {str(t): detect(n, t + 10, t) for t in HEADLINE} for n in names},
        "summary": summary, "injected": injected, "source": str(args.aa_dir),
    }
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(data)))
    print(f"wrote {out} and {out.with_suffix('.csv')}")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Benchmark gate: false positives vs detection by threshold</title>
<style>
  .viz-root { color-scheme: light;
    --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
    --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
    --fp: #eb6834;
    --s1: #86b6ef; --s2: #5598e7; --s3: #2a78d6; --s4: #1c5cab; --s5: #104281; }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root { color-scheme: dark;
      --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7;
      --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
      --fp: #d95926;
      --s1: #184f95; --s2: #256abf; --s3: #3987e5; --s4: #6da7ec; --s5: #b7d3f6; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; }
  .viz-root { background: var(--page); color: var(--text-primary); min-height: 100vh;
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 1040px; margin: 0 auto; padding: 32px 22px 70px; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  h2 { font-size: 19px; margin: 34px 0 6px; }
  p { margin: 0 0 10px; color: var(--text-secondary); max-width: 860px; }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 18px 18px 12px; margin: 12px 0; position: relative; }
  .card h3 { margin: 0 0 2px; font-size: 15px; }
  .card .sub { font-size: 13px; color: var(--text-secondary); margin: 0 0 8px; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 13px; color: var(--text-secondary); margin: 4px 0 6px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .legend i { display: inline-block; width: 16px; height: 2px; border-radius: 1px; }
  .legend i.band { height: 10px; opacity: .25; border-radius: 2px; }
  .tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px; font-size: 12.5px; box-shadow: 0 4px 14px rgba(0,0,0,.14);
    opacity: 0; transition: opacity .08s; min-width: 190px; z-index: 3; }
  .tip.show { opacity: 1; }
  .tip .t { color: var(--text-secondary); margin-bottom: 4px; }
  .tip .row { display: flex; align-items: center; gap: 7px; }
  .tip .row b { font-variant-numeric: tabular-nums; min-width: 52px; text-align: right; color: var(--text-primary); }
  .tip .row i { width: 12px; height: 2px; display: inline-block; }
  .tip .row span { color: var(--text-secondary); }
  table { border-collapse: collapse; width: 100%; font-size: 13.5px; margin: 8px 0; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
  th { color: var(--text-secondary); font-weight: 600; }
  td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  details { margin: 8px 0; font-size: 13.5px; }
  summary { cursor: pointer; color: var(--text-secondary); }
  .note { font-size: 12.5px; color: var(--muted); }
  code { font: 12.5px ui-monospace, SFMono-Regular, Menlo, monospace; }
  .axis-label { fill: var(--muted); font-size: 11.5px; }
  .tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
</style>
</head>
<body class="viz-root">
<main>
  <h1>Benchmark gate: false positives vs detection by threshold</h1>
  <p id="intro"></p>

  <h2>1. How often the gate fails with no real change</h2>
  <div class="card" id="fp-card">
    <h3>False positives, whole suite (any benchmark fails)</h3>
    <p class="sub">Zoomed to 0–10%: at thresholds under ~5%, nearly every run fails. Shaded: 5th–95th percentile band.</p>
    <svg id="fp-chart" role="img" aria-label="False-positive rate by threshold"></svg>
  </div>

  <h2>2. How often it catches a real slowdown</h2>
  <div class="card" id="det-card">
    <h3>Detection rate, average over benchmarks, by the size of a true uniform slowdown</h3>
    <div class="legend" id="det-legend"></div>
    <svg id="det-chart" role="img" aria-label="Detection rate by threshold, one line per slowdown size"></svg>
  </div>

  <h2>3. The trade-off (ROC-style)</h2>
  <div class="card" id="roc-card">
    <h3>Detection vs false positives: each point on a line is a threshold</h3>
    <p class="sub">Up and left is better. Each line runs from threshold 0% (right) to 50% (left). Markers: thresholds 10–40% on the +20% line.</p>
    <div class="legend" id="roc-legend"></div>
    <svg id="roc-chart" role="img" aria-label="Detection rate against false-positive rate, one line per slowdown size"></svg>
  </div>

  <h2>4. Headline thresholds</h2>
  <table id="summary"></table>
  <p class="note">"Caught" = share of simulated gate runs that fail. T, T+5 and T+10 are true slowdowns relative to each threshold, averaged over benchmarks. "2×, weakest" is the lowest detection of a doubling across all benchmarks.</p>

  <div id="injected"></div>

  <details><summary>Per-benchmark detection at T+10%</summary><table id="per-bench"></table></details>
  <details><summary>Chart data as a table</summary><table id="data-table"></table></details>
</main>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const el = (tag, attrs = {}, text) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
};
const h = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
const pct = (v, d = 1) => v === null || v === undefined ? "—" : (v < 0.05 && v > 0 ? "<0.1%" : v.toFixed(d) + "%");
const SERIES = D.slowdowns.map((s, i) => ({ s, key: String(s), label: s === 100 ? "2× (+100%)" : `+${s}% slowdown`, color: `var(--s${i + 1})` }));

$("intro").textContent =
  `Replayed from ${D.runs} local runs of unchanged code (${D.benchmarks.length} benchmarks), ` +
  `${D.samples.toLocaleString()} random groups of 3 base + 3 PR runs, using the gate's rule: fail if a PR median ` +
  `(median of 3 runs) is more than T% slower. Detection simulates a true slowdown by scaling one benchmark's PR runs. ` +
  `Local laptop data: a preview, not the final numbers.`;

function legend(container, items) {
  for (const it of items) {
    const s = h("span"); const i = h("i"); i.style.background = it.color; if (it.band) i.className = "band";
    s.append(i, document.createTextNode(it.label)); container.append(s);
  }
}

function lineChart({ svg, card, xDomain, yDomain, xTicks, yTicks, xLabel, yLabel, series, bands = [], markers = [], tip, xLog = false, xFmt = t => `${t}%` }) {
  const W = 980, H = 300, m = { l: 52, r: 16, t: 10, b: 42 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const xn = v => xLog ? (Math.log10(Math.max(v, xDomain[0])) - Math.log10(xDomain[0])) / (Math.log10(xDomain[1]) - Math.log10(xDomain[0]))
                      : (v - xDomain[0]) / (xDomain[1] - xDomain[0]);
  const x = v => m.l + xn(v) * (W - m.l - m.r);
  const y = v => H - m.b - (Math.min(v, yDomain[1]) - yDomain[0]) / (yDomain[1] - yDomain[0]) * (H - m.t - m.b);
  for (const t of yTicks) {
    svg.append(el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: "var(--grid)", "stroke-width": 1 }));
    svg.append(el("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end", class: "tick" }, `${t}%`));
  }
  svg.append(el("line", { x1: m.l, x2: W - m.r, y1: y(yDomain[0]), y2: y(yDomain[0]), stroke: "var(--axis)", "stroke-width": 1 }));
  for (const t of xTicks) svg.append(el("text", { x: x(t), y: H - m.b + 18, "text-anchor": "middle", class: "tick" }, xFmt(t)));
  svg.append(el("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle", class: "axis-label" }, xLabel));
  svg.append(el("text", { x: 12, y: (m.t + H - m.b) / 2, transform: `rotate(-90 12 ${(m.t + H - m.b) / 2})`, "text-anchor": "middle", class: "axis-label" }, yLabel));
  const clip = `clip-${svg.id}`;
  const defs = el("defs"); const cp = el("clipPath", { id: clip }); cp.append(el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b })); defs.append(cp); svg.append(defs);
  const g = el("g", { "clip-path": `url(#${clip})` }); svg.append(g);
  for (const b of bands) {
    const pts = b.xs.map((xv, i) => `${x(xv)},${y(b.hi[i])}`).concat(b.xs.map((xv, i) => `${x(xv)},${y(b.lo[i])}`).reverse());
    g.append(el("polygon", { points: pts.join(" "), fill: b.color, opacity: 0.12 }));
  }
  for (const s of series) {
    const d = s.xs.map((xv, i) => `${i ? "L" : "M"}${x(xv)},${y(s.ys[i])}`).join("");
    g.append(el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  for (const mk of markers) {
    svg.append(el("circle", { cx: x(mk.x), cy: y(mk.y), r: 4.5, fill: mk.color, stroke: "var(--surface-1)", "stroke-width": 2 }));
    if (mk.label) svg.append(el("text", { x: x(mk.x) + 8, y: y(mk.y) + (mk.above ? -9 : 14), class: "tick" }, mk.label));
  }
  if (!tip) return;
  const hair = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--axis)", "stroke-width": 1, opacity: 0 }); svg.append(hair);
  const box = h("div", undefined, "tip"); card.append(box);
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", tabindex: 0 }); svg.append(hit);
  const show = xv => {
    hair.setAttribute("x1", x(xv)); hair.setAttribute("x2", x(xv)); hair.setAttribute("opacity", 1);
    box.replaceChildren(h("div", `Threshold ${xv}%`, "t"));
    for (const r of tip(xv)) {
      const row = h("div", undefined, "row"); const i = h("i"); i.style.background = r.color;
      row.append(h("b", r.value), i, h("span", r.label)); box.append(row);
    }
    const rect = svg.getBoundingClientRect(), cr = card.getBoundingClientRect();
    const px = rect.left - cr.left + x(xv) / W * rect.width;
    box.style.left = Math.min(px + 12, cr.width - 210) + "px"; box.style.top = (rect.top - cr.top + 10) + "px";
    box.classList.add("show");
  };
  const toX = ev => { const r = svg.getBoundingClientRect(); const v = xDomain[0] + ((ev.clientX - r.left) / r.width * W - m.l) / (W - m.l - m.r) * (xDomain[1] - xDomain[0]); return Math.max(xDomain[0], Math.min(xDomain[1], Math.round(v))); };
  hit.addEventListener("pointermove", ev => show(toX(ev)));
  hit.addEventListener("pointerleave", () => { box.classList.remove("show"); hair.setAttribute("opacity", 0); });
  let kx = 20;
  hit.addEventListener("focus", () => show(kx));
  hit.addEventListener("blur", () => { box.classList.remove("show"); hair.setAttribute("opacity", 0); });
  hit.addEventListener("keydown", ev => { if (ev.key === "ArrowRight") kx = Math.min(xDomain[1], kx + 1); else if (ev.key === "ArrowLeft") kx = Math.max(xDomain[0], kx - 1); else return; ev.preventDefault(); show(kx); });
}

const T = D.thresholds, ticks = [0, 10, 20, 30, 40, 50];
lineChart({ svg: $("fp-chart"), card: $("fp-card"), xDomain: [0, 50], yDomain: [0, 10], xTicks: ticks, yTicks: [0, 2, 4, 6, 8, 10],
  xLabel: "Threshold (allowed slowdown)", yLabel: "Unchanged PRs failed",
  series: [{ xs: T, ys: D.fp, color: "var(--fp)" }],
  bands: [{ xs: T, lo: D.fp_band[0], hi: D.fp_band[1], color: "var(--fp)" }],
  markers: D.headline.map(t => ({ x: t, y: D.fp[T.indexOf(t)], color: "var(--fp)", label: pct(D.fp[T.indexOf(t)], 2), above: true })),
  tip: xv => { const i = T.indexOf(xv); return [{ value: pct(D.fp[i], 2), color: "var(--fp)", label: `false positives (band ${pct(D.fp_band[0][i], 2)}–${pct(D.fp_band[1][i], 2)})` }]; } });

legend($("det-legend"), SERIES);
lineChart({ svg: $("det-chart"), card: $("det-card"), xDomain: [0, 50], yDomain: [0, 100], xTicks: ticks, yTicks: [0, 25, 50, 75, 100],
  xLabel: "Threshold (allowed slowdown)", yLabel: "Slowdowns caught",
  series: SERIES.map(s => ({ xs: T, ys: D.average[s.key], color: s.color })),
  bands: SERIES.map(s => ({ xs: T, lo: D.det_band[s.key][0], hi: D.det_band[s.key][1], color: s.color })),
  tip: xv => { const i = T.indexOf(xv); return SERIES.slice().reverse().map(s => ({ value: pct(D.average[s.key][i]), color: s.color, label: s.label })); } });

legend($("roc-legend"), SERIES);
// Log x-axis: false positives at useful thresholds are fractions of a percent.
// Zero (never failed in the replay) is drawn at the axis floor.
const FLOOR = 0.01;
lineChart({ svg: $("roc-chart"), card: $("roc-card"), xDomain: [FLOOR, 100], yDomain: [0, 100], xLog: true,
  xTicks: [0.01, 0.1, 1, 10, 100], xFmt: t => `${t}%`, yTicks: [0, 25, 50, 75, 100],
  xLabel: "False positives: unchanged PRs failed (log scale; 0.01% = never in the replay)", yLabel: "Slowdowns caught",
  series: SERIES.map(s => ({ xs: D.fp.map(v => Math.max(v, FLOOR)), ys: D.average[s.key], color: s.color })),
  markers: D.headline.map(t => {
    const s = SERIES.find(z => z.s === 20);
    return { x: Math.max(D.fp[T.indexOf(t)], FLOOR), y: D.average[s.key][T.indexOf(t)], color: s.color, label: `T=${t}%` };
  }) });

// Summary table.
const sum = $("summary");
const head = sum.createTHead().insertRow();
for (const [t, n] of [["Threshold", 1], ["False positives (band)", 1], ["Caught at T", 1], ["at T+5%", 1], ["at T+10%", 1], ["Weakest at T+10%", 0], ["2×, weakest", 1]]) {
  const th = h("th", t); if (n) th.className = "n"; head.append(th);
}
const body = sum.createTBody();
for (const r of D.summary) {
  const tr = body.insertRow();
  const cells = [[`${r.threshold}%`, 1], [`${pct(r.fp, 2)} (${pct(r.fp_lo, 2)}–${pct(r.fp_hi, 2)})`, 1], [pct(r.at_t), 1], [pct(r.at_t5), 1], [pct(r.at_t10), 1], [`${r.weakest} ${pct(r.weakest_t10)}`, 0], [pct(r.at_2x), 1]];
  for (const [v, n] of cells) { const td = h("td", v); if (n) td.className = "n"; tr.append(td); }
}

// Injected changes.
for (const inj of D.injected) {
  const wrap = $("injected");
  wrap.append(h("h2", `Real injected change: ${inj.label}`));
  wrap.append(h("p", `${inj.runs[0]} unchanged + ${inj.runs[1]} changed runs, alternating. Suite-level: the job fails ` +
    D.headline.map(t => `${pct(inj.suite[t])} at ${t}%`).join(", ") + ". Per benchmark, caught (simulation's prediction for the same true slowdown):"));
  const tb = h("table"); wrap.append(tb);
  const hr = tb.createTHead().insertRow();
  [["Benchmark", 0], ["True slowdown", 1], ...D.headline.map(t => [`Caught at ${t}%`, 1])].forEach(([t, n]) => { const th = h("th", t); if (n) th.className = "n"; hr.append(th); });
  const b = tb.createTBody();
  for (const r of inj.rows) {
    if (r.true !== null && Math.abs(r.true) < 3) continue;
    const tr = b.insertRow(); tr.append(h("td", r.name.replace("test_", "")));
    const tdTrue = h("td", r.true === null ? `errored in ${r.errored}/${r.runs} runs (guard)` : `${r.true > 0 ? "+" : ""}${r.true.toFixed(1)}%`); tdTrue.className = "n"; tr.append(tdTrue);
    for (const t of D.headline) {
      const v = r.predicted ? `${pct(r.caught[t])} (${pct(r.predicted[t])})` : `${pct(r.caught[t])}`;
      const td = h("td", v); td.className = "n"; tr.append(td);
    }
  }
  wrap.append(h("p", "Benchmarks whose true change was under 3% are omitted.", "note"));
}

// Per-benchmark detection at T+10.
const pb = $("per-bench"); const pbh = pb.createTHead().insertRow();
pbh.append(h("th", "Benchmark")); D.headline.forEach(t => { const th = h("th", `T=${t}%, +${t + 10}% slowdown`); th.className = "n"; pbh.append(th); });
const pbb = pb.createTBody();
for (const name of D.benchmarks) {
  const tr = pbb.insertRow(); tr.append(h("td", name));
  for (const t of D.headline) {
    const v = D.per_bench_t10[name][String(t)];
    const td = h("td", v === null ? "—" : pct(v)); td.className = "n"; tr.append(td);
  }
}

// Data table.
const dt = $("data-table"); const dth = dt.createTHead().insertRow();
["Threshold", "False positives", ...SERIES.map(s => `Caught: ${s.label}`)].forEach((t, i) => { const th = h("th", t); if (i) th.className = "n"; dth.append(th); });
const dtb = dt.createTBody();
T.forEach((t, i) => { const tr = dtb.insertRow(); [`${t}%`, pct(D.fp[i], 2), ...SERIES.map(s => pct(D.average[s.key][i]))].forEach((v, j) => { const td = h("td", v); if (j) td.className = "n"; tr.append(td); }); });
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
