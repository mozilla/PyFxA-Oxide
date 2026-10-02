# Threshold experiments

These tools measure how often the benchmark check would fail unchanged code
(false alarms) and how reliably it catches real slowdowns, at thresholds from
0% to 50%. The results behind the current threshold, and the terms used here,
are in [`bench/README.md`](../README.md#choosing-the-threshold-the-experiments).

- `collect_runs.py` runs the benchmarks many times, each in a fresh process,
  and saves every run's results.
- `replay.py` replays those runs through the check at thresholds from 0%
  to 50%, and writes `roc.html` (charts), `roc.csv` and `summary.md`.
- `roc_template.html` is the page for `roc.html`; `replay.py` fills in the data.

Run the commands below from the repository root.

## When to rerun the experiment

The experiment should be rerun when any of the following apply:

- A new benchmark is much noisier than the others. For example, it's
  timing-sensitive: threads, real I/O, or a single operation of under ~1 µs
  that isn't batched.
- What the benchmarks measure changes: `ROUNDS`, `BATCH_SIZE`, `MISS_TOKENS`,
  etc. in `bench/settings.env`.
- The check itself changes: a lower threshold, fewer runs per side, or a new
  rule.
- The CI runners change, or the suite gets a lot bigger (i.e. twice as many
  benchmarks).

CI covers most of these automatically:

- **PRs that change the check or what it measures** run the experiment:
  changes to `bench/settings.env`, `bench/check.py`, `bench/compare.sh`,
  `.github/workflows/bench.yml` or the experiment itself.
- **A monthly run** (on the 1st) catches changes to GitHub's runners.
- **Noisy benchmarks:** the PR check warns when a benchmark's runs differ by
  more than `BENCH_NOISE_WARN` (15%), ignoring the fastest and slowest run. If
  a new benchmark gets this warning, make it steadier (batch it, or give it
  more rounds) or rerun the experiment.

Each experiment job fails if its results don't support the configured check
(the threshold and runs per side in `bench/settings.env`, or the repository
variables): at the threshold, false alarms must stay at or below 0.1% of
checks (the band's upper end), and every benchmark must catch at least 99% of
slowdowns 10 points above the threshold. These limits are `MAX_FALSE_ALARMS`
and `MIN_CAUGHT` in `replay.py`. A suite that gets a lot bigger without
changing any of those files is not covered; rerun the experiment by hand.

## On GitHub CI (recommended)

The results that matter are from CI's machines. `bench-experiment.yml` runs
200 runs on each of two runners (about 10 minutes) and uploads the results.
Besides the automatic runs above, you can start it from your computer:

```bash
# Push your current commit to a bench-experiment/ branch; this starts the job
git push origin HEAD:bench-experiment/my-run

# Follow it, then download both jobs' results
RUN_ID="$(gh run list --workflow bench-experiment.yml --branch bench-experiment/my-run --limit 1 --json databaseId -q '.[0].databaseId')"
gh run watch "$RUN_ID"
gh run download "$RUN_ID" -D .benchmarks/experiment/my-run

# Open the charts, then delete the branch
open .benchmarks/experiment/my-run/bench-experiment-1/roc.html
git push origin --delete bench-experiment/my-run
```

Once `bench-experiment.yml` is on the default branch, you can also start it
without a branch: `gh workflow run bench-experiment.yml --ref <branch> -f runs=200`.

## Locally

Close other apps first: a busy laptop gives noisier results.

```bash
python3 bench/experiments/collect_runs.py aa .benchmarks/experiment/local --runs 200
python3 bench/experiments/replay.py .benchmarks/experiment/local
open .benchmarks/experiment/local/roc.html
```

To try a different number of runs per side on any saved results, without
rerunning anything:

```bash
python3 bench/experiments/replay.py .benchmarks/experiment/my-run/bench-experiment-1/aa --runs-per-side 3 --out /tmp/roc3/roc.html
```

## Reading `roc.html`

1. **False alarms by threshold**: how often unchanged code fails. Lower is
   better. The shaded band shows the uncertainty.
2. **Detection by threshold**: one line per size of slowdown (+10% to 2×). Each
   line drops from 100% to 0% around the threshold equal to that slowdown.
3. **Trade-off chart**: detection against false alarms, one line per slowdown
   size; up and to the left is better.
4. **Summary table**: the headline numbers for 10%, 20%, 30% and 40%.

## Checking with a real slowdown

To confirm the simulated numbers with a real change, make the change in a
scratch worktree (with this tree's `bench/` copied in), alternate runs of
unchanged and changed code, and add them to the replay:

```bash
git worktree add --detach /tmp/slow HEAD && cp -R bench /tmp/slow/  # then edit fxa/ there
python3 bench/experiments/collect_runs.py ab . /tmp/slow .benchmarks/experiment/my-change --runs 60
python3 bench/experiments/replay.py .benchmarks/experiment/local --injected "my change=.benchmarks/experiment/my-change"
git worktree remove --force /tmp/slow
```

The report adds a table comparing how often the change was really caught with
the simulation's prediction.

## With Claude Code

The `bench-experiment` skill (`.claude/skills/bench-experiment/`) runs these
steps for you: it starts the CI job (or a local run), waits, downloads the
results, and summarises them. Ask Claude Code to "run the benchmark
experiment", or type `/bench-experiment`.
