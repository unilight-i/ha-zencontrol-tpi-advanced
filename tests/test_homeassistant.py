"""The integration inside a real Home Assistant core, talking UDP to the simulated demo case."""

from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.unilight_zen_tpi_advanced import tpi
from custom_components.unilight_zen_tpi_advanced.const import DOMAIN

from .fake_controller import FakeController as FakeControllerFactory

DOMAIN_LIGHT = "light"


def h(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(tpi, "DEFAULT_TIMEOUT", 0.3)
    monkeypatch.setattr(tpi.TpiClient.__init__, "__defaults__", (5108, 0.3))


async def add_entry(hass: HomeAssistant, fake, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Walizka testowa", unique_id=f"127.0.0.1:{fake.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}, options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def later(hass: HomeAssistant, seconds: float) -> None:
    """Move Home Assistant's clock forward and let the (background) polling finish."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


# ------------------------------------------------------------------ setup form
async def test_config_flow_success(hass: HomeAssistant, fake):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Walizka testowa"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
    assert hass.states.get("light.downlight_3000k_a62") is not None

    # the same controller cannot be added twice
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_config_flow_errors(hass: HomeAssistant, fake):
    fake.silent = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}

    fake.silent, fake.error_all = False, 0xB0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    assert result["errors"] == {"base": "paid_feature"}

    fake.error_all = None            # the same form works once the controller answers
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_setup_retries_when_controller_is_silent(hass: HomeAssistant, fake):
    fake.silent = True
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1", CONF_PORT: fake.port})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY


# ------------------------------------------------------------------ devices and entities
async def test_devices_and_entities(hass: HomeAssistant, fake):
    entry = await add_entry(hass, fake)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    names = sorted(d.name for d in devices)
    assert names == sorted([
        "Walizka testowa",
        "RGBW (A56)", "Rolety (A57)", "Tunable White (A58)", "L prawa (A59)", "L środek (A60)",
        "L lewa (A61)", "Downlight 3000K (A62)", "GU10 (A63)",
        "Tunable white (G1)", "Downlight (G2)", "Gu10 (G3)", "Liniowa (G4)", "Rolety (G5)", "RGBW (G6)",
        "Strefa 3 lampy (G8)", "Akcent (G9)",
    ])
    controller = next(d for d in devices if d.name == "Walizka testowa")
    assert controller.sw_version == "2.2.145"

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    lights = sorted(e.entity_id for e in entities if e.domain == "light")
    buttons = sorted(e.entity_id for e in entities if e.domain == "button")
    assert len(lights) == 16
    assert len(buttons) == 2 + 3 + 2 + 1 + 2 + 1 + 2 + 1      # All on/off + scenes of G1 G2 G3 G4 G6 G8 G9
    assert "button.walizka_testowa_all_on" in buttons and "button.walizka_testowa_all_off" in buttons
    assert "button.strefa_3_lampy_g8_pelne" in buttons and "button.tunable_white_g1_wylacz" in buttons

    # levels read from the controller
    state = hass.states.get("light.l_lewa_a61")
    assert state.state == "on" and state.attributes["brightness"] == 100
    assert state.attributes["dali_target"] == "A61" and state.attributes["arc_level"] == 100
    assert hass.states.get("light.l_prawa_a59").state == "off"
    group = hass.states.get("light.liniowa_g4")          # one member is on -> the group is on
    assert group.state == "on" and group.attributes["brightness"] == 100
    assert group.attributes["dali_target"] == "G4" and group.attributes["dali_address"] == 68

    # colour capabilities
    tw = hass.states.get("light.tunable_white_a58")
    assert tw.attributes["supported_color_modes"] == ["color_temp"]
    assert (tw.attributes["min_color_temp_kelvin"], tw.attributes["max_color_temp_kelvin"]) == (2700, 6500)
    assert hass.states.get("light.rgbw_a56").attributes["supported_color_modes"] == ["rgbw"]
    assert hass.states.get("light.gu10_a63").attributes["supported_color_modes"] == ["brightness"]
    assert hass.states.get("light.strefa_3_lampy_g8").attributes["supported_color_modes"] == ["color_temp"]


async def test_light_commands(hass: HomeAssistant, fake):
    await add_entry(hass, fake)
    call = hass.services.async_call

    await call(DOMAIN_LIGHT, "turn_on", {"entity_id": "light.gu10_a63", "brightness": 128}, blocking=True)
    assert fake.sent(0xA2)[-1] == h("A2 3F 00 00 7F")            # brightness 128 of 255 -> arc 127 -> A63
    state = hass.states.get("light.gu10_a63")
    assert state.state == "on" and state.attributes["arc_level"] == 127       # shown at once (optimistic)

    await call(DOMAIN_LIGHT, "turn_off", {"entity_id": "light.gu10_a63"}, blocking=True)
    assert fake.sent(0xA9)[-1] == h("A9 3F 00 00 00")
    assert hass.states.get("light.gu10_a63").state == "off"

    await call(DOMAIN_LIGHT, "turn_on", {"entity_id": "light.gu10_a63"}, blocking=True)   # no brightness
    assert fake.sent(0xB5)[-1] == h("B5 3F 00 00 00")            # go to last active level
    assert fake.lights[63].level == 127

    await call(DOMAIN_LIGHT, "turn_on", {"entity_id": "light.liniowa_g4", "brightness": 255}, blocking=True)
    assert fake.sent(0xA2)[-1] == h("A2 44 00 00 FE")            # group 4 -> address 68, arc 254
    assert [hass.states.get(e).state for e in ("light.l_prawa_a59", "light.l_srodek_a60", "light.l_lewa_a61")] == ["on"] * 3

    await call(DOMAIN_LIGHT, "turn_on",
               {"entity_id": "light.downlight_3000k_a62", "brightness": 255, "transition": 5}, blocking=True)
    assert fake.sent(0xB4)[-1] == h("B4 3E FE 00 05")            # custom fade 5 s
    await call(DOMAIN_LIGHT, "turn_off", {"entity_id": "light.downlight_3000k_a62", "transition": 3}, blocking=True)
    assert fake.sent(0xB4)[-1] == h("B4 3E 00 00 03")


async def test_colour_commands(hass: HomeAssistant, fake):
    await add_entry(hass, fake)
    call = hass.services.async_call

    # tunable white while off, no brightness: colour only, then switch on
    await call(DOMAIN_LIGHT, "turn_on", {"entity_id": "light.tunable_white_a58", "color_temp_kelvin": 4000}, blocking=True)
    assert fake.sent(0x0E)[-1] == h("0E 3A FF 20 0F A0 FF FF FF FF FF")
    assert fake.sent(0xB5)[-1] == h("B5 3A 00 00 00")
    state = hass.states.get("light.tunable_white_a58")
    assert state.state == "on" and state.attributes["color_temp_kelvin"] == 4000

    # with brightness: one colour frame carries the arc level
    await call(DOMAIN_LIGHT, "turn_on",
               {"entity_id": "light.tunable_white_a58", "color_temp_kelvin": 6500, "brightness": 255}, blocking=True)
    assert fake.sent(0x0E)[-1] == h("0E 3A FE 20 19 64 FF FF FF FF FF")
    assert (fake.lights[58].kelvin, fake.lights[58].level) == (6500, 254)

    await call(DOMAIN_LIGHT, "turn_on",
               {"entity_id": "light.rgbw_a56", "rgbw_color": [255, 128, 0, 10], "brightness": 255}, blocking=True)
    assert fake.sent(0x0E)[-1] == h("0E 38 FE 80 FE 80 00 0A FF FF FF")
    await later(hass, 2)                                         # real values read back from the controller
    state = hass.states.get("light.rgbw_a56")
    assert state.state == "on" and state.attributes["color_mode"] == "rgbw"
    assert state.attributes["rgbw_color"] == (255, 129, 0, 10)

    # colour temperature on a group reaches the tunable member
    await call(DOMAIN_LIGHT, "turn_on", {"entity_id": "light.strefa_3_lampy_g8", "color_temp_kelvin": 3000}, blocking=True)
    assert fake.sent(0x0E)[-1] == h("0E 48 FF 20 0B B8 FF FF FF FF FF")
    assert fake.lights[58].kelvin == 3000


async def test_buttons_and_readback(hass: HomeAssistant, fake):
    await add_entry(hass, fake)
    press = lambda entity: hass.services.async_call("button", "press", {"entity_id": entity}, blocking=True)

    await press("button.strefa_3_lampy_g8_relaks")
    assert fake.sent(0xA1)[-1] == h("A1 48 00 00 0B")            # scene 11 on group 8
    assert hass.states.get("light.gu10_a63").state == "off"      # not known yet
    await later(hass, 2)                                         # read back after the fade
    state = hass.states.get("light.gu10_a63")
    assert state.state == "on" and state.attributes["arc_level"] == 60

    await press("button.walizka_testowa_all_on")
    assert fake.sent(0xA7)[-1] == h("A7 FF 00 00 00")            # recall max, broadcast
    assert all(hass.states.get(e).state == "on" for e in hass.states.async_entity_ids("light"))
    await press("button.walizka_testowa_all_off")
    assert fake.sent(0xA9)[-1] == h("A9 FF 00 00 00")
    assert all(hass.states.get(e).state == "off" for e in hass.states.async_entity_ids("light"))


async def test_polling_shows_changes_made_outside_home_assistant(hass: HomeAssistant, fake):
    await add_entry(hass, fake)
    assert hass.states.get("light.gu10_a63").state == "off"
    fake.lights[63].set_level(200)                               # somebody pressed a wall panel
    await later(hass, 31)
    state = hass.states.get("light.gu10_a63")
    assert state.state == "on" and state.attributes["arc_level"] == 200

    fake.silent = True                                           # controller unplugged
    await later(hass, 31)
    assert hass.states.get("light.gu10_a63").state == "unavailable"
    fake.silent = False
    await later(hass, 31)
    assert hass.states.get("light.gu10_a63").state == "on"


async def test_command_errors_are_reported(hass: HomeAssistant, fake):
    await add_entry(hass, fake)
    fake.error_all = 0xB2
    with pytest.raises(HomeAssistantError, match="CMD_REFUSED"):
        await hass.services.async_call(DOMAIN_LIGHT, "turn_off", {"entity_id": "light.gu10_a63"}, blocking=True)
    fake.error_all, fake.silent = None, True
    with pytest.raises(HomeAssistantError, match="No reply"):
        await hass.services.async_call(DOMAIN_LIGHT, "turn_off", {"entity_id": "light.gu10_a63"}, blocking=True)


# ------------------------------------------------------------------ actions
async def test_actions(hass: HomeAssistant, fake):
    await add_entry(hass, fake)
    events = []
    hass.bus.async_listen("unilight_zen_tpi_advanced_response", lambda e: events.append(e.data))

    async def act(name, data):
        return await hass.services.async_call(DOMAIN, name, data, blocking=True, return_response=True)

    result = await act("send_command", {"command": "0x1C"})
    assert result["type"] == "ANSWER" and result["data"] == [2, 2, 145]
    result = await act("send_command", {"command": "0xA2", "address": "62", "data": "0x00 0x00 0xFE"})
    assert result["type"] == "NO_ANSWER" and result["ok"] and fake.lights[62].level == 254
    result = await act("send_command", {"command": 36})
    assert result["text"] == "Walizka testowa"

    fake.lights[63].set_level(99)
    result = await act("send_raw", {"payload": "04 00 A9 FF 00 00 00", "add_checksum": True})
    assert result["request_hex"] == "04 00 A9 FF 00 00 00 52" and fake.lights[63].level == 0
    result = await act("send_raw", {"payload": "0x04,0x01,0x1C,0x00,0x00,0x00,0x00,0x19"})
    assert result["type"] == "ANSWER"

    await act("arc_level", {"group": 4, "level": 77})
    assert [fake.lights[a].level for a in (59, 60, 61)] == [77, 77, 77]
    await act("recall_scene", {"group": 8, "scene": 10})
    assert fake.lights[62].level == 254
    await act("turn_off", {"broadcast": True})
    assert all(light.level == 0 for light in fake.lights.values())
    await act("turn_on", {"address": 57})
    assert fake.lights[57].level == 254
    result = await act("arc_level", {"address": 20, "level": 10})
    assert result == {**result, "type": "ERROR", "ok": False, "error": "UNKNOWN_TARGET"}

    await hass.async_block_till_done()
    assert len(events) == 10 and events[0]["action"] == "send_command" and events[0]["host"] == "127.0.0.1"

    for bad in (
        ("arc_level", {"level": 10}),                               # no target
        ("arc_level", {"address": 1, "group": 2, "level": 10}),     # two targets
        ("arc_level", {"address": 64, "level": 10}),
        ("send_command", {"command": "zz"}),
        ("send_command", {"command": "0x1C", "data": "1 2 3 4"}),
        ("send_raw", {"payload": "04 0"}),
        ("send_command", {"command": "0x1C", "host": "10.0.0.1"}),  # no such controller
    ):
        with pytest.raises(ServiceValidationError):
            await act(*bad)


# ------------------------------------------------------------------ settings
async def test_options_and_reconfigure(hass: HomeAssistant, fake):
    entry = await add_entry(hass, fake)
    assert hass.states.get("light.dali_a0") is None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 5, "include_unlabelled": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("light.dali_a0") is not None          # reloaded with the unlabelled fixtures
    assert hass.states.get("light.dali_group_0") is not None
    fake.lights[63].set_level(42)
    await later(hass, 6)                                         # the 5 s interval is in use
    assert hass.states.get("light.gu10_a63").attributes["arc_level"] == 42

    # the controller gets a new address: entities keep their ids
    from .fake_controller import FakeController
    moved = FakeController()
    await moved.start()
    fake.silent = True
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: moved.port})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PORT] == moved.port
    await hass.services.async_call(DOMAIN_LIGHT, "turn_on", {"entity_id": "light.gu10_a63", "brightness": 255}, blocking=True)
    assert moved.lights[63].level == 254
    moved.stop()


async def test_unload(hass: HomeAssistant, fake):
    entry = await add_entry(hass, fake)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED
    assert hass.states.get("light.gu10_a63").state == "unavailable"


# ------------------------------------------------------------------ events (version 0.5.0)
import asyncio
import pathlib
import socket

import voluptuous as vol
import yaml
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

AUTOMATIONS = pathlib.Path(__file__).parent.parent / "examples" / "powiadomienie_o_ruchu.yaml"


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def event_options(port: int, **extra) -> dict:
    return {"scan_interval": 30, "include_unlabelled": False, "events_enabled": True,
            "event_host": "127.0.0.1", "event_port": port, "occupancy_hold": 60,
            "sensors_enabled": True, "buttons_enabled": True, **extra}


async def arrive(hass: HomeAssistant) -> None:
    """Let a UDP event travel from the simulated controller into Home Assistant."""
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def test_events_configure_controller_and_drive_entities(hass: HomeAssistant, fake):
    port = free_udp_port()
    await add_entry(hass, fake, options=event_options(port))
    # the integration told the controller where to send events, and switched them on
    assert fake.unicast == (port, "127.0.0.1") and fake.event_flags == 0x41

    bus = []
    hass.bus.async_listen("unilight_zen_tpi_advanced_event", lambda e: bus.append(e.data))

    # motion sensor found by the discovery
    assert hass.states.get("binary_sensor.pir_ecd4").state == "off"
    assert fake.emit(0x06, 64 + 4, [0, 1])
    await arrive(hass)
    state = hass.states.get("binary_sensor.pir_ecd4")
    assert state.state == "on" and state.attributes["dali_target"] == "ECD4" and state.attributes["last_motion"]
    assert bus[-1] == {**bus[-1], "type": "occupancy", "address": 4, "instance": 0, "name": "PIR",
                       "host": "127.0.0.1", "mac": "7c:ba:cc:2f:40:2e"}
    await later(hass, 40)                                   # motion again before the 60 s are over
    fake.emit(0x06, 64 + 4, [0, 1])
    await arrive(hass)
    await later(hass, 40)
    assert hass.states.get("binary_sensor.pir_ecd4").state == "on"
    await later(hass, 61)
    assert hass.states.get("binary_sensor.pir_ecd4").state == "off"

    # a sensor the discovery did not list appears with its first event
    assert hass.states.get("binary_sensor.dali_ecd7") is None
    fake.emit(0x06, 64 + 7, [3, 1])
    await arrive(hass)
    assert hass.states.get("binary_sensor.dali_ecd7").state == "on"

    # buttons reach automations as bus events
    fake.emit(0x00, 64 + 3, [2])
    await arrive(hass)
    assert bus[-1] == {**bus[-1], "type": "button_press", "address": 3, "instance": 2, "name": "klawiszowy"}
    fake.emit(0x0A, 64 + 8, [0xFF, 1])
    await arrive(hass)
    assert bus[-1] == {**bus[-1], "type": "group_occupancy", "group": 8, "occupied": True, "name": "Strefa 3 lampy (G8)"}

    # the diagnostic sensor proves that events arrive
    last = hass.states.get("sensor.walizka_testowa_last_event")
    assert last.state not in ("unknown", "unavailable")
    assert last.attributes["listening"] and last.attributes["events_sent_to"] == f"127.0.0.1:{port}"


async def test_events_update_lights_at_once(hass: HomeAssistant, fake):
    await add_entry(hass, fake, options=event_options(free_udp_port()))
    assert hass.states.get("light.gu10_a63").state == "off"

    fake.lights[63].set_level(200)                          # a wall panel switched the lamp on
    fake.emit(0x03, 63, [200])                              # legacy level event (controllers without V2)
    await arrive(hass)
    state = hass.states.get("light.gu10_a63")               # no waiting for the 30 s poll
    assert state.state == "on" and state.attributes["arc_level"] == 200

    fake.lights[58].set_level(254)
    fake.lights[58].kelvin = 5000
    fake.emit(0x0B, 58, [254, 254])                         # V2: the fixture arrived at 254
    fake.emit(0x08, 58, [0x20, 0x13, 0x88])                 # colour temperature 5000 K
    await arrive(hass)
    state = hass.states.get("light.tunable_white_a58")
    assert state.attributes["arc_level"] == 254 and state.attributes["color_temp_kelvin"] == 5000

    fake.emit(0x03, 58, [100])                              # legacy events are ignored once V2 events were seen
    await arrive(hass)
    assert hass.states.get("light.tunable_white_a58").attributes["arc_level"] == 254

    fake.lights[63].set_level(0)
    fake.emit(0x0B, 63, [200, 0])                           # still dimming to off: not shown yet ...
    await arrive(hass)
    assert hass.states.get("light.gu10_a63").state == "on"
    fake.emit(0x0B, 63, [0, 0])                             # ... arrived: shown at once
    await arrive(hass)
    assert hass.states.get("light.gu10_a63").state == "off"


async def test_group_reports_do_not_overwrite_member_lamps(hass: HomeAssistant, fake):
    """Seen on the real demo case: A62 switched on, a report about group 8 said 0, the lamp showed off."""
    await add_entry(hass, fake, options=event_options(free_udp_port()))
    await hass.services.async_call(
        DOMAIN_LIGHT, "turn_on", {"entity_id": "light.downlight_3000k_a62", "brightness": 115}, blocking=True)
    fake.emit(0x0B, 62, [0, 115])                           # the lamp starts dimming up
    fake.emit(0x0B, 64 + 2, [0, 115])                       # its own group
    fake.emit(0x0B, 64 + 8, [0, 0])                         # other groups it belongs to, as the controller sees them
    fake.emit(0x0B, 64 + 9, [0, 0])
    fake.emit(0x04, 8, [0])
    fake.emit(0x0B, 62, [115, 115])                         # arrived
    await arrive(hass)
    state = hass.states.get("light.downlight_3000k_a62")
    assert state.state == "on" and state.attributes["arc_level"] == 115
    assert hass.states.get("light.gu10_a63").state == "off"                 # other members untouched

    for a in (59, 60, 61):                                   # a panel dims group 4: shown after the read-back
        fake.lights[a].set_level(77)
    fake.emit(0x0B, 64 + 4, [100, 77])
    await arrive(hass)
    await later(hass, 3)
    assert [hass.states.get(e).attributes["arc_level"] for e in
            ("light.l_prawa_a59", "light.l_srodek_a60", "light.l_lewa_a61")] == [77, 77, 77]
    assert hass.states.get("light.downlight_3000k_a62").attributes["arc_level"] == 115


async def test_events_are_set_again_after_controller_restart(hass: HomeAssistant, fake):
    port = free_udp_port()
    await add_entry(hass, fake, options=event_options(port))
    fake.reboot()                                           # the controller forgets the event setting
    assert fake.event_flags == 0
    await later(hass, 31)                                   # next poll
    assert fake.unicast == (port, "127.0.0.1") and fake.event_flags == 0x41

    fake.event_flags = 0xC1                                 # somebody switched multicast off: left as it is
    fake.unicast = (1, "10.0.0.9")
    await later(hass, 31)
    assert fake.unicast == (port, "127.0.0.1") and fake.event_flags == 0xC1


async def test_events_off_by_default_and_port_in_use(hass: HomeAssistant, fake):
    entry = await add_entry(hass, fake)
    assert fake.event_flags == 0 and fake.sent(0x40) == [] and fake.sent(0x16) == []
    assert hass.states.get("binary_sensor.pir_ecd4") is None
    assert hass.states.get("sensor.walizka_testowa_last_event") is None
    assert await hass.config_entries.async_unload(entry.entry_id)

    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("0.0.0.0", 0))
    try:
        other = FakeControllerFactory()
        await other.start()
        second = MockConfigEntry(domain=DOMAIN, title="Druga", data={CONF_HOST: "127.0.0.1", CONF_PORT: other.port},
                                 options=event_options(blocker.getsockname()[1]))
        second.add_to_hass(hass)
        assert await hass.config_entries.async_setup(second.entry_id)      # lights still work
        await hass.async_block_till_done()
        assert second.state is config_entries.ConfigEntryState.LOADED
        assert other.event_flags == 0                                      # controller left alone
        other.stop()
    finally:
        blocker.close()


async def test_options_form_checks_the_computer_address(hass: HomeAssistant, fake):
    entry = await add_entry(hass, fake)
    base = {"scan_interval": 30, "include_unlabelled": False, "event_port": 6969, "occupancy_hold": 60}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**base, "events_enabled": True})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"event_host": "event_host_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, "events_enabled": True, "event_host": "192.168.1"})
    assert result["errors"] == {"event_host": "event_host_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, "events_enabled": True, "event_host": "127.0.0.1"})
    assert result["errors"] == {"event_host": "event_host_is_controller"}      # here the fake is on 127.0.0.1
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, "events_enabled": False, "event_host": " 192.168.1.57 "})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"]["event_host"] == "192.168.1.57"
    await hass.async_block_till_done()                     # let the reload after saving finish


async def test_shipped_automation_sends_one_notification_per_minute(hass: HomeAssistant, fake):
    """The example automation from examples/, run inside Home Assistant."""
    config = yaml.safe_load(AUTOMATIONS.read_text(encoding="utf-8"))
    assert [a["id"] for a in config] == ["zencontrol_pir_powiadomienie"]
    calls = async_mock_service(hass, "notify", "send_message")
    assert await async_setup_component(hass, "automation", {"automation": config})
    await add_entry(hass, fake, options=event_options(free_udp_port()))

    fake.emit(0x06, 64 + 4, [0, 1])
    await asyncio.sleep(0.1)        # (not block_till_done: the automation is inside its 60 s delay)
    assert len(calls) == 1
    assert calls[0].data["title"] == "Walizka zencontrol"
    assert calls[0].data["message"].startswith("Czujnik PIR wykrył ruch o ")
    assert calls[0].data["entity_id"] == ["notify.twoj_telefon"]

    fake.emit(0x06, 64 + 4, [0, 1])                        # more motion within the minute: no second message
    await asyncio.sleep(0.1)        # (not block_till_done: the automation is inside its 60 s delay)
    fake.emit(0x00, 64 + 3, [2])                           # a button press is not motion
    await asyncio.sleep(0.1)        # (not block_till_done: the automation is inside its 60 s delay)
    assert len(calls) == 1

    await later(hass, 61)
    fake.emit(0x06, 64 + 4, [0, 1])
    await asyncio.sleep(0.1)        # (not block_till_done: the automation is inside its 60 s delay)
    assert len(calls) == 2

    await hass.services.async_call("automation", "turn_off",
                                   {"entity_id": "automation.walizka_powiadomienie_o_ruchu_pir"}, blocking=True)
    await later(hass, 61)
    fake.emit(0x06, 64 + 4, [0, 1])
    await asyncio.sleep(0.1)        # (not block_till_done: the automation is inside its 60 s delay)
    assert len(calls) == 2                                  # switched off: silent


# ------------------------------------------------------------------ wall panel buttons (version 0.6.0)
async def test_panel_buttons_are_event_entities(hass: HomeAssistant, fake):
    entry = await add_entry(hass, fake, options=event_options(free_udp_port()))
    registry = er.async_get(hass)
    events = sorted(e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
                    if e.domain == "event")
    assert events == [
        "event.dotykowy_ecd2_button_0", "event.dotykowy_ecd2_button_1",
        "event.klawiszowy_ecd3_button_0", "event.klawiszowy_ecd3_button_1",
        "event.klawiszowy_ecd3_button_2", "event.klawiszowy_ecd3_button_3",
        "event.panel_rolety_ecd1_button_0", "event.panel_rolety_ecd1_button_1", "event.panel_rolety_ecd1_button_2",
        "event.przycisk_ecd5_button_0",
    ]
    devices = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert {"klawiszowy (ECD3)", "panel rolety (ECD1)", "dotykowy (ECD2)", "przycisk (ECD5)", "PIR (ECD4)"} <= devices

    state = hass.states.get("event.klawiszowy_ecd3_button_2")
    assert state.state == "unknown" and state.attributes["event_types"] == ["press", "hold"]
    assert state.attributes["dali_target"] == "ECD3" and state.attributes["dali_instance"] == 2

    fake.emit(0x00, 64 + 3, [2])                            # short press of key 2
    await arrive(hass)
    state = hass.states.get("event.klawiszowy_ecd3_button_2")
    assert state.state != "unknown" and state.attributes["event_type"] == "press"
    assert hass.states.get("event.klawiszowy_ecd3_button_1").state == "unknown"       # other keys untouched

    fake.emit(0x01, 64 + 3, [2])                            # the key is held down
    await arrive(hass)
    assert hass.states.get("event.klawiszowy_ecd3_button_2").attributes["event_type"] == "hold"

    # a key the discovery did not list appears with its first press
    assert hass.states.get("event.dali_ecd7_button_9") is None
    fake.emit(0x00, 64 + 7, [9])
    await arrive(hass)
    state = hass.states.get("event.dali_ecd7_button_9")
    assert state is not None and state.attributes["event_type"] == "press"

    # a press can start an automation without any YAML knowledge of event data
    calls = async_mock_service(hass, "light", "toggle")
    assert await async_setup_component(hass, "automation", {"automation": [{
        "alias": "Klawisz 0 przełącza downlight",
        "triggers": [{"trigger": "state", "entity_id": "event.klawiszowy_ecd3_button_0"}],
        "conditions": [{"condition": "template", "value_template": "{{ trigger.to_state.attributes.event_type == 'press' }}"}],
        "actions": [{"action": "light.toggle", "target": {"entity_id": "light.downlight_g2"}}],
    }]})
    fake.emit(0x00, 64 + 3, [0])
    await arrive(hass)
    assert len(calls) == 1


# ------------------------------------------------------------------ motion -> lamp automation offered to the user
MOTION_LIGHT = """
alias: "Walizka: ruch włącza Downlight"
description: Każde zgłoszenie ruchu włącza lampę, jeśli jest zgaszona. Lampa gaśnie 20 s po ostatnim ruchu.
triggers:
  - trigger: event
    event_type: unilight_zen_tpi_advanced_event
    event_data:
      type: occupancy
actions:
  - if:
      - condition: state
        entity_id: light.downlight_3000k_a62
        state: "off"
    then:
      - action: light.turn_on
        target:
          entity_id: light.downlight_3000k_a62
        data:
          brightness_pct: 100
  - delay: "00:00:20"
  - action: light.turn_off
    target:
      entity_id: light.downlight_3000k_a62
mode: restart
"""


async def test_motion_light_automation_recovers_when_a_panel_switches_the_lamp_off(hass: HomeAssistant, fake):
    assert await async_setup_component(hass, "automation", {"automation": [yaml.safe_load(MOTION_LIGHT)]})
    await add_entry(hass, fake, options=event_options(free_udp_port()))

    fake.emit(0x06, 64 + 4, [0, 1])                         # motion: lamp on at 100 %
    await asyncio.sleep(0.15)
    assert fake.lights[62].level == 254 and fake.sent(0xA2)[-1] == h("A2 3E 00 00 FE")

    fake.lights[62].set_level(0)                            # a wall panel switches the lamp off
    fake.emit(0x0B, 62, [0, 0])
    await asyncio.sleep(0.15)
    assert hass.states.get("light.downlight_3000k_a62").state == "off"
    fake.emit(0x06, 64 + 4, [0, 1])                         # the next motion report brings it back
    await asyncio.sleep(0.15)
    assert fake.lights[62].level == 254

    commands = len(fake.sent(0xA2))
    fake.emit(0x06, 64 + 4, [0, 1])                         # lamp already on: nothing is sent again
    await asyncio.sleep(0.15)
    assert len(fake.sent(0xA2)) == commands

    await later(hass, 21)                                   # 20 s without motion
    assert fake.lights[62].level == 0


# ------------------------------------------------------------------ shared package (version 0.6.2)
async def test_setup_form_starts_without_an_address(hass: HomeAssistant, fake):
    """Nothing of one particular installation is built in: the controller IP has to be typed."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys["host"].default is vol.UNDEFINED and keys["port"].default() == 5108
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 127.0.0.1 ", CONF_PORT: fake.port})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"][CONF_HOST] == "127.0.0.1"
