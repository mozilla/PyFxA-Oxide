"""Measure the paths that MLPA's FxA auth calls through PyFxA."""

import json
from unittest.mock import patch

import responses

SCOPE = "profile:uid"
ROUNDS = 50
VERIFY_KWARGS = {"scope": SCOPE, "include_verification_source": True}
JWKS_URL = "https://benchmark.invalid/v1/jwks"


def verify_once(client, token):
    return client.verify_token(token, **VERIFY_KWARGS)


def verify_each(client, tokens):
    """Verify several distinct tokens: one cache miss each. Returns the last profile."""
    for token in tokens:
        profile = client.verify_token(token, **VERIFY_KWARGS)
    return profile


def assert_each_missed(client, tokens):
    """Untimed check that every token in a round is verified, not served from cache."""
    for token in tokens:
        assert_profile(verify_once(client, token), "local")


def assert_profile(profile, source):
    assert profile["user"] == "benchmark-user"
    assert profile["client_id"] == "benchmark-client"
    assert profile["scope"] == [SCOPE]
    assert profile["verification_source"] == source


# The four cache-miss benchmarks verify MISS_TOKENS distinct tokens per round
# (bench/conftest.py), so their times are per MISS_TOKENS verifications. One
# token per round hid changes that only some calls hit: the median of 50
# one-call rounds ignores anything in fewer than half of them.


def test_verify_supplied_jwks_cache_miss(benchmark, signed_tokens_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid", jwks=[jwk]), tokens), {}

    benchmark.extra_info["verifications_per_round"] = len(tokens)
    # Fail rather than silently measure the PyJWT fallback.
    with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
        assert_each_missed(*fresh_client()[0])
        result = benchmark.pedantic(verify_each, setup=fresh_client, rounds=ROUNDS)

    assert_profile(result, "local")


def test_verify_supplied_jwks_cache_miss_pyjwt_fallback(benchmark, signed_tokens_and_jwk, require):
    # test_verify_supplied_jwks_cache_miss with jwtoxide forced to fail, so every
    # miss uses PyJWT: the cost of a silent fallback (~2.6x a jwtoxide miss).
    # The other benchmarks fail if a real fallback happens; this one times it.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid", jwks=[jwk]), tokens), {}

    benchmark.extra_info["verifications_per_round"] = len(tokens)
    with patch("fxa.oauth.decode", side_effect=ValueError("forced fallback")) as rust:
        result = benchmark.pedantic(verify_each, setup=fresh_client, rounds=ROUNDS)

    assert rust.call_count >= ROUNDS * len(tokens)  # every miss went through the fallback
    assert_profile(result, "local")


def test_verify_mocked_jwks_cache_miss(benchmark, signed_tokens_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid"), tokens), {}

    benchmark.extra_info["verifications_per_round"] = len(tokens)
    # Only this GET is mocked; any other request raises.
    with responses.RequestsMock() as http:
        http.add(responses.GET, JWKS_URL, json={"keys": [jwk]}, status=200)
        with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
            result = benchmark.pedantic(verify_each, setup=fresh_client, rounds=ROUNDS)
        # One /jwks fetch per verification: every token missed.
        assert len(http.calls) == ROUNDS * len(tokens)

    assert_profile(result, "local")


def test_verify_reused_client_mocked_jwks_cache_miss(benchmark, signed_tokens_and_jwk, require):
    # How MLPA runs: one long-lived client verifying many tokens. Only the
    # per-token result cache is emptied each round, so anything the client
    # keeps (such as a JWKS cache) carries over, as in production.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    MemoryCache = require("fxa.cache", "MemoryCache")
    tokens, jwk = signed_tokens_and_jwk
    client = Client(server_url="https://benchmark.invalid")

    def empty_result_cache():
        client.cache = MemoryCache()
        return (client, tokens), {}

    benchmark.extra_info["verifications_per_round"] = len(tokens)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        http.add(responses.GET, JWKS_URL, json={"keys": [jwk]}, status=200)
        assert_each_missed(*empty_result_cache()[0])
        result = benchmark.pedantic(verify_each, setup=empty_result_cache, rounds=ROUNDS)

    assert_profile(result, "local")


def test_verify_second_key_cache_miss(benchmark, signed_tokens_and_jwk, other_jwk, require):
    # The tokens match the second of two keys, as during an FxA key rotation,
    # so the first key is tried and rejected on every call.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid", jwks=[other_jwk, jwk]), tokens), {}

    benchmark.extra_info["verifications_per_round"] = len(tokens)
    assert_each_missed(*fresh_client()[0])
    result = benchmark.pedantic(verify_each, setup=fresh_client, rounds=ROUNDS)

    assert_profile(result, "local")


def test_verify_cache_hit(benchmark, signed_token_and_jwk, require):
    # _verify_jwt_token is patched below as a guard.
    Client = require("fxa.oauth", "Client", has=["verify_token", "_verify_jwt_token"])
    token, jwk = signed_token_and_jwk
    client = Client(server_url="https://benchmark.invalid", jwks=[jwk])
    verify_once(client, token)  # warm the cache

    with patch.object(client, "_verify_jwt_token", side_effect=AssertionError("cache miss")):
        result = benchmark.pedantic(
            client.verify_token,
            args=(token,),
            kwargs=VERIFY_KWARGS,
            rounds=ROUNDS,
            iterations=100,
        )

    assert_profile(result, "cached")


def test_decode_jwtoxide(benchmark, signed_token_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["_verify_jwt_token"])
    token, jwk = signed_token_and_jwk
    client = Client(server_url="https://benchmark.invalid", jwks=[jwk])
    key = json.dumps(jwk)

    with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
        result = benchmark.pedantic(
            client._verify_jwt_token, args=(key, token), rounds=ROUNDS, iterations=20
        )

    assert result["user"] == "benchmark-user"


def test_decode_pyjwt_fallback(benchmark, signed_token_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["_verify_jwt_token"])
    token, jwk = signed_token_and_jwk
    client = Client(server_url="https://benchmark.invalid", jwks=[jwk])
    key = json.dumps(jwk)

    # Force the same exception route used when jwtoxide cannot decode a token.
    with patch("fxa.oauth.decode", side_effect=ValueError("forced fallback")) as rust:
        result = benchmark.pedantic(
            client._verify_jwt_token, args=(key, token), rounds=ROUNDS, iterations=20
        )

    assert rust.call_count >= ROUNDS * 20  # every timed call went through the fallback
    assert result["user"] == "benchmark-user"
