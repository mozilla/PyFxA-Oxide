==========================================================================================
PyFxA-Oxide: A fork of `PyFxA <https://github.com/mozilla/PyFxA>`_ with `jwtoxide` support
==========================================================================================

PyFxA-Oxide is a fork of the PyFxA library that instead utilizes `jwtoxide` for optimized JWT decoding.

Performance Comparison
======================
.. image:: performance.png
    :alt: PyFxA-Oxide vs PyFxA performance comparison
    :align: center

Verification benchmarks
=======================

``bench/`` has performance benchmarks for token verification. CI runs them on
every PR and compares the results with the base branch.

Run locally
-----------

With Hatch installed::

    make bench            # run the benchmarks
    make bench-compare    # compare your working tree with main, like CI

``make bench-compare`` options:

- ``BASE=origin/main``: branch to compare against (default ``main``)
- ``THRESHOLD=10%``: allowed slowdown (default 20%)
- ``RUNS=5``: runs per side (default 3)
- ``KEEP_RESULTS=1``: keep the results in ``.benchmarks/compare/<time>-<pid>/``,
  a new folder per run (deleted by default). Delete old folders when done.

If you change the bench Python version in ``pyproject.toml``, run
``hatch env remove bench`` once.

Read the results
----------------

Each side runs 3 times, alternating base and PR. The table (printed locally,
and in the CI job summary) shows each benchmark's median time on the base
branch and the PR, as the median of that side's runs, and the % change. One
unusually slow or fast run can't decide the result.

- ✅ **ok**: within the threshold
- ❌ **over N%**: slower than the threshold; fails the job
- ⚠️ **failed**: the benchmark errored on the PR; fails the job. Some fail on
  purpose when the code takes a slower fallback path.
- 🆕 **no baseline**: a new benchmark, or the base branch doesn't have the code
  it uses yet; not compared

In CI, set the threshold with the ``BENCH_FAIL_THRESHOLD`` repository variable
and the runs per side with ``BENCH_RUNS``.
