"""Config flow for SURU WaterMonitor."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SuruAuthError, SuruError, SuruWaterApi
from .const import DOMAIN


class SuruConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the SURU WaterMonitor config flow."""

    VERSION = 1

    async def _validate(self, email: str, password: str) -> dict[str, str]:
        """Return an errors dict ({} on success)."""
        api = SuruWaterApi(async_get_clientsession(self.hass), email, password)
        try:
            await api.async_login()
            await api.async_get_units()
        except SuruAuthError:
            return {"base": "invalid_auth"}
        except SuruError:
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            errors = await self._validate(email, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            email = reauth_entry.data[CONF_EMAIL]
            errors = await self._validate(email, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
