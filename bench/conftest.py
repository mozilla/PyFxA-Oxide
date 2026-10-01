"""Shared benchmark fixtures."""

import importlib
import json
import os
from pathlib import Path

import jwt
import pytest

from bench.settings import SETTINGS

# Fixed test keys keep the token inputs identical across base and PR runs.
KEYS = json.loads((Path(__file__).parent / "keys.json").read_text())
# Fixed claims, so the token (and its RS256 signature, which is deterministic)
# is byte-for-byte the same in every run. exp is far enough out never to expire.
ISSUED_AT = 1_700_000_000
EXPIRES_AT = 4_102_444_800  # 2100-01-01
# Set by compare.sh when running the PR's benchmarks against the base's code.
BASELINE_RUN = os.environ.get("PYFXA_BENCH_BASELINE") == "1"


def pytest_benchmark_update_machine_info(config, machine_info):
    """pytest-benchmark hook: drop the CPU clock speed from machine info.

    It varies from moment to moment, so left in, it makes runs on the same
    machine look like different machines (a machine-info warning).
    """
    for key in ("hz_actual", "hz_actual_friendly", "hz_advertised", "hz_advertised_friendly"):
        machine_info.get("cpu", {}).pop(key, None)


@pytest.fixture(scope="session")
def require():
    """Get code under test, e.g. ``require("fxa.oauth", "Client", has=["verify_token"])``.

    If the module, name or any ``has`` attribute is missing, skip the benchmark
    on the baseline run ("no baseline": the PR added or renamed it), and fail
    it on the PR run.
    """

    def require(module_name, name, has=()):
        try:
            obj = getattr(importlib.import_module(module_name), name)
            for attr in has:
                getattr(obj, attr)
            return obj
        except (ImportError, AttributeError) as exc:
            if BASELINE_RUN:
                pytest.skip(f"no baseline: {exc}")
            raise

    return require


@pytest.fixture(scope="session")
def signed_tokens_and_jwk():
    """MISS_TOKENS tokens that differ only in their jti claim, and the public JWK."""
    private_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(KEYS["signing_key"]))
    tokens = tuple(
        jwt.encode(
            {
                "sub": "benchmark-user",
                "client_id": "benchmark-client",
                "scope": "profile:uid",
                "iat": ISSUED_AT,
                "exp": EXPIRES_AT,
                "jti": f"benchmark-{index}",
            },
            private_key,
            algorithm="RS256",
            headers={"typ": "at+jwt"},
        )
        for index in range(SETTINGS.MISS_TOKENS)
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return tokens, jwk


@pytest.fixture(scope="session")
def signed_token_and_jwk(signed_tokens_and_jwk):
    """One token and the public JWK, for benchmarks that time a single token."""
    tokens, jwk = signed_tokens_and_jwk
    return tokens[0], jwk


@pytest.fixture(scope="session")
def other_jwk():
    """Public JWK of an unrelated key, as during key rotation."""
    return KEYS["other_key"]
