"""Unit tests for the async API client (fake HTTP session, no Home Assistant)."""
import json
import pathlib
import sys

import pytest

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[1]
        / "custom_components" / "suru_water_monitor"),
)

from api import SuruApiError, SuruAuthError, SuruWaterApi  # noqa: E402

_CHALLENGE = {
    "ChallengeName": "PASSWORD_VERIFIER",
    "ChallengeParameters": {
        "USER_ID_FOR_SRP": "uid-1",
        "SALT": "a1b2c3d4",
        "SRP_B": "ff" * 64,
        "SECRET_BLOCK": "c2VjcmV0",
    },
}


def _tokens(access: str) -> dict:
    return {"AuthenticationResult": {"AccessToken": access, "ExpiresIn": 3600}}


class _FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._text = json.dumps(payload) if payload is not None else text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return json.loads(self._text)

    async def text(self):
        return self._text


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession returning queued responses."""

    def __init__(self):
        self._queues = {"POST": [], "GET": []}
        self.calls = []

    def queue(self, method, **kwargs):
        self._queues[method].append(_FakeResp(**kwargs))
        return self

    def login(self, access="ACCESS-123"):
        return (self.queue("POST", payload=_CHALLENGE)
                    .queue("POST", payload=_tokens(access)))

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        return self._queues["POST"].pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs.get("params")))
        return self._queues["GET"].pop(0)


async def test_login_and_get_units():
    session = FakeSession()
    session.login().queue("GET", payload={"units": [{"id": "144675"}]})
    api = SuruWaterApi(session, "u@example.com", "pw")
    units = await api.async_get_units()
    assert units == [{"id": "144675"}]
    assert api._access_token == "ACCESS-123"


async def test_reading_and_consumption():
    session = FakeSession()
    session.login()
    session.queue("GET", payload={"reading": 0.014, "measuredAt": "2026-06-25T11:03:00+02:00"})
    session.queue("GET", payload={"values": [1, 2, None], "range": "day", "groupBy": "hour"})
    api = SuruWaterApi(session, "u@example.com", "pw")
    reading = await api.async_get_reading("1")
    consumption = await api.async_get_consumption("1", "day")
    assert reading["reading"] == 0.014
    assert consumption["values"] == [1, 2, None]
    # reading must send the mandatory `date` query param
    assert any(c[0] == "GET" and c[2] and "date" in c[2] for c in session.calls)


async def test_invalid_credentials_raise_auth_error():
    session = FakeSession()
    session.queue("POST", status=400,
                  payload={"__type": "NotAuthorizedException", "message": "bad"})
    api = SuruWaterApi(session, "u@example.com", "wrong")
    with pytest.raises(SuruAuthError):
        await api.async_get_units()


async def test_http_error_raises_api_error():
    session = FakeSession()
    session.login().queue("GET", status=500, text="boom")
    api = SuruWaterApi(session, "u@example.com", "pw")
    with pytest.raises(SuruApiError):
        await api.async_get_units()


async def test_token_reuse_then_relogin_on_expiry():
    session = FakeSession()
    session.login("ACCESS-1").queue("GET", payload={"units": []})
    session.queue("GET", payload={"units": []})
    session.login("ACCESS-2").queue("GET", payload={"units": []})
    api = SuruWaterApi(session, "u@example.com", "pw")

    await api.async_get_units()
    assert api._access_token == "ACCESS-1"
    await api.async_get_units()            # token still valid -> reused
    assert api._access_token == "ACCESS-1"
    api._expires_at = 0                     # force expiry -> re-login
    await api.async_get_units()
    assert api._access_token == "ACCESS-2"
    # exactly two logins (4 POSTs) happened
    assert sum(1 for c in session.calls if c[0] == "POST") == 4
