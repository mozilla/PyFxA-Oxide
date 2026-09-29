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


def assert_profile(profile, source):
    assert profile["user"] == "benchmark-user"
    assert profile["client_id"] == "benchmark-client"
    assert profile["scope"] == [SCOPE]
    assert profile["verification_source"] == source


def test_verify_supplied_jwks_cache_miss(benchmark, signed_token_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    token, jwk = signed_token_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid", jwks=[jwk]), token), {}

    # Fail rather than silently measure the PyJWT fallback.
    with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
        result = benchmark.pedantic(verify_once, setup=fresh_client, rounds=ROUNDS)

    assert_profile(result, "local")


def test_verify_mocked_jwks_cache_miss(benchmark, signed_token_and_jwk, require):
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    token, jwk = signed_token_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid"), token), {}

    # Only this GET is mocked; any other request raises.
    with responses.RequestsMock() as http:
        http.add(responses.GET, JWKS_URL, json={"keys": [jwk]}, status=200)
        with patch("fxa.oauth.jwt.decode", side_effect=AssertionError("PyJWT fallback")):
            result = benchmark.pedantic(verify_once, setup=fresh_client, rounds=ROUNDS)
        assert len(http.calls) == ROUNDS  # one /jwks fetch per round: every round missed

    assert_profile(result, "local")


def test_verify_reused_client_mocked_jwks_cache_miss(benchmark, signed_token_and_jwk, require):
    # How MLPA runs: one long-lived client. Only the per-token result cache is
    # emptied each round, so anything the client keeps (such as a JWKS cache)
    # carries over, as in production.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    MemoryCache = require("fxa.cache", "MemoryCache")
    token, jwk = signed_token_and_jwk
    client = Client(server_url="https://benchmark.invalid")

    def empty_result_cache():
        client.cache = MemoryCache()
        return (client, token), {}

    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        http.add(responses.GET, JWKS_URL, json={"keys": [jwk]}, status=200)
        result = benchmark.pedantic(verify_once, setup=empty_result_cache, rounds=ROUNDS)

    assert_profile(result, "local")


def test_verify_second_key_cache_miss(benchmark, signed_token_and_jwk, other_jwk, require):
    # The token matches the second of two keys, as during an FxA key rotation,
    # so the first key is tried and rejected on every call.
    Client = require("fxa.oauth", "Client", has=["verify_token"])
    token, jwk = signed_token_and_jwk

    def fresh_client():
        return (Client(server_url="https://benchmark.invalid", jwks=[other_jwk, jwk]), token), {}

    result = benchmark.pedantic(verify_once, setup=fresh_client, rounds=ROUNDS)

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
