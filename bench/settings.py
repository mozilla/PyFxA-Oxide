"""Settings from bench/settings.env: `from bench.settings import SETTINGS`.

Percentages such as 20% are read as whole numbers (20).
"""

from dataclasses import dataclass, fields
from pathlib import Path

_values = {}
for _line in Path(__file__).with_name("settings.env").read_text().splitlines():
    _key, _, _value = _line.partition("#")[0].partition("=")  # values never contain '#'
    if _value.strip():
        _values[_key.strip()] = _value.strip()


@dataclass(frozen=True)
class Settings:
    # The check's defaults (compare.sh reads them from the file too).
    BENCH_FAIL_THRESHOLD: int  # %
    BENCH_RUNS: int
    # What the benchmarks measure.
    ROUNDS: int
    MISS_TOKENS: int
    CACHE_SIZE: int
    BATCH_SIZE: int
    EXPIRED_ENTRIES: int


SETTINGS = Settings(**{f.name: int(_values[f.name].removesuffix("%")) for f in fields(Settings)})
