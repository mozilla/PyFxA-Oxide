"""Shared benchmark fixtures; setup here is never part of a timed call."""

import importlib
import json
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

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
