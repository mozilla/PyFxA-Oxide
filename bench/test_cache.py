"""Measure PyFxA's process-local cache with production-sized entry counts."""

import time

ROUNDS = 50
CACHE_SIZE = 10_000
BATCH_SIZE = 100
EXPIRED_ENTRIES = 1_000


def populated_cache(MemoryCache):
    cache = MemoryCache(ttl=300)
    now = time.time()
    for index in range(CACHE_SIZE):
        cache.set(f"key-{index}", "value", now=now)
    return cache


def get_batch(cache, keys):
    for key in keys:
        cache.get(key)


def set_batch(cache, keys):
    for key in keys:
        cache.set(key, "new value")


def test_memory_cache_get_many_live_entries(benchmark, require):
    MemoryCache = require("fxa.cache", "MemoryCache", has=["get"])
    cache = populated_cache(MemoryCache)
    keys = tuple(f"key-{index}" for index in range(BATCH_SIZE))
    benchmark.extra_info["operations_per_round"] = BATCH_SIZE  # report.py shows per get
    benchmark.pedantic(get_batch, args=(cache, keys), rounds=ROUNDS, iterations=10)
    assert cache.get(keys[-1]) == "value"


def test_memory_cache_set_many_live_entries(benchmark, require):
    MemoryCache = require("fxa.cache", "MemoryCache", has=["set"])
    keys = tuple(f"new-key-{index}" for index in range(BATCH_SIZE))

    def fresh_cache():
        return (populated_cache(MemoryCache), keys), {}

    benchmark.extra_info["operations_per_round"] = BATCH_SIZE  # report.py shows per set
    benchmark.pedantic(set_batch, setup=fresh_cache, rounds=ROUNDS)


def test_memory_cache_purge_expired_entries(benchmark, require):
    MemoryCache = require("fxa.cache", "MemoryCache", has=["get"])

    def expired_cache():
        cache = MemoryCache(ttl=300)
        for index in range(EXPIRED_ENTRIES):
            cache.set(f"old-{index}", "value", now=0)
        return (cache, "absent"), {"now": 301}

    # A get at now=301 purges every expired entry.
    cache, key = expired_cache()[0]
    assert cache.get(key, now=301) is None
    assert not cache.expiry_queue
    result = benchmark.pedantic(MemoryCache.get, setup=expired_cache, rounds=ROUNDS)
    assert result is None
