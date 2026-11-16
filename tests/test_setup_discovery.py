"""Version 0.7.0: finding controllers, one-click setup, automatic events, sensor / key switches."""

import asyncio
import socket
from ipaddress import IPv4Address

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unilight_zen_tpi_advanced import config_flow, tpi
from custom_components.unilight_zen_tpi_advanced.config_flow import (
    _detect_event_host as real_detect_event_host,
    _find_controllers as real_find_controllers,
)
from custom_components.unilight_zen_tpi_advanced.const import DOMAIN
from custom_components.unilight_zen_tpi_advanced.scan import FoundController, scan

from .fake_controller import FakeController

USER = {"source": config_entries.SOURCE_USER}
DHCP = {"source": config_entries.SOURCE_DHCP}


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(tpi, "DEFAULT_TIMEOUT", 0.3)
    monkeypatch.setattr(tpi.TpiClient.__init__, "__defaults__", (5108, 0.3))


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def on_network(monkeypatch, fake):
    """The simulated controller is what a search of the network finds; TPI's port is the simulator's."""
    monkeypatch.setattr(config_flow, "DEFAULT_PORT", fake.port)

    async def find(hass):
        return await scan(["127.0.0.1"], fake.port, wait=0.2)

    monkeypatch.setattr(config_flow, "_find_controllers", find)


@pytest.fixture
def same_network(monkeypatch):
    """Home Assistant has an address in the controller's network; events get a free UDP port."""
    port = free_udp_port()
    monkeypatch.setattr(config_flow, "DEFAULT_EVENT_PORT", port)

    async def detect(hass, controller):
        return "127.0.0.1"

    monkeypatch.setattr(config_flow, "_detect_event_host", detect)
    return port


def control_entities(hass: HomeAssistant, entry) -> tuple[list[str], list[str]]:
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return (sorted(e.entity_id for e in entities if e.domain == "binary_sensor"),
            sorted(e.entity_id for e in entities if e.domain == "event"))


# ------------------------------------------------------------------ searching the network
async def test_scan_finds_the_controller_with_name_and_firmware(fake):
    found = await scan(["127.0.0.1"], fake.port, wait=0.2)
    assert found == [FoundController(host="127.0.0.1", label="Walizka testowa", version="2.2.145", licensed=True)]
    assert set(fake.sent(0x1C)) and not fake.sent(0x40) and not fake.sent(0x08)      # nothing was changed


async def test_scan_reports_a_controller_without_licence_and_ignores_silence(fake):
    fake.error_all = 0xB0
    found = await scan(["127.0.0.1"], fake.port, wait=0.2)
    assert found == [FoundController(host="127.0.0.1", licensed=False)]
    fake.error_all, fake.silent = None, True
    assert await scan(["127.0.0.1"], fake.port, wait=0.2) == []
    assert await scan([], fake.port, wait=0.2) == []


async def test_scan_ignores_packets_that_are_not_tpi_replies():
    class Noise(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            self.transport.sendto(b"hello", addr)
            self.transport.sendto(bytes([0xA1, 0xD0, 3, 1, 2, 3, 0x00]), addr)     # wrong checksum

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(Noise, local_addr=("127.0.0.1", 0))
    try:
        assert await scan(["127.0.0.1"], transport.get_extra_info("sockname")[1], wait=0.2) == []
    finally:
        transport.close()


async def test_search_uses_the_broadcast_addresses_of_home_assistant(hass: HomeAssistant, fake, monkeypatch):
    asked = []

    async def broadcast_addresses(hass):
        return {IPv4Address("192.168.7.255"), IPv4Address("255.255.255.255")}

    async def fake_scan(targets, port):
        asked.append((targets, port))
        return []

    monkeypatch.setattr(config_flow.network, "async_get_ipv4_broadcast_addresses", broadcast_addresses)
    monkeypatch.setattr(config_flow, "scan", fake_scan)
    assert await real_find_controllers(hass) == []
    assert asked == [(["192.168.7.255", "255.255.255.255"], 5108)]

    async def broken(hass):
        raise OSError("no adapters")

    monkeypatch.setattr(config_flow.network, "async_get_ipv4_broadcast_addresses", broken)
    assert await real_find_controllers(hass) == []           # the address can still be typed


# ------------------------------------------------------------------ "Add integration" with a found controller
async def test_found_controller_is_added_by_choosing_it(hass: HomeAssistant, fake, on_network):
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pick"
    options = result["data_schema"].schema["controller"].config["options"]
    assert options[0] == {"value": "127.0.0.1", "label": "Walizka testowa (127.0.0.1, firmware 2.2.145)"}
    assert options[-1]["value"] == "manual" and len(options) == 2

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "127.0.0.1"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Walizka testowa"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
    assert result["options"] == {}                           # Home Assistant is not in the controller's network
    assert hass.states.get("light.downlight_3000k_a62") is not None

    # a controller that is already added is not offered again: the form asks for an address
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"


async def test_list_leads_to_typing_the_address(hass: HomeAssistant, fake, on_network):
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "manual"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_controller_without_licence_is_listed_and_explained(hass: HomeAssistant, fake, on_network):
    hass.config.language = "pl"
    fake.error_all = 0xB0
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    options = result["data_schema"].schema["controller"].config["options"]
    assert options[0]["label"] == "zencontrol (127.0.0.1, brak licencji TPI Advanced)"
    assert options[-1]["label"] == "Wpisz adres ręcznie"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "127.0.0.1"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pick"
    assert result["errors"] == {"base": "paid_feature"}


# ------------------------------------------------------------------ events switched on by themselves
async def test_events_are_switched_on_when_home_assistant_is_in_the_controllers_network(
        hass: HomeAssistant, fake, on_network, same_network):
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "127.0.0.1"})
    await hass.async_block_till_done()
    assert result["options"] == {"events_enabled": True, "event_host": "127.0.0.1", "event_port": same_network}
    assert fake.unicast == (same_network, "127.0.0.1") and fake.event_flags & 0x41 == 0x41
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert hass.states.get("sensor.walizka_testowa_last_event") is not None
    assert control_entities(hass, entry) == ([], [])         # sensors and keys wait for their switches


async def test_events_stay_off_when_another_system_receives_them(
        hass: HomeAssistant, fake, on_network, same_network, caplog):
    fake.event_flags, fake.unicast = 0x41, (6969, "192.168.1.99")
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "127.0.0.1"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["options"] == {}
    assert fake.unicast == (6969, "192.168.1.99") and not fake.sent(0x40)     # the other receiver keeps them
    assert "already sends its events to 192.168.1.99" in caplog.text


async def test_events_are_taken_back_after_reinstalling_on_the_same_computer(
        hass: HomeAssistant, fake, on_network, same_network):
    fake.event_flags, fake.unicast = 0x41, (6969, "127.0.0.1")       # left over from an earlier installation
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "127.0.0.1"})
    await hass.async_block_till_done()
    assert result["options"]["events_enabled"] is True
    assert fake.unicast == (same_network, "127.0.0.1")


async def test_second_controller_gets_its_own_event_port(hass: HomeAssistant, fake, on_network, same_network):
    first = MockConfigEntry(domain=DOMAIN, title="Showroom", data={CONF_HOST: "192.168.1.160", CONF_PORT: 5108},
                            options={"events_enabled": True, "event_host": "127.0.0.1", "event_port": same_network},
                            disabled_by=config_entries.ConfigEntryDisabler.USER)
    first.add_to_hass(hass)                                  # switched off: only its port matters here
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"controller": "127.0.0.1"})
    await hass.async_block_till_done()
    assert result["options"]["event_port"] == same_network + 1


async def test_skipping_the_test_leaves_events_off(hass: HomeAssistant, fake, same_network):
    result = await hass.config_entries.flow.async_init(DOMAIN, context=USER)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, "skip_test": True})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["options"] == {}


async def test_address_of_home_assistant_is_taken_from_the_controllers_network(hass: HomeAssistant, monkeypatch):
    async def adapters(hass):
        return [
            {"name": "lo", "enabled": False, "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}], "ipv6": []},
            {"name": "docker0", "enabled": False, "ipv4": [{"address": "172.17.0.1", "network_prefix": 16}], "ipv6": []},
            {"name": "eth0", "enabled": True, "ipv4": [{"address": "192.168.1.57", "network_prefix": 24}], "ipv6": []},
            {"name": "wlan0", "enabled": False, "ipv4": [], "ipv6": []},
        ]

    monkeypatch.setattr(config_flow.network, "async_get_adapters", adapters)
    assert await real_detect_event_host(hass, "192.168.1.213") == "192.168.1.57"
    assert await real_detect_event_host(hass, "192.168.2.213") is None       # another network: routed or NAT
    assert await real_detect_event_host(hass, "127.0.0.1") is None           # loopback is never an answer
    assert await real_detect_event_host(hass, "controller.local") is None


async def test_options_fill_in_the_address_when_it_is_left_empty(
        hass: HomeAssistant, fake, same_network, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, title="Walizka testowa", unique_id=f"127.0.0.1:{fake.port}",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async def other_computer(hass, controller):
        return "192.168.1.57"

    monkeypatch.setattr(config_flow, "_detect_event_host", other_computer)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "scan_interval": 30, "include_unlabelled": False, "events_enabled": True, "event_port": same_network,
        "sensors_enabled": True, "buttons_enabled": False, "occupancy_hold": 60})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["event_host"] == "192.168.1.57" and result["data"]["sensors_enabled"] is True
    await hass.async_block_till_done()


# ------------------------------------------------------------------ found by Home Assistant (DHCP)
def dhcp_info() -> DhcpServiceInfo:
    return DhcpServiceInfo(ip="127.0.0.1", hostname="zc-controller", macaddress="7cbacc210e01")


async def test_controller_noticed_by_home_assistant_is_added_with_one_confirmation(
        hass: HomeAssistant, fake, on_network):
    result = await hass.config_entries.flow.async_init(DOMAIN, context=DHCP, data=dhcp_info())
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"name": "Walizka testowa", "host": "127.0.0.1"}
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["flow_id"] == result["flow_id"])
    assert flow["context"]["title_placeholders"] == {"name": "Walizka testowa"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Walizka testowa"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}

    # noticed again later (every DHCP renewal): nothing is offered a second time
    result = await hass.config_entries.flow.async_init(DOMAIN, context=DHCP, data=dhcp_info())
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_noticed_device_that_is_not_a_usable_controller_is_not_offered(hass: HomeAssistant, fake, on_network):
    fake.silent = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context=DHCP, data=dhcp_info())
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_tpi_controller"
    fake.silent, fake.error_all = False, 0xB0                # no TPI Advanced licence
    result = await hass.config_entries.flow.async_init(DOMAIN, context=DHCP, data=dhcp_info())
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_tpi_controller"


async def test_manifest_asks_home_assistant_to_watch_for_zencontrol_network_cards(hass: HomeAssistant):
    from homeassistant.loader import async_get_integration

    integration = await async_get_integration(hass, DOMAIN)
    assert integration.dhcp == [{"macaddress": "7CBACC*"}]
    assert "network" in integration.dependencies


# ------------------------------------------------------------------ motion sensors and panel keys: options
def event_options(port: int, **extra) -> dict:
    return {"scan_interval": 30, "include_unlabelled": False, "events_enabled": True,
            "event_host": "127.0.0.1", "event_port": port, "occupancy_hold": 60, **extra}


async def add_entry(hass: HomeAssistant, fake: FakeController, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Walizka testowa", unique_id=f"127.0.0.1:{fake.port}",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def arrive(hass: HomeAssistant) -> None:
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def test_sensors_and_keys_are_off_until_switched_on_in_options(hass: HomeAssistant, fake):
    port = free_udp_port()
    entry = await add_entry(hass, fake, event_options(port))
    assert fake.unicast == (port, "127.0.0.1") and fake.event_flags == 0x41
    assert control_entities(hass, entry) == ([], [])
    devices = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert not {name for name in devices if "(ECD" in name}

    bus = []
    hass.bus.async_listen("unilight_zen_tpi_advanced_event", lambda e: bus.append(e.data))
    fake.emit(0x06, 64 + 4, [0, 1])                         # motion
    fake.emit(0x00, 64 + 3, [2])                            # key press
    fake.emit(0x06, 64 + 7, [3, 1])                         # a sensor the discovery did not list
    fake.emit(0x00, 64 + 7, [9])                            # a key the discovery did not list
    await arrive(hass)
    assert control_entities(hass, entry) == ([], [])        # nothing appears with the first event either
    # automations on the bus event keep working, and events still prove themselves
    assert [e["type"] for e in bus] == ["occupancy", "button_press", "occupancy", "button_press"]
    assert hass.states.get("sensor.walizka_testowa_last_event").state not in ("unknown", "unavailable")


async def test_sensors_and_keys_are_switched_separately(hass: HomeAssistant, fake):
    port = free_udp_port()
    entry = await add_entry(hass, fake, event_options(port, sensors_enabled=True))
    sensors, keys = control_entities(hass, entry)
    assert "binary_sensor.pir_ecd4" in sensors and keys == []

    hass.config_entries.async_update_entry(entry, options=event_options(port, buttons_enabled=True))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("event.klawiszowy_ecd3_button_0").state == "unknown"
    # the sensor made earlier is not removed: Home Assistant keeps it as "unavailable"
    assert hass.states.get("binary_sensor.pir_ecd4").state == "unavailable"
    fake.emit(0x06, 64 + 4, [0, 1])
    await arrive(hass)
    assert hass.states.get("binary_sensor.pir_ecd4").state == "unavailable"
