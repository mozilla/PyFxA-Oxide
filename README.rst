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

``bench/`` times the token-verification code MLPA uses: ``verify_token``
cache misses and hits, the jwtoxide and PyJWT decoders, and ``MemoryCache``.
There are no network calls.

Run locally, with Hatch installed::

    make bench                      # or: hatch run bench:run
    make bench-compare              # or: hatch run bench:compare [BASE] [OUT]

``make bench-compare`` also takes ``BASE=origin/main``, ``THRESHOLD=10%``, and
``OUT=.benchmarks/compare`` to keep the results. If you change the bench
Python version in ``pyproject.toml``, run ``hatch env remove bench`` once.

CI runs the comparison on every PR and puts a table in the job summary with
each benchmark's base and PR median, the % change, and a status: ✅ ok,
❌ over the threshold, ⚠️ failed, or 🆕 no baseline (new, or can't run on the
base yet). The job fails on ❌ or ⚠️. The threshold is 20% by default; change
it with the ``BENCH_FAIL_THRESHOLD`` repository variable. Some benchmarks fail
on purpose if jwtoxide falls back to PyJWT.

To add a benchmark, put it in ``bench/`` with any helpers and mocks, keep
setup out of the timed call, and get code under test through the ``require``
fixture::

    def test_new_thing(benchmark, require):
        Client = require("fxa.oauth", "Client", has=["new_method"])
        ...

If the base branch doesn't have that code yet, the benchmark is skipped there
("no baseline") instead of failing.
