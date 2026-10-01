#!/usr/bin/env bash
# Compare benchmarks: the current working tree against a base git ref.
#
# Usage: hatch run bench:compare [BASE_REF] [OUT_DIR]   (or: make bench-compare)
#
# Both sides run this tree's bench/ files, each in its own venv. The baseline
# runs in a worktree of BASE_REF (default: main), because the benches import
# `fxa` from the directory they run in. Each side runs BENCH_RUNS times
# (default 5), alternating base and PR.
#
# bench/check.py decides whether the PR passes (see its docstring for the
# rules); the script exits with its status, 0 if the PR passes. bench/report.py
# then writes report.md from check.py's result; if that fails, it only warns.
#
# Results are deleted at the end, unless KEEP_RESULTS=1 (kept in
# .benchmarks/compare/<time>-<pid>/) or OUT_DIR is given (used by CI).
set -euo pipefail

section() { printf '\n======== %s ========\n' "$1"; }  # blank line, then a heading

# --- Settings ----------------------------------------------------------------

base_ref="${1:-main}"
repo="$(git rev-parse --show-toplevel)"

# Defaults from this tree's bench/settings.env; values already set in the
# environment win, so remember them before sourcing the file.
threshold="${BENCH_FAIL_THRESHOLD:-}"
runs="${BENCH_RUNS:-}"
source "$repo/bench/settings.env"
threshold="${threshold:-$BENCH_FAIL_THRESHOLD}"
runs="${runs:-$BENCH_RUNS}"

# Require the % sign, so a bare number can't be mistaken for a time.
if [[ ! "$threshold" =~ ^[0-9]+%$ ]]; then
  echo "BENCH_FAIL_THRESHOLD must be a whole-number percentage such as 20%, got '$threshold'." >&2
  exit 2
fi

# Runs per side; the check compares their medians (see bench/README.md).
if [[ ! "$runs" =~ ^[1-9][0-9]*$ ]]; then
  echo "BENCH_RUNS must be a positive whole number, got '$runs'." >&2
  exit 2
fi

# Hatch supplies Python for both venvs; set PYTHON to override it.
python="${PYTHON:-python3}"

# Benchmark tooling (pytest, pytest-benchmark, responses): the bench env's pins
# in this tree's pyproject.toml, installed on both sides so both use the same
# versions. Each side's library dependencies come from its own pyproject.toml.
read_pins='import sys, tomllib
with open(sys.argv[1], "rb") as f:
    print("\n".join(tomllib.load(f)["tool"]["hatch"]["envs"]["bench"]["dependencies"]))'
if ! pins="$("$python" -c "$read_pins" "$repo/pyproject.toml")" || [ -z "$pins" ]; then
  echo "Could not read [tool.hatch.envs.bench] dependencies from pyproject.toml" \
    "(needs Python 3.11+; PYTHON is $python)." >&2
  exit 2
fi
bench_deps=()
while IFS= read -r pin; do bench_deps+=("$pin"); done <<< "$pins"

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
  # A reused OUT_DIR may hold an earlier run's files, which check.py would
  # also read; remove them. The other two choices below are always new folders.
  rm -rf "$out"/baseline-* "$out"/pr-* "$out/result.json" "$out/report.md"
elif [ "${KEEP_RESULTS:-}" = "1" ]; then
  # A new folder per run, so earlier kept runs are never overwritten.
  out="$repo/.benchmarks/compare/$(date +%Y%m%d-%H%M%S)-$$"
else
  out="$work/results"
  keep=false
fi
mkdir -p "$out"
out="$(cd "$out" && pwd)"

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
    "$2" "${bench_deps[@]}"
}
install "$work/base-venv" "$base"
install "$work/pr-venv" "$repo"

# --- Run the benchmarks ------------------------------------------------------

# Record each run's exit code instead of stopping, so every run happens;
# check.py decides what a failed run means.
set +e

# Alternate base and PR, so a machine that speeds up or slows down during the
# job affects both sides alike. Benches that can't run on the base (new or
# renamed code) skip there via the require fixture in bench/conftest.py.
for i in $(seq 1 "$runs"); do
  section "Baseline run $i/$runs: $base_ref"
  (cd "$base" && PYFXA_BENCH_BASELINE=1 "$work/base-venv/bin/python" -m pytest bench \
    --benchmark-only -rsfE -p no:cacheprovider \
    --benchmark-columns=min,median,max,rounds --benchmark-sort=name \
    --benchmark-json="$out/baseline-$i.json") 2>&1 | tee "$out/baseline-$i.txt"
  echo "${PIPESTATUS[0]}" > "$out/baseline-$i.exit"

  section "PR run $i/$runs"
  (cd "$repo" && "$work/pr-venv/bin/python" -m pytest bench \
    --benchmark-only -rsfE -p no:cacheprovider \
    --benchmark-columns=min,median,max,rounds --benchmark-sort=name \
    --benchmark-json="$out/pr-$i.json" --junitxml="$out/pr-$i.xml") 2>&1 | tee "$out/pr-$i.txt"
  echo "${PIPESTATUS[0]}" > "$out/pr-$i.exit"
done

# --- Check and report --------------------------------------------------------

# check.py's exit status is the result; if it crashes, there's no verdict, so fail.
section "Check"
"$work/pr-venv/bin/python" "$repo/bench/check.py" "$out" "$threshold"
status=$?
if [ ! -s "$out/result.json" ] && [ "$status" -eq 0 ]; then
  status=1
fi
if [ "$status" -eq 0 ]; then verdict="passed"; else verdict="failed"; fi

# report.py only formats result.json into report.md, so if it fails, just warn.
section "Report"
if ! "$work/pr-venv/bin/python" "$repo/bench/report.py" "$out"; then
  msg="Could not build the benchmark report; the check $verdict (see above)."
  if [ "${GITHUB_ACTIONS:-}" = "true" ]; then echo "::warning::$msg"; else echo "warning: $msg" >&2; fi
  echo "**Benchmark check $verdict.** $msg" > "$out/report.md"
fi
set -e

if $keep; then
  echo "Results: $out"
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  echo "::notice::Results: $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
fi
exit "$status"
