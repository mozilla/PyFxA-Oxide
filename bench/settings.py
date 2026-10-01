"""The benchmark sizes from bench/settings.env: `from bench.settings import SETTINGS`."""

from dataclasses import dataclass, fields
from pathlib import Path

_values = {}
for _line in Path(__file__).with_name("settings.env").read_text().splitlines():
    _key, _, _value = _line.partition("#")[0].partition("=")  # values never contain '#'
    if _value.strip():
        _values[_key.strip()] = _value.strip()


@dataclass(frozen=True)
class Settings:
    BENCH_RUNS: int  # default runs per side (compare.sh reads it from the file too)
    ROUNDS: int
    MISS_TOKENS: int
    CACHE_SIZE: int
    BATCH_SIZE: int
    EXPIRED_ENTRIES: int


SETTINGS = Settings(**{f.name: int(_values[f.name]) for f in fields(Settings)})
