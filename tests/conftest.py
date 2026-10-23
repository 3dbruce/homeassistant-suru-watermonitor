"""Pytest fixtures.

The HA-framework tests need pytest-homeassistant-custom-component; the pure
unit tests (SRP, API client) do not. Guard the HA bits so the unit tests can
also run in a plain Python environment.
"""
try:
    import pytest_homeassistant_custom_component  # noqa: F401

    _HA_AVAILABLE = True
except ImportError:
    _HA_AVAILABLE = False


if _HA_AVAILABLE:
    import pytest

    pytest_plugins = ["pytest_homeassistant_custom_component"]

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
        """Enable loading the custom integration in every HA test.

        The integration depends on the recorder (hourly statistics), which must
        be set up before `hass`, hence `recorder_mock` comes first.
        """
        yield
