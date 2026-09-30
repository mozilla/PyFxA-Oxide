.PHONY: bench bench-compare

BASE ?= main
# Allowed median slowdown, e.g. make bench-compare THRESHOLD=10%. Empty uses
# bench/compare.sh's default; BENCH_FAIL_THRESHOLD in the environment also works.
THRESHOLD ?= $(BENCH_FAIL_THRESHOLD)
# KEEP_RESULTS=1 keeps each run's results in .benchmarks/compare/<time>-<pid>/.
# Empty deletes them.
KEEP_RESULTS ?=
# Runs per side, e.g. make bench-compare RUNS=5. Empty uses bench/compare.sh's
# default (3); BENCH_RUNS in the environment also works.
RUNS ?= $(BENCH_RUNS)

bench:
	hatch run bench:run

# Compare the working tree with BASE the way CI does, e.g. make bench-compare BASE=origin/main
bench-compare:
	BENCH_FAIL_THRESHOLD="$(THRESHOLD)" BENCH_RUNS="$(RUNS)" KEEP_RESULTS="$(KEEP_RESULTS)" hatch run bench:compare $(BASE)
