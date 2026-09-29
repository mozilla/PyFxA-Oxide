"""Shared benchmark fixtures; setup here is never part of a timed call."""

import importlib
import json
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

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


@pytest.fixture(scope="session")
def signed_token_and_jwk():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issued_at = int(time.time())
    token = jwt.encode(
        {
            "sub": "benchmark-user",
            "client_id": "benchmark-client",
            "scope": "profile:uid",
            "iat": issued_at,
            "exp": issued_at + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"typ": "at+jwt"},
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return token, jwk


@pytest.fixture(scope="session")
def other_jwk():
    """Public JWK of an unrelated key, e.g. the other key during a rotation."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
