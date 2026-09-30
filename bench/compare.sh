#!/usr/bin/env bash
# Compare benchmarks: the current working tree against a base git ref.
#
# Usage: hatch run bench:compare [BASE_REF] [OUT_DIR]   (or: make bench-compare)
#
# Both sides run this tree's bench/ files, each in its own venv. The baseline
# runs in a worktree of BASE_REF (default: main), because the benches import
# `fxa` from the directory they run in. Each side runs BENCH_RUNS times
# (default 5), alternating base and PR, and bench/report.py compares the median
# of each side's runs. Results are deleted at the end, unless KEEP_RESULTS=1
# (kept in .benchmarks/compare/<time>-<pid>/) or OUT_DIR is given (used by CI).
# Fails if a median is more than BENCH_FAIL_THRESHOLD slower, or
# if any pytest run fails. Baseline skips are allowed.
set -euo pipefail

section() { printf '\n======== %s ========\n' "$1"; }  # blank line, then a heading

# --- Settings ----------------------------------------------------------------

base_ref="${1:-main}"
repo="$(git rev-parse --show-toplevel)"

# Require the % sign, so a bare number can't be mistaken for a time.
DEFAULT_BENCH_FAIL_THRESHOLD="20%"
threshold="${BENCH_FAIL_THRESHOLD:-$DEFAULT_BENCH_FAIL_THRESHOLD}"
if [[ ! "$threshold" =~ ^[0-9]+%$ ]]; then
  echo "BENCH_FAIL_THRESHOLD must be a whole-number percentage such as 20%, got '$threshold'." >&2
  exit 2
fi

# Runs per side. The median of 5 ignores up to two unusually slow or fast runs
# on either side. In CI experiments (bench/README.md), 5 instead of 3 cut false
# alarms and missed slowdowns about 4-10x for ~10 s more per check.
DEFAULT_BENCH_RUNS=5
runs="${BENCH_RUNS:-$DEFAULT_BENCH_RUNS}"
if [[ ! "$runs" =~ ^[1-9][0-9]*$ ]]; then
  echo "BENCH_RUNS must be a positive whole number, got '$runs'." >&2
  exit 2
fi

# Hatch supplies Python for both venvs; set PYTHON to override it.
python="${PYTHON:-python3}"

# --- Temp space and cleanup --------------------------------------------------

# Everything temporary (worktree, venvs, results unless OUT_DIR is given) lives
# under $work and is removed on exit, including after Ctrl-C.
work="$(mktemp -d)"
base="$work/base"
cleanup() {
  git -C "$repo" worktree remove --force "$base" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT   # Ctrl-C: exit, which runs cleanup
trap 'exit 143' TERM
git -C "$repo" worktree prune  # forget worktrees from runs that were killed

keep=true
if [ -n "${2:-}" ]; then
  out="$2"  # CI reads the results from here after the script ends
elif [ "${KEEP_RESULTS:-}" = "1" ]; then
  # A new folder per run, so earlier kept runs are never overwritten.
  out="$repo/.benchmarks/compare/$(date +%Y%m%d-%H%M%S)-$$"
else
  out="$work/results"
  keep=false
fi
mkdir -p "$out"
out="$(cd "$out" && pwd)"
# A reused OUT_DIR may hold an earlier run's files; remove them so a file this
# run fails to write can't be mistaken for its output.
rm -rf "$out"/baseline-* "$out"/pr-* "$out/report.md"

# --- Baseline code, with this tree's benchmarks ------------------------------

# Check out BASE_REF next to this tree, then replace its bench/ with ours so
# both sides run the same benchmarks.
echo "Baseline: $base_ref ($(git -C "$repo" rev-parse --short "$base_ref"))"
git -C "$repo" worktree add --detach --quiet "$base" "$base_ref"
rm -rf "$base/bench"
cp -R "$repo/bench" "$base/bench"

# --- Install each side -------------------------------------------------------

# A separate venv per side, so each gets its own dependency versions
# (jwtoxide, pyjwt, cryptography, ...) and dependency bumps are measured.
install() {  # install VENV_DIR PACKAGE_DIR
  "$python" -m venv "$1"
  "$1/bin/python" -m pip install --quiet --disable-pip-version-check \
    "$2" -r "$repo/bench/requirements.txt"
}
install "$work/base-venv" "$base"
install "$work/pr-venv" "$repo"

# --- Run the benchmarks ------------------------------------------------------

# Exit codes are captured instead of stopping the script, so every run happens
# and the results are always written.
set +e
base_status=0  # first failing baseline exit code, if any
status=0       # first failing PR exit code, if any

# Alternate base and PR, so a machine that speeds up or slows down during the
# job affects both sides alike. Benches that can't run on the base (new or
# renamed code) skip there via the require fixture in bench/conftest.py.
for i in $(seq 1 "$runs"); do
  section "Baseline run $i/$runs: $base_ref"
  (cd "$base" && PYFXA_BENCH_BASELINE=1 "$work/base-venv/bin/python" -m pytest bench \
    --benchmark-only -rsfE -p no:cacheprovider \
    --benchmark-columns=min,median,max,rounds --benchmark-sort=name \
    --benchmark-json="$out/baseline-$i.json") 2>&1 | tee "$out/baseline-$i.txt"
  run_status=${PIPESTATUS[0]}
  if [ "$run_status" -ne 0 ] && [ "$base_status" -eq 0 ]; then
    base_status=$run_status
  fi

  section "PR run $i/$runs"
  (cd "$repo" && "$work/pr-venv/bin/python" -m pytest bench \
    --benchmark-only -rsfE -p no:cacheprovider \
    --benchmark-columns=min,median,max,rounds --benchmark-sort=name \
    --benchmark-json="$out/pr-$i.json" --junitxml="$out/pr-$i.xml") 2>&1 | tee "$out/pr-$i.txt"
  run_status=${PIPESTATUS[0]}
  if [ "$run_status" -ne 0 ] && [ "$status" -eq 0 ]; then
    status=$run_status
  fi
done

# --- Report ------------------------------------------------------------------

# bench/report.py compares the medians and exits non-zero on a regression.
# If it can't produce a table, the comparison can't be trusted, so that fails
# the job too.
report_status=0
"$work/pr-venv/bin/python" "$repo/bench/report.py" "$out" "$threshold" > "$work/table.md" ||
  report_status=$?
set -e
{
  if [ "$base_status" -ne 0 ]; then
    echo "**Baseline failed (pytest exit $base_status); this comparison fails.** See baseline-*.txt or the job log."
    echo
  fi
  if [ -s "$work/table.md" ]; then
    cat "$work/table.md"
  else
    echo "Could not build the % change table; see the job log."
  fi
} > "$out/report.md"
section "Summary"
cat "$out/report.md"

# Explicit skips succeed; errors, failed tests, and collection failures do not.
if [ "$base_status" -ne 0 ]; then
  echo "::error::A baseline pytest run failed with exit code $base_status."
fi
if [ "$report_status" -ne 0 ] && [ -s "$work/table.md" ]; then
  echo "::error::A benchmark is more than $threshold slower than the base branch."
elif [ "$report_status" -ne 0 ]; then
  echo "::error::bench/report.py failed with exit code $report_status."
fi
if $keep; then
  echo "Results: $out"
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  echo "::notice::Results: $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
fi

# Fail on a failed PR run, a regression, or a failed baseline run.
for code in "$status" "$report_status" "$base_status"; do
  if [ "$code" -ne 0 ]; then
    exit "$code"
  fi
done
exit 0
