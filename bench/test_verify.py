"""Benchmark Client.verify_token and _verify_jwt_token."""

import json
from unittest.mock import patch

import responses

from bench.settings import SETTINGS

SCOPE = "profile:uid"
VERIFY_KWARGS = {"scope": SCOPE, "include_verification_source": True}
SERVER_URL = "https://benchmark.invalid"  # never resolves, so nothing reaches a network
JWKS_URL = f"{SERVER_URL}/v1/jwks"  # Client adds /v1 to the server URL


def verify_once(client, token):
    return client.verify_token(token, **VERIFY_KWARGS)


def verify_each(client, tokens):
    """Verify each token in turn; return the last profile, for the caller to check."""
    for token in tokens:
        profile = client.verify_token(token, **VERIFY_KWARGS)
    return profile


def fresh_client_each_round(Client, tokens, **client_kwargs):
    """A pedantic setup that gives every round a new Client, so its cache starts empty."""
    def setup():
        return (Client(server_url=SERVER_URL, **client_kwargs), tokens), {}
    return setup


def assert_each_missed(client, tokens):
    """Untimed check that every token in a round is verified, not served from cache."""
    for token in tokens:
        assert_profile(verify_once(client, token), "local")


def assert_profile(profile, source):
    assert profile["user"] == "benchmark-user"
    assert profile["client_id"] == "benchmark-client"
    assert profile["scope"] == [SCOPE]
    assert profile["verification_source"] == source


# The cache-miss benchmarks verify MISS_TOKENS distinct tokens per round, so a
# slowdown in only some calls still shows (see bench/README.md).


def test_verify_supplied_jwks_cache_miss(benchmark, signed_tokens_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk
    setup = fresh_client_each_round(Client, tokens, jwks=[jwk])

    benchmark.extra_info["operations_per_round"] = len(tokens)
    # Fail rather than silently measure the PyJWT fallback.
    with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
        assert_each_missed(*setup()[0])
        result = benchmark.pedantic(verify_each, setup=setup, rounds=SETTINGS.ROUNDS)

    assert_profile(result, "local")


def test_verify_supplied_jwks_cache_miss_pyjwt_fallback(benchmark, signed_tokens_and_jwk, require):
    # The benchmark above with jwtoxide forced to fail: times a silent PyJWT
    # fallback (~2.6x a jwtoxide miss), which the other benchmarks fail on.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk
    setup = fresh_client_each_round(Client, tokens, jwks=[jwk])

    benchmark.extra_info["operations_per_round"] = len(tokens)
    with patch("fxa.oauth.decode", side_effect=ValueError("forced fallback")) as jwtoxide_decode:
        result = benchmark.pedantic(verify_each, setup=setup, rounds=SETTINGS.ROUNDS)

    # Every miss tried jwtoxide, then fell back.
    assert jwtoxide_decode.call_count >= SETTINGS.ROUNDS * len(tokens)
    assert_profile(result, "local")


def test_verify_mocked_jwks_cache_miss(benchmark, signed_tokens_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk
    setup = fresh_client_each_round(Client, tokens)  # no jwks: each miss fetches them

    benchmark.extra_info["operations_per_round"] = len(tokens)
    # Only this GET is mocked; any other request raises.
    with responses.RequestsMock() as http:
        http.add(responses.GET, JWKS_URL, json={"keys": [jwk]}, status=200)
        with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
            result = benchmark.pedantic(verify_each, setup=setup, rounds=SETTINGS.ROUNDS)
        # One /jwks fetch per verification: every token missed.
        assert len(http.calls) == SETTINGS.ROUNDS * len(tokens)

    assert_profile(result, "local")


def test_verify_reused_client_mocked_jwks_cache_miss(benchmark, signed_tokens_and_jwk, require):
    # One long-lived client, as a service keeps: only its result cache is
    # emptied each round, so anything else it keeps (e.g. a JWKS cache) carries over.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    MemoryCache = require("fxa.cache", "MemoryCache")
    tokens, jwk = signed_tokens_and_jwk
    client = Client(server_url=SERVER_URL)

    def empty_result_cache():
        client.cache = MemoryCache()
        return (client, tokens), {}

    benchmark.extra_info["operations_per_round"] = len(tokens)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        http.add(responses.GET, JWKS_URL, json={"keys": [jwk]}, status=200)
        assert_each_missed(*empty_result_cache()[0])
        result = benchmark.pedantic(verify_each, setup=empty_result_cache, rounds=SETTINGS.ROUNDS)

    assert_profile(result, "local")


def test_verify_second_key_cache_miss(benchmark, signed_tokens_and_jwk, other_jwk, require):
    # The tokens match the second of two keys, as during an FxA key rotation,
    # so the first key is tried and rejected on every call.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    tokens, jwk = signed_tokens_and_jwk
    setup = fresh_client_each_round(Client, tokens, jwks=[other_jwk, jwk])

    benchmark.extra_info["operations_per_round"] = len(tokens)
    assert_each_missed(*setup()[0])
    result = benchmark.pedantic(verify_each, setup=setup, rounds=SETTINGS.ROUNDS)

    assert_profile(result, "local")


def test_verify_cache_hit(benchmark, signed_token_and_jwk, require):
    # _verify_jwt_token is patched below as a guard.
    Client = require("fxa.oauth", "Client", has=["verify_token", "_verify_jwt_token"])
    token, jwk = signed_token_and_jwk
    client = Client(server_url=SERVER_URL, jwks=[jwk])
    verify_once(client, token)  # warm the cache

    with patch.object(client, "_verify_jwt_token", side_effect=AssertionError("cache miss")):
        result = benchmark.pedantic(
            client.verify_token,
            args=(token,),
            kwargs=VERIFY_KWARGS,
            rounds=SETTINGS.ROUNDS,
            iterations=100,
        )

    assert_profile(result, "cached")


def test_decode_jwtoxide(benchmark, signed_token_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["_verify_jwt_token"])
    token, jwk = signed_token_and_jwk
    client = Client(server_url=SERVER_URL, jwks=[jwk])
    key = json.dumps(jwk)

    with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
        result = benchmark.pedantic(
            client._verify_jwt_token, args=(key, token), rounds=SETTINGS.ROUNDS, iterations=20
        )

    assert result["user"] == "benchmark-user"


def test_decode_pyjwt_fallback(benchmark, signed_token_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["_verify_jwt_token"])
    token, jwk = signed_token_and_jwk
    client = Client(server_url=SERVER_URL, jwks=[jwk])
    key = json.dumps(jwk)

    # Force the same exception route used when jwtoxide cannot decode a token.
    with patch("fxa.oauth.decode", side_effect=ValueError("forced fallback")) as jwtoxide_decode:
        result = benchmark.pedantic(
            client._verify_jwt_token, args=(key, token), rounds=SETTINGS.ROUNDS, iterations=20
        )

    # Every timed call tried jwtoxide, then fell back.
    assert jwtoxide_decode.call_count >= SETTINGS.ROUNDS * 20
    assert result["user"] == "benchmark-user"
