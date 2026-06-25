"""Config and options flow tests (require pytest-homeassistant-custom-component)."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suru_water_monitor.api import SuruApiError, SuruAuthError
from custom_components.suru_water_monitor.const import CONF_SCAN_INTERVAL, DOMAIN

USER_INPUT = {CONF_EMAIL: "u@example.com", CONF_PASSWORD: "pw"}

_PATCH = "custom_components.suru_water_monitor.config_flow.SuruWaterApi"


def _api(**kwargs) -> AsyncMock:
    api = AsyncMock()
    api.async_login = AsyncMock(**kwargs.get("login", {}))
    api.async_get_units = AsyncMock(return_value=[{"id": "1"}])
    return api


async def test_user_flow_success(hass: HomeAssistant) -> None:
    with patch(_PATCH, return_value=_api()), patch(
        "custom_components.suru_water_monitor.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "u@example.com"
    assert result["data"] == USER_INPUT


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    api = _api(login={"side_effect": SuruAuthError})
    with patch(_PATCH, return_value=api):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    api = _api(login={"side_effect": SuruApiError})
    with patch(_PATCH, return_value=api):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="u@example.com", data=USER_INPUT
    ).add_to_hass(hass)
    with patch(_PATCH, return_value=_api()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="u@example.com", data=USER_INPUT
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 30
