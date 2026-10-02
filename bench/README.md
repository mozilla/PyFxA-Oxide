# Benchmarks

These benchmarks time PyFxA's token verification. On every pull request, 
CI runs them against the base branch and the PR
and fails if the PR makes something noticeably slower. There are no network
calls: HTTP is mocked, and the fake server address (`benchmark.invalid`) can
never resolve.

## Quick start

With [Hatch](https://hatch.pypa.io/) installed, from the repo root:

```bash
make bench            # run the benchmarks once
make bench-compare    # compare your working tree with main, the way CI does
```

`make bench-compare` options:

| Option | Meaning | Default |
|---|---|---|
| `BASE=origin/main` | branch to compare against | `main` |
| `THRESHOLD=10%` | allowed slowdown before the check fails | `20%` |
| `RUNS=3` | runs per side | `5` |
| `KEEP_RESULTS=1` | keep results in `.benchmarks/compare/<time>-<pid>/` | results deleted |

In CI, set the `BENCH_FAIL_THRESHOLD` and `BENCH_RUNS` repository variables
(Settings → Secrets and variables → Actions → Variables) to change the same
settings.

All settings, these defaults and what the benchmarks measure (rounds, batch
sizes, cache sizes), are in `bench/settings.env`.

## How the CI check works

1. The base branch is checked out next to the PR, and **both sides run the PR's
   `bench/` files**, so the only difference is the library code.
2. Each side runs **`RUNS` times (default 5), alternating** base and PR. For each benchmark,
   the check takes the **median** (middle value) of each side's results, so
   one unusually slow or fast run can't decide the outcome.
3. If a PR median is more than **THRESHOLD (default 20%) slower** than the base median, the check
   fails.

The table in the job summary shows each benchmark's result:

- ✅ **ok**: within the threshold
- ❌ **over N%**: slower than the threshold; the job fails
- ⚠️ **failed**: the benchmark errored; the job fails. Some benchmarks fail on
  purpose when the code takes a slower path (see "Guards" below).
- 🆕 **no baseline**: the benchmark is new, or the base branch doesn't have the
  code it uses yet, so it isn't compared

Above the table, ⚠️ **noisy** warns that a benchmark's runs on one side
differ by more than `BENCH_NOISE_WARN` (15%), ignoring the fastest and slowest
run. Its result is less reliable, but the job doesn't fail. If a new benchmark
gets this warning, see [`experiments/README.md`](experiments/README.md).

Times in the table are **per operation**, e.g. one token verification or one
cache lookup, so rows can be compared with each other. Some benchmarks time a
batch of operations together (see below); the table divides those by the
batch size. The % change is the same either way. pytest-benchmark's own output
in the job log is **not** divided for the cache-miss and cache get/set
benchmarks: there, they show the time for the whole round, e.g. ~700 µs for 10
verifications.

Code: `compare.sh` runs both sides and records the results, `check.py`
decides whether the PR passes (all the rules are in one place, and its exit
status is the job's), `report.py` turns that result into the table (if it
fails, the job only warns), and `.github/workflows/bench.yml` runs it on
every PR.

## What each benchmark measures

Times are typical on a GitHub CI runner, for **one call**, as in the job
summary's table. "Calls per round" is how many calls are timed together in
each round. The cache-miss benchmarks time 10 verifications together so
slowdowns that hit only some calls still show; the faster benchmarks time 20
or 100 calls together because one is too short to time accurately; see
[Why the benchmarks are built this way](#why-the-benchmarks-are-built-this-way).

| Benchmark | One call | Calls per round | Time per call |
|---|---|---:|---:|
| `verify_supplied_jwks_cache_miss` | Verify a new token with the keys already given to the client | 10 | ~70 µs |
| `verify_supplied_jwks_cache_miss_pyjwt_fallback` | The same, with the fast decoder forced to fail, so it uses the slower PyJWT | 10 | ~144 µs |
| `verify_mocked_jwks_cache_miss` | Verify a new token, fetching the keys over (mocked) HTTP: the default when the client isn't given `jwks` | 10 | ~348 µs |
| `verify_reused_client_mocked_jwks_cache_miss` | The same with one long-lived client, as a long-running service keeps | 10 | ~344 µs |
| `verify_second_key_cache_miss` | Verify a token signed by the second of two keys, as during an FxA key rotation | 10 | ~188 µs |
| `verify_cache_hit` | Verify a token that's already in the cache (most requests) | 100 | ~4.4 µs |
| `decode_jwtoxide` | Decode a token with jwtoxide (Rust) only | 20 | ~56 µs |
| `decode_pyjwt_fallback` | Decode after jwtoxide fails, with PyJWT | 20 | ~115 µs |
| `memory_cache_get_many_live_entries` | Look up a key in a 10,000-entry cache | 100 | ~0.3 µs |
| `memory_cache_set_many_live_entries` | Store a key in a 10,000-entry cache | 100 | ~0.4 µs |
| `memory_cache_purge_expired_entries` | A lookup that first clears out 1,000 expired entries | 1 | ~119 µs |

## Why the benchmarks are built this way

- **Fixed test keys and token contents** (`keys.json`). With random keys, the
  base run and the PR run sometimes did different amounts of work: with about
  1 key pair in 12, checking the wrong key finished early and was about 40%
  faster. Fixed keys mean both sides always do identical work. If you
  regenerate `keys.json`, keep `other_key`'s modulus (`n`) larger than
  `signing_key`'s, so every token takes the normal, full check.
- **10 different tokens per round in the cache-miss benchmarks.** If a
  slowdown only affects some calls, say 3 in 10, timing one call per round
  misses it, because most rounds get a normal call and the median ignores the
  rest. Timing 10 calls together puts some slow calls into every round.
  Why 10: if 30% of calls are slow, a 10-call round misses all of them only
  ~3% of the time (~34% with 3 calls), while a round still takes only
  ~0.7–3.5 ms on CI. In our tests, this caught such a slowdown 100% of the time,
  against 0.5% with 1 call per round.
- **Many calls per round for very fast operations** (100 cache operations,
  100 cache hits, 20 decodes). A single ~0.1 µs call is too short for the
  clock to time accurately.
- **The median, not the average.** An occasional pause (for example Python's
  garbage collector) can make one round much slower. The median ignores it.
- **Guards.** Some benchmarks replace code that *shouldn't* run with a
  stand-in that raises an error: PyJWT's decoder where jwtoxide should do
  the work, and the decoder where the cache should answer. The mocked-HTTP
  benchmarks also fail on any request other than the expected `GET /jwks`.
  If a PR makes the code take one of those paths, the benchmark fails
  outright (⚠️) with a clear message, whatever the threshold.
- **50 rounds per benchmark.** In our tests, the variation between whole runs
  was 2–6× larger than the variation more rounds could reduce, so more rounds
  wouldn't make the check more reliable.

## Adding a benchmark

- Put it in `bench/`, with any helpers and mocks it needs. Don't import from
  `fxa/tests/`.
- Do setup in fixtures or `benchmark.pedantic(setup=...)`, never in the timed
  call.
- For cache misses, give each round an empty cache, e.g. a fresh `Client`.
- Get the code under test through the `require` fixture, not a top-level
  import:

  ```python
  def test_new_thing(benchmark, require):
      Client = require("fxa.oauth", "Client", has=["new_method"])
      ...
  ```

  If the base branch doesn't have that code yet, the benchmark is skipped there
  ("no baseline") instead of failing.
- Put any size or count that defines what the benchmark measures in
  `bench/settings.env`, add it as a field of `Settings` in
  `bench/settings.py`, and use it as `SETTINGS.NAME`.
- If one timed call covers several operations (a batch), set
  `benchmark.extra_info["operations_per_round"]` to the batch size, so the
  report shows time per operation.
- If the benchmark should never make an HTTP call or use a fallback, add a
  guard, as the existing benchmarks do.

## Choosing the threshold: the experiments

We tested the check two ways:

1. **Unchanged code, hundreds of times.** Every time the check would have
   failed on unchanged code is a **false alarm**.
2. **Deliberately slowed code.** We added known slowdowns and checked whether
   the check caught them. Every time it passed is a **missed slowdown**.

The first tests showed problems, all fixed in the design above: random test
keys caused false alarms, a single slow run could fail a PR, and slowdowns
that only affect some calls were invisible.

The tools, and how to rerun them, are in
[`experiments/README.md`](experiments/README.md).

### Results on a GitHub CI runner

**How these were measured:** 200 runs of unchanged code on one GitHub runner
(AMD EPYC, 4 CPUs), each with **50 rounds per benchmark**, replayed through the
check 20,000 times. Missed slowdowns were measured by making the PR side of
those runs uniformly slower by a known amount. "Band" is the range the true
rate probably falls in, given the limited number of runs.

- **False alarm:** the check fails unchanged code.
- **Missed slowdown:** the check passes a real slowdown of the size shown.
  "T" is the threshold; "weakest" is the benchmark that missed most often
  (`memory_cache_purge_expired_entries` in every row).

#### 5 runs per side (the default)

| Threshold | False alarms (band) | Missed: T+5% slowdown | Missed: T+10% (weakest) | Missed: 2× |
|---:|---:|---:|---:|---:|
| 10% | 0.04% (0.00–0.18%), about 1 check in 2,500 | 0.10% | 0.01% (0.03%) | 0% |
| **20%** | **0.00% (0.00–0.03%)**, none in 20,000 | **0.14%** | **0.01% (0.09%)** | **0%** |
| 30% | 0.00% (0.00–0.00%) | 0.20% | 0.02% (0.16%) | 0% |
| 40% | 0.00% (0.00–0.00%) | 0.28% | 0.02% (0.20%) | 0% |

#### 3 runs per side, for comparison

| Threshold | False alarms (band) | Missed: T+5% slowdown | Missed: T+10% (weakest) | Missed: 2× |
|---:|---:|---:|---:|---:|
| 10% | 0.33% (0.10–0.95%), about 1 check in 300 | 0.44% | 0.06% (0.46%) | 0% |
| 20% | 0.05% (0.00–0.38%), about 1 check in 2,000 | 0.56% | 0.10% (0.78%) | 0% |
| 30% | 0.00% (0.00–0.12%) | 0.73% | 0.12% (0.96%) | 0% |
| 40% | 0.00% (0.00–0.07%) | 0.94% | 0.14% (1.06%) | 0% |

5 runs per side cuts both false alarms and missed slowdowns about 4–10×, for
about 10 seconds more per check (the CI job takes ~58 s with 3, ~68 s with 5).

#### What the 50 rounds contribute

The small differences between rounds within a run shift that run's median by
only 0.2–0.6% at 50 rounds (worst: `verify_supplied_jwks_cache_miss_pyjwt_fallback`).
That's already included in the rates above, and it's much smaller than the
differences between whole runs (0.9–4%), which the runs per side are there to
handle. Doubling the rounds would shrink it to at most 0.4% and wouldn't
measurably change the rates, so we kept 50.

#### What this means

- **A slowdown right at the threshold is caught about half the time.** Timings
  wobble a little either side of the true value, so to be caught reliably, a
  slowdown needs to be about 5–10 points above the threshold.
- **Real code changes behaved like the simulated ones** (tested locally). A
  steady +25% slowdown was caught 100% of the time at 10%, 99% at 20% and 0%
  at 30%, as predicted. A PyJWT fallback on 30% of decodes was caught 100% of
  the time, by the guards.

**We chose 20%, with 5 runs per side.** False alarms essentially never happen,
and it catches 99.9% or more of slowdowns of 30% or more, and every 2× change.

### Limits

- The check compares each PR with its base branch. Several small slowdowns,
  each just under 20%, could add up over time without failing any single PR.
- The CI results come from one runner type (AMD EPYC, 4 CPUs). Rerun the
  experiments ([`experiments/README.md`](experiments/README.md)) if the
  runners, benchmarks or dependencies change a lot.
