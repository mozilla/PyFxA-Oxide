---
name: bench-experiment
description: Run the PyFxA-Oxide benchmark threshold experiment (hundreds of runs of unchanged code, replayed through the CI check) on GitHub CI or locally, download the results, and summarise false alarms and detection by threshold. Use when the user wants to check or choose the benchmark threshold, measure CI benchmark noise, or rerun/re-analyse the experiment.
---

# Benchmark threshold experiment

Measures how often the benchmark check in `bench/compare.sh` would fail
unchanged code (false alarms) and how reliably it catches real slowdowns, at
thresholds from 0% to 50%. Background and the current results:
`bench/README.md` ("Choosing the threshold"). How to run the experiments
and read the results: `bench/experiments/README.md`.

Tools: `bench/experiments/collect_runs.py` (collect runs),
`bench/experiments/replay.py` (replay and report),
`.github/workflows/bench-experiment.yml` (CI job: 2 runners x 200 runs).

## 1. Agree the plan with the user

Ask, with a sensible default for each:

- **Where:** GitHub CI (default; those are the numbers that matter) or this
  computer (a quick preview; laptop noise varies a lot between sessions).
- **Or re-analyse existing results:** if there are downloaded results under
  `.benchmarks/experiment/`, no new runs may be needed (skip to step 4).
- **Run name:** short, e.g. `run-2` or `after-cache-change`. Used for the
  branch and the results folder.
- **Runs:** 200 per job (the default). Fewer gives wide uncertainty bands.

## 2. Check before starting

```bash
gh auth status                      # CI only: must be logged in
git status --short                  # CI tests the pushed commit, not local edits
git log --oneline -1
git ls-files .github/workflows/bench-experiment.yml bench/experiments/
```

- **Uncommitted changes:** CI only sees committed code. Tell the user and ask
  whether to commit them first (see the repo's commit rules), or run
  locally instead.
- **Missing workflow or scripts in the commit:** the CI job needs
  `.github/workflows/bench-experiment.yml` and `bench/experiments/` in the
  pushed commit.

## 3a. Run on GitHub CI

Pushing to the shared remote is visible to others and starts CI. **Show the
user the exact command and get a clear yes before running it.**

If `bench-experiment.yml` is on the default branch, the job can be started
without a new branch:

```bash
gh workflow run bench-experiment.yml --ref "$(git branch --show-current)" -f runs=200
```

Otherwise (always true before the workflow is merged), push the current commit
to a `bench-experiment/` branch, which starts the job:

```bash
git push origin HEAD:bench-experiment/<name>
```

Find the run (it can take a few seconds to appear; retry if empty), then wait
for it in the background (about 10 minutes):

```bash
RUN_ID="$(gh run list --workflow bench-experiment.yml --branch bench-experiment/<name> --limit 1 --json databaseId -q '.[0].databaseId')"
gh run watch "$RUN_ID" --exit-status
```

Download **both** jobs' results and check both folders arrived:

```bash
gh run download "$RUN_ID" -D .benchmarks/experiment/<name>
ls .benchmarks/experiment/<name>        # expect bench-experiment-1 and bench-experiment-2
```

If a job is missing, check `gh run view "$RUN_ID"`. Fetch one job alone with
`gh run download "$RUN_ID" -n bench-experiment-2 -D .benchmarks/experiment/<name>`.

## 3b. Run locally

Ask the user to close other apps first. Run in the background; 200 runs take
about 4 minutes:

```bash
python3 bench/experiments/collect_runs.py aa .benchmarks/experiment/<name>/local/aa --runs 200
python3 bench/experiments/replay.py .benchmarks/experiment/<name>/local/aa --out .benchmarks/experiment/<name>/local/roc.html
```

Don't run other heavy work (including other benchmarks) while it runs.

## 4. Analyse

Each job folder has `summary.md`, `roc.html` (charts), `roc.csv` and the raw
runs in `aa/`. Read `summary.md` for each job, and re-run the replay for any
other settings the user wants to compare, e.g. 3 runs per side:

```bash
python3 bench/experiments/replay.py .benchmarks/experiment/<name>/bench-experiment-1/aa \
  --runs-per-side 3 --out .benchmarks/experiment/<name>/bench-experiment-1/rps3/roc.html
```

Also note the runner: `machine_info.cpu.brand_raw` in any `aa/run-*.json`.

## 5. Report to the user

Keep it short and in plain language (see `bench/README.md` for the terms):

- **False alarms** at 10%, 20%, 30% and 40%, with the band's upper end (the
  cautious number). Convert to "about 1 check in N".
- **Detection:** the share of T+10% slowdowns caught, and the weakest
  benchmark.
- **Compared with the current setting:** the default threshold and runs per
  side are in `bench/settings.env` (`BENCH_FAIL_THRESHOLD`, `BENCH_RUNS`), unless
  repository variables override them. Say whether the new results still
  support them, and compare with the earlier results in `bench/README.md`.
- **Differences between the two runners,** if any.
- Don't change the threshold, `BENCH_RUNS` or the README numbers unless the
  user asks.

Point the user to `roc.html` for the charts: `open <folder>/roc.html`.

## 6. Clean up

Offer to delete the trigger branch (ask first; it's on the shared remote):

```bash
git push origin --delete bench-experiment/<name>
```

The results in `.benchmarks/` are git-ignored and can stay.
