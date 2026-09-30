"""Shared benchmark fixtures; setup here is never part of a timed call."""

import base64
import importlib
import json
import os
from pathlib import Path

import jwt
import pytest

# Test-only keys, fixed so the base and PR runs sign and verify identical
# tokens. Random keys per run made results depend on the key pair: see
# other_jwk.
KEYS = json.loads((Path(__file__).parent / "keys.json").read_text())
# Fixed claims, so the token (and its RS256 signature, which is deterministic)
# is byte-for-byte the same in every run. exp is far enough out never to expire.
ISSUED_AT = 1_700_000_000
EXPIRES_AT = 4_102_444_800  # 2100-01-01

def pytest_benchmark_update_machine_info(config, machine_info):
    """pytest-benchmark hook: drop the CPU's clock speed from machine info.

    Clock speed varies from moment to moment on the same machine, so it doesn't
    identify the machine; the fields that do (CPU model, cores, OS, Python) are
    kept. Left in, the hz_* fields make same-machine CI runs look like different
    machines, causing a machine-info difference warning.
    """
    for key in ("hz_actual", "hz_actual_friendly", "hz_advertised", "hz_advertised_friendly"):
        machine_info.get("cpu", {}).pop(key, None)


# Set by CI when running the PR's benchmarks against the base branch's code.
BASELINE_RUN = os.environ.get("PYFXA_BENCH_BASELINE") == "1"


@pytest.fixture(scope="session")
def require():
    """Return code under test, e.g. ``require("fxa.oauth", "Client", has=["verify_token"])``.

    ``has`` lists attributes the object must have, such as the methods a
    benchmark times. If anything is missing:

    - Baseline run (``PYFXA_BENCH_BASELINE=1``): skip the benchmark as "no
      baseline"; the PR added or renamed that code.
    - PR run: raise the error, so the benchmark fails.
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


# Distinct tokens for the cache-miss benchmarks, which verify each once per
# round: every call misses, and a slowdown or speed-up that only some calls hit
# still shows in every round's time, so the median sees it.
MISS_TOKENS = 10


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
        for index in range(MISS_TOKENS)
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return tokens, jwk


@pytest.fixture(scope="session")
def signed_token_and_jwk(signed_tokens_and_jwk):
    """One token and the public JWK, for benchmarks that time a single token."""
    tokens, jwk = signed_tokens_and_jwk
    return tokens[0], jwk


def _b64_int(value):
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


@pytest.fixture(scope="session")
def other_jwk():
    """Public JWK of an unrelated key, e.g. the other key during a rotation.

    A wrong key normally costs a full RSA check. But if a token's signature, as
    a number, is at least this key's modulus, jwtoxide and PyJWT reject it
    without one, about 40% faster. Every signature is below the signing key's
    modulus, so a larger modulus here keeps every token on the full check.
    """
    assert _b64_int(KEYS["other_key"]["n"]) > _b64_int(KEYS["signing_key"]["n"]), (
        "bench/keys.json: other_key's modulus must exceed signing_key's"
    )
    return KEYS["other_key"]
