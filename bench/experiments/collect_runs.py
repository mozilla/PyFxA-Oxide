"""Collect benchmark runs for replay.py, each in a fresh pytest process (as one
side of bench/compare.sh).

Usage (from the repo root, with Hatch installed):

    python3 bench/experiments/collect_runs.py aa OUT_DIR [--runs 200]
    python3 bench/experiments/collect_runs.py ab BASE_DIR PR_DIR OUT_DIR [--runs 60]

aa runs this tree's code over and over (unchanged code: false alarms) into
OUT_DIR/run-N.json. ab alternates two checkouts, e.g. this tree and a worktree
with a deliberate slowdown, into OUT_DIR/base/ and OUT_DIR/pr/ (for replay.py
--injected); give both checkouts the same bench/ files. --pytest-args passes
extra options, e.g. "--benchmark-disable-gc". A failed run's output is saved
next to its results as run-N.log.
"""

import argparse
import shlex
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def bench_python():
    """The Hatch bench env's Python (Python 3.12, with the pinned tools)."""
    env = subprocess.run(
        ["hatch", "env", "find", "bench"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()
    python = Path(env) / "bin" / "python"
    if not python.exists():
        subprocess.run(["hatch", "env", "create", "bench"], cwd=REPO, check=True)
    return python


def run_suite(python, checkout, out, number, pytest_args):
    """Run the suite once in checkout, saving out/run-NNN.json; print progress."""
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = subprocess.run(
        [str(python), "-m", "pytest", "bench", "--benchmark-only", "-q", "-p", "no:cacheprovider",
         f"--benchmark-json={out / f'run-{number:03d}.json'}", *shlex.split(pytest_args)],
        cwd=checkout, capture_output=True, text=True,
    )
    status = "ok" if result.returncode == 0 else f"exit {result.returncode}"
    print(f"  {out.name}/run-{number:03d}  {time.time() - started:5.1f}s  {status}", flush=True)
    if result.returncode != 0:
        (out / f"run-{number:03d}.log").write_text(result.stdout + result.stderr)


def aa(args):
    python = bench_python()
    for number in range(1, args.runs + 1):
        run_suite(python, REPO, Path(args.out), number, args.pytest_args)


def ab(args):
    python = bench_python()
    out = Path(args.out)
    for number in range(1, args.runs + 1):
        run_suite(python, Path(args.base), out / "base", number, args.pytest_args)
        run_suite(python, Path(args.pr), out / "pr", number, args.pytest_args)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    a = sub.add_parser("aa", help="run this tree's code RUNS times")
    a.add_argument("out")
    a.add_argument("--runs", type=int, default=200)
    a.set_defaults(func=aa)
    b = sub.add_parser("ab", help="alternate two checkouts, RUNS times each")
    b.add_argument("base")
    b.add_argument("pr")
    b.add_argument("out")
    b.add_argument("--runs", type=int, default=60)
    b.set_defaults(func=ab)
    for p in (a, b):
        p.add_argument("--pytest-args", default="", help='extra pytest options, e.g. "--benchmark-disable-gc"')
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
