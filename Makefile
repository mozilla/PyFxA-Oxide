.PHONY: bench bench-compare

BASE ?= main
# Allowed median slowdown, e.g. make bench-compare THRESHOLD=10%. Empty uses
# bench/compare.sh's default; BENCH_FAIL_THRESHOLD in the environment also works.
THRESHOLD ?= $(BENCH_FAIL_THRESHOLD)
# Folder to keep results in, e.g. OUT=.benchmarks/compare. Empty deletes them.
OUT ?=

bench:
	hatch run bench:run

# Compare the working tree with BASE the way CI does, e.g. make bench-compare BASE=origin/main
bench-compare:
	BENCH_FAIL_THRESHOLD="$(THRESHOLD)" hatch run bench:compare $(BASE) $(OUT)
