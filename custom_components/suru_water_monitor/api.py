"""Async client for the SURU WaterMonitor cloud API.

Auth is AWS Cognito USER_SRP_AUTH (no client secret) -> JWT access token, sent as
`Authorization: Bearer <access_token>` to the API Gateway (this is exactly what
the Android app does). Pure aiohttp + the self-contained SRP helper, no boto3.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import aiohttp

try:  # package import (Home Assistant)
    from .aws_srp import AWSSRP
    from .const import (
        API_BASE,
        COGNITO_CLIENT_ID,
        COGNITO_IDP_URL,
        COGNITO_POOL_ID,
    )
except ImportError:  # standalone import (recon test)
    from aws_srp import AWSSRP
    from const import API_BASE, COGNITO_CLIENT_ID, COGNITO_IDP_URL, COGNITO_POOL_ID

_AMZ_JSON = "application/x-amz-json-1.1"


class SuruError(Exception):
    """Base error."""


class SuruAuthError(SuruError):
    """Invalid credentials or auth flow failure."""


class SuruApiError(SuruError):
    """Non-auth API error."""


class SuruWaterApi:
    """Minimal SURU WaterMonitor API client."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str):
        self._session = session
        self._email = email
        self._password = password
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    # -- Cognito auth -----------------------------------------------------

    async def _cognito(self, target: str, body: dict) -> dict:
        try:
            async with self._session.post(
                COGNITO_IDP_URL,
                data=json.dumps(body),
                headers={
                    "Content-Type": _AMZ_JSON,
                    "X-Amz-Target": f"AWSCognitoIdentityProviderService.{target}",
                },
            ) as resp:
                payload = await resp.json(content_type=None)
                if resp.status != 200:
                    err_type = (payload or {}).get("__type", "")
                    msg = (payload or {}).get("message", str(payload))
                    if any(t in err_type for t in
                           ("NotAuthorized", "UserNotFound", "InvalidParameter")):
                        raise SuruAuthError(f"{err_type}: {msg}")
                    raise SuruApiError(f"{err_type}: {msg}")
                return payload
        except aiohttp.ClientError as err:
            raise SuruApiError(f"Cognito request failed: {err}") from err

    async def async_login(self) -> None:
        """Full SRP login -> access/refresh tokens."""
        srp = AWSSRP(self._email, self._password, COGNITO_POOL_ID, COGNITO_CLIENT_ID)
        init = await self._cognito("InitiateAuth", {
            "AuthFlow": "USER_SRP_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": srp.get_auth_parameters(),
        })
        if init.get("ChallengeName") != "PASSWORD_VERIFIER":
            raise SuruAuthError(f"Unexpected challenge: {init.get('ChallengeName')}")
        responses = srp.process_challenge(init["ChallengeParameters"])
        result = await self._cognito("RespondToAuthChallenge", {
            "ChallengeName": "PASSWORD_VERIFIER",
            "ClientId": COGNITO_CLIENT_ID,
            "ChallengeResponses": responses,
        })
        self._store_tokens(result.get("AuthenticationResult", {}))

    def _store_tokens(self, auth_result: dict) -> None:
        token = auth_result.get("AccessToken")
        if not token:
            raise SuruAuthError("No AccessToken in Cognito response")
        self._access_token = token
        self._expires_at = time.time() + int(auth_result.get("ExpiresIn", 3600))

    async def _async_token(self) -> str:
        # Access tokens live ~1h. The pool rejects refresh-token rotation, so we
        # just repeat the (cheap) SRP login when the token is about to expire.
        if not self._access_token or time.time() >= self._expires_at - 60:
            await self.async_login()
        return self._access_token  # type: ignore[return-value]

    # -- API --------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict:
        token = await self._async_token()
        url = f"{API_BASE}/{path.lstrip('/')}"
        try:
            async with self._session.get(
                url, params=params,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/json"},
            ) as resp:
                if resp.status == 401:
                    raise SuruAuthError("API returned 401 (token rejected)")
                text = await resp.text()
                if resp.status != 200:
                    raise SuruApiError(f"GET {path} -> HTTP {resp.status}: {text[:200]}")
                return json.loads(text) if text else {}
        except aiohttp.ClientError as err:
            raise SuruApiError(f"GET {path} failed: {err}") from err

    async def async_get_units(self) -> list[dict]:
        data = await self._get("units")
        return data.get("units", []) or []

    async def async_get_unit(self, unit_id: str) -> dict:
        return await self._get(f"units/{unit_id}")

    async def async_get_reading(self, unit_id: str) -> dict:
        # `date` is mandatory (API returns HTTP 400 without it).
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return await self._get(f"units/{unit_id}/reading", {"date": now})

    async def async_get_consumption(self, unit_id: str, range_: str = "day") -> dict:
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return await self._get(
            f"units/{unit_id}/consumption", {"range": range_, "startDate": start}
        )

    async def async_get_incidents(
        self, unit_id: str, device_id: str | None = None
    ) -> dict:
        params = {"deviceId": device_id} if device_id else None
        return await self._get(f"units/{unit_id}/incidents", params)
