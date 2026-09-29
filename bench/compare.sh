#!/usr/bin/env bash
# Compare benchmarks: the current working tree against a base git ref.
#
# Usage: hatch run bench:compare [BASE_REF] [OUT_DIR]   (or: make bench-compare)
#
# Both sides run this tree's bench/ files, each in its own venv. The baseline
# runs in a worktree of BASE_REF (default: main), because the benches import
# `fxa` from the directory they run in. Results are kept in OUT_DIR if given,
# otherwise deleted. Fails if a median is more than BENCH_FAIL_THRESHOLD
# (default 20%) slower, or if a benchmark fails on the current tree.
set -euo pipefail

# --- Settings ----------------------------------------------------------------

base_ref="${1:-main}"
repo="$(git rev-parse --show-toplevel)"

# pytest-benchmark reads a bare number as seconds and accepts only whole
# percentages, so require an integer followed by %.
threshold="${BENCH_FAIL_THRESHOLD:-20%}"
if [[ ! "$threshold" =~ ^[0-9]+%$ ]]; then
  echo "BENCH_FAIL_THRESHOLD must be a whole-number percentage such as 20%, got '$threshold'." >&2
  exit 2
fi

# Interpreter for both venvs. Under `hatch run bench:compare` this is the bench
# env's Python, whose version is set in pyproject.toml. PYTHON overrides it.
python="${PYTHON:-python3}"

# --- Temp space and cleanup --------------------------------------------------

# Everything temporary (worktree, venvs, results unless OUT_DIR is given) lives
# under $work and is removed on exit, including after Ctrl-C.
work="$(mktemp -d)"
base="$work/base"
# pytest-benchmark shows its storage path relative to the current directory
# and crashes if it can't, so keep storage inside the checkout the PR run uses.
storage_dir="$repo/.benchmarks/compare-$$"
cleanup() {
  git -C "$repo" worktree remove --force "$base" 2>/dev/null || true
  rm -rf "$work" "$storage_dir"
  rmdir "$repo/.benchmarks" 2>/dev/null || true  # only if now empty
}
trap cleanup EXIT
trap 'exit 130' INT   # Ctrl-C: exit, which runs cleanup
trap 'exit 143' TERM
git -C "$repo" worktree prune  # forget worktrees from runs that were killed

out="${2:-$work/results}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
rm -rf "$out"/baseline.* "$out"/pr.* "$out/report.md"
storage="file://$storage_dir"  # new per run, so the baseline is always run 0001

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

# Exit codes are captured instead of stopping the script, so both runs happen
# and the results are always written.
set +e

# 1. Baseline: run in the worktree and save the results as run 0001.
#    Benches that can't run here (new or renamed code) skip via the require
#    fixture in bench/conftest.py. --benchmark-quiet: its table would repeat
#    the baseline rows of the PR run's comparison table.
echo "== Baseline run: $base_ref =="
(cd "$base" && PYFXA_BENCH_BASELINE=1 "$work/base-venv/bin/python" -m pytest bench \
  --benchmark-only --benchmark-quiet -rsfE -p no:cacheprovider \
  --benchmark-storage="$storage" --benchmark-save=baseline \
  --benchmark-json="$out/baseline.json") 2>&1 | tee "$out/baseline.txt"
base_status=${PIPESTATUS[0]}

# 2. Current tree: run here, compare each median with run 0001, and fail above
#    the threshold. A bench with no baseline is shown but not compared. Its
#    table lists each bench twice: (0001_baseline) and (NOW), the PR.
echo
echo "== PR run, compared with the baseline =="
(cd "$repo" && "$work/pr-venv/bin/python" -m pytest bench \
  --benchmark-only -rsfE -p no:cacheprovider \
  --benchmark-storage="$storage" --benchmark-compare=0001 \
  --benchmark-compare-fail="median:$threshold" \
  --benchmark-columns=min,median,max,rounds \
  --benchmark-sort=name \
  --benchmark-json="$out/pr.json" --junitxml="$out/pr.xml") 2>&1 | tee "$out/pr.txt"
status=${PIPESTATUS[0]}

set -e

# --- Report ------------------------------------------------------------------

# % change table (bench/report.py). Reporting only: a problem here must not
# change the exit status.
"$work/pr-venv/bin/python" "$repo/bench/report.py" "$out" "$threshold" > "$out/report.md" ||
  echo "Could not build the % change table; see pr.txt." > "$out/report.md"
echo
echo "== Summary =="
cat "$out/report.md"
echo

# Baseline failures don't fail the job: those benches just have no baseline.
if [ "$base_status" -ne 0 ]; then
  echo "::warning::Some benchmarks failed on the baseline; they have no baseline."
fi
if [ -n "${2:-}" ]; then
  echo "Results: $out"
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  echo "::notice::Results: $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
fi

# The current tree's result decides: regression or failed bench -> non-zero.
exit "$status"
