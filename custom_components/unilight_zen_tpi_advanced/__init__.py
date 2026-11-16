"""zencontrol TPI Advanced (Unilight) - local control of a zencontrol DALI controller over TPI Advanced.

Needs the TPI Advanced licence on the controller and the same local network (UDP 5108).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    BROADCAST,
    CONF_EVENT_HOST,
    CONF_EVENT_PORT,
    CONF_EVENTS,
    CONF_INCLUDE_UNLABELLED,
    CONF_SCAN_INTERVAL,
    DEFAULT_EVENT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_RESPONSE,
    GROUP_OFFSET,
)
from .coordinator import ZenCoordinator
from .discovery import discover
from .entity import controller_device
from .events import ZenEvents
from .tpi import TpiClient, TpiError, TpiReply, checksum

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.BUTTON, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.EVENT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ZenConfigEntry = ConfigEntry[ZenCoordinator]


# ---------------------------------------------------------------- config entry
async def async_setup_entry(hass: HomeAssistant, entry: ZenConfigEntry) -> bool:
    client = TpiClient(entry.data[CONF_HOST], entry.data[CONF_PORT])
    event_host = entry.options.get(CONF_EVENT_HOST) if entry.options.get(CONF_EVENTS) else None
    try:
        structure = await discover(
            client, entry.options.get(CONF_INCLUDE_UNLABELLED, False), with_control_devices=bool(event_host))
    except TpiError as err:
        await client.close()
        raise ConfigEntryNotReady(
            f"zencontrol controller {client.host}:{client.port} does not reply over TPI: {err}") from err

    coordinator = ZenCoordinator(
        hass, entry, client, structure, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    if event_host:
        # open the UDP port for events first; the first refresh then tells the controller to use it
        coordinator.events = ZenEvents(
            hass, entry, coordinator, event_host, entry.options.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT))
        await coordinator.events.async_start()
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if coordinator.events is not None:
            await coordinator.events.async_stop()
        await client.close()
        raise

    # controller device first - fixture and group devices point to it through via_device
    controller = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **controller_device(entry, structure))
    coordinator.controller_device_id = controller.id

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZenConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = entry.runtime_data
        if coordinator.events is not None:
            await coordinator.events.async_stop()
        await coordinator.async_shutdown()
        await coordinator.client.close()
    return unloaded


# ---------------------------------------------------------------- actions (services)
def _number(value: Any, name: str) -> int:
    """'0x1C', '28' or 28 -> 28."""
    if isinstance(value, bool):
        raise ServiceValidationError(f"{name}: expected a number")
    if isinstance(value, (int, float)):
        number = int(value)
    else:
        text = str(value).strip()
        try:
            number = int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError as err:
            raise ServiceValidationError(f"{name}: '{value}' is not a number (use 28 or 0x1C)") from err
    if not 0 <= number <= 255:
        raise ServiceValidationError(f"{name}: {number} is outside 0-255")
    return number


def _number_list(value: Any, name: str) -> list[int]:
    """'0x00 0x00 0xFE', '0,0,254' or [0, 0, 254] -> [0, 0, 254]."""
    if value is None or value == "":
        return []
    items = value if isinstance(value, (list, tuple)) else [t for t in re.split(r"[\s,;]+", str(value).strip()) if t]
    return [_number(item, name) for item in items]


def _hex_bytes(value: Any) -> bytes:
    """'04 00 A9 FF 00 00 00' (also with 0x, commas, colons or dashes) -> bytes."""
    if isinstance(value, (list, tuple)):
        return bytes(_number(item, "payload") for item in value)
    text = re.sub(r"0[xX]", "", str(value))
    text = re.sub(r"[\s,;:\-]+", "", text)
    if not text or len(text) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", text):
        raise ServiceValidationError("payload: expected hex bytes, for example '04 00 1C 00 00 00 00'")
    return bytes.fromhex(text)


def _wire(call: ServiceCall) -> int:
    """address 0-63 / group 0-15 / broadcast -> the address byte used on the wire."""
    address, group = call.data.get("address"), call.data.get("group")
    chosen = [address is not None, group is not None, bool(call.data.get("broadcast"))]
    if sum(chosen) != 1:
        raise ServiceValidationError("Give exactly one target: address (0-63), group (0-15) or broadcast: true")
    if address is not None:
        number = _number(address, "address")
        if number > 63:
            raise ServiceValidationError("address: a DALI short address is 0-63 (use 'group' for groups)")
        return number
    if group is not None:
        number = _number(group, "group")
        if number > 15:
            raise ServiceValidationError("group: a DALI group is 0-15")
        return GROUP_OFFSET + number
    return BROADCAST


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> ZenCoordinator:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    host = call.data.get("host")
    if host:
        entries = [e for e in entries if e.data[CONF_HOST] == str(host).strip()]
    if not entries:
        raise ServiceValidationError(
            "No connected zencontrol TPI controller" + (f" with IP {host}" if host else "")
            + ". Check Settings > Devices & services > zencontrol TPI.")
    return entries[0].runtime_data


TARGET_FIELDS = {
    vol.Optional("address"): vol.Any(int, str),
    vol.Optional("group"): vol.Any(int, str),
    vol.Optional("broadcast"): cv.boolean,
    vol.Optional("host"): cv.string,
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _finish(call: ServiceCall, coordinator: ZenCoordinator, action) -> ServiceResponse:
        try:
            reply: TpiReply = await action
        except TpiError as err:
            raise HomeAssistantError(f"zencontrol: {err}") from err
        result = reply.as_dict()
        hass.bus.async_fire(EVENT_RESPONSE, {"action": call.service, "host": coordinator.client.host, **result})
        coordinator.schedule_refresh()
        return result if call.return_response else None

    async def send_command(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        command = _number(call.data["command"], "command")
        address = _number(call.data.get("address", 0), "address")
        data = _number_list(call.data.get("data"), "data")
        if len(data) > 3:
            raise ServiceValidationError("data: at most 3 bytes fit into a basic TPI frame (use send_raw for more)")
        return await _finish(call, coordinator, coordinator.client.request(command, address, data, retries=0))

    async def send_raw(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        frame = _hex_bytes(call.data["payload"])
        if call.data.get("add_checksum"):
            frame += bytes([checksum(frame)])
        if len(frame) < 4:
            raise ServiceValidationError("payload: a TPI frame has at least 4 bytes")
        return await _finish(call, coordinator, coordinator.client.send_raw(frame))

    async def arc_level(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return await _finish(call, coordinator, coordinator.client.arc_level(_wire(call), call.data["level"]))

    async def recall_scene(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return await _finish(call, coordinator, coordinator.client.scene(_wire(call), call.data["scene"]))

    async def turn_on(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return await _finish(call, coordinator, coordinator.client.recall_max(_wire(call)))

    async def turn_off(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return await _finish(call, coordinator, coordinator.client.off(_wire(call)))

    def register(name: str, handler, fields: dict) -> None:
        hass.services.async_register(
            DOMAIN, name, handler, schema=vol.Schema(fields), supports_response=SupportsResponse.OPTIONAL)

    register("send_command", send_command, {
        vol.Required("command"): vol.Any(int, str),
        vol.Optional("address", default=0): vol.Any(int, str),
        vol.Optional("data"): vol.Any(str, [vol.Any(int, str)]),
        vol.Optional("host"): cv.string,
    })
    register("send_raw", send_raw, {
        vol.Required("payload"): vol.Any(str, [vol.Any(int, str)]),
        vol.Optional("add_checksum", default=False): cv.boolean,
        vol.Optional("host"): cv.string,
    })
    register("arc_level", arc_level, {
        **TARGET_FIELDS, vol.Required("level"): vol.All(vol.Coerce(int), vol.Range(min=0, max=254))})
    register("recall_scene", recall_scene, {
        **TARGET_FIELDS, vol.Required("scene"): vol.All(vol.Coerce(int), vol.Range(min=0, max=11))})
    register("turn_on", turn_on, dict(TARGET_FIELDS))
    register("turn_off", turn_off, dict(TARGET_FIELDS))
    return True
