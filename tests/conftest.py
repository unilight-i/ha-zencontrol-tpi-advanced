"""Shared fixtures."""

import pytest

from .fake_controller import FakeController

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _auto_enable(enable_custom_integrations, socket_enabled):
    """Load integrations from custom_components and allow UDP to 127.0.0.1."""
    yield


@pytest.fixture(autouse=True)
def _quiet_network(monkeypatch):
    """By default a search of the network finds nothing and Home Assistant is outside the controller's
    network, so setup asks for the address and leaves events off. Tests of discovery replace both."""
    from custom_components.unilight_zen_tpi_advanced import config_flow

    async def nothing_found(hass):
        return []

    async def not_in_network(hass, controller):
        return None

    monkeypatch.setattr(config_flow, "_find_controllers", nothing_found)
    monkeypatch.setattr(config_flow, "_detect_event_host", not_in_network)


@pytest.fixture
async def fake():
    controller = FakeController()
    await controller.start()
    yield controller
    controller.stop()
