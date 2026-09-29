.PHONY: bench bench-compare

BASE ?= main
# Allowed median slowdown, e.g. make bench-compare THRESHOLD=10%. Empty uses
# bench/compare.sh's default; BENCH_FAIL_THRESHOLD in the environment also works.
THRESHOLD ?= $(BENCH_FAIL_THRESHOLD)
# KEEP_RESULTS=1 keeps each run's results in .benchmarks/compare/<time>-<pid>/.
# Empty deletes them.
KEEP_RESULTS ?=

bench:
	hatch run bench:run

# Compare the working tree with BASE the way CI does, e.g. make bench-compare BASE=origin/main
bench-compare:
	BENCH_FAIL_THRESHOLD="$(THRESHOLD)" KEEP_RESULTS="$(KEEP_RESULTS)" hatch run bench:compare $(BASE)
