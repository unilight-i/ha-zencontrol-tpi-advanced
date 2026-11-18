"""Setup: find controllers on the network (or take a typed address), test the connection, add.

Ways in:
  user   - "Add integration": the network is searched; found controllers are offered as a list,
           with "enter the address manually" as the last choice. When nothing is found the form says
           so and takes an address, or the first three numbers of a network to search address by address
  dhcp   - Home Assistant noticed a device with a zencontrol network card; one click adds it
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import (
    CONF_BUTTONS,
    CONF_CONTROLLER,
    CONF_EVENT_HOST,
    CONF_EVENT_PORT,
    CONF_EVENTS,
    CONF_INCLUDE_UNLABELLED,
    CONF_OCCUPANCY_HOLD,
    CONF_SCAN_INTERVAL,
    CONF_SENSORS,
    CONF_SKIP_TEST,
    DEFAULT_EVENT_PORT,
    DEFAULT_HOST,
    DEFAULT_OCCUPANCY_HOLD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_OCCUPANCY_HOLD,
    MAX_SCAN_INTERVAL,
    MIN_OCCUPANCY_HOLD,
    MIN_SCAN_INTERVAL,
)
from .scan import FoundController, scan
from .tpi import (
    CMD_QUERY_CONTROLLER_LABEL,
    CMD_QUERY_VERSION,
    ERR_PAID_FEATURE,
    EVENTS_ENABLED,
    EVENTS_UNICAST,
    TpiClient,
    TpiError,
    decode_text,
)

_LOGGER = logging.getLogger(__name__)


def _schema(host: str, port: int, with_skip: bool = True) -> vol.Schema:
    fields: dict[Any, Any] = {
        (vol.Required(CONF_HOST, default=host) if host else vol.Required(CONF_HOST)): str,
        vol.Required(CONF_PORT, default=port): cv.port,
    }
    if with_skip:
        fields[vol.Optional(CONF_SKIP_TEST, default=False)] = bool
    return vol.Schema(fields)


@dataclass(slots=True)
class Probe:
    """Result of the connection test."""

    error: str | None = None            # key of the message shown in the form
    label: str | None = None            # controller name
    events_to: str | None = None        # address another receiver already gets this controller's events at


async def _probe(host: str, port: int) -> Probe:
    """Ask the controller for its version, its name and where it sends events."""
    client = TpiClient(host, port)
    try:
        reply = await client.request(CMD_QUERY_VERSION, retries=2)
        if reply.error_code == ERR_PAID_FEATURE:
            return Probe(error="paid_feature")
        if not reply.ok:
            _LOGGER.warning("zencontrol TPI %s:%s answered %s", host, port, reply.error_name)
            return Probe(error="unexpected_reply")
        label_reply = await client.request(CMD_QUERY_CONTROLLER_LABEL)
        probe = Probe(label=(decode_text(label_reply.data) if label_reply.answered else None) or None)
        state = await client.query_event_unicast()
        active = EVENTS_ENABLED | EVENTS_UNICAST
        if state and (state[0] & active) == active and state[2] != "0.0.0.0":
            probe.events_to = state[2]
        return probe
    except TpiError as err:
        _LOGGER.warning("zencontrol TPI %s:%s did not reply: %s", host, port, err)
        return Probe(error="cannot_connect")
    finally:
        await client.close()


async def _find_controllers(hass: HomeAssistant) -> list[FoundController]:
    """Controllers answering on the networks Home Assistant is connected to."""
    try:
        targets = sorted(str(address) for address in await network.async_get_ipv4_broadcast_addresses(hass))
    except Exception:  # noqa: BLE001 - a network problem must not block typing the address by hand
        _LOGGER.debug("zencontrol TPI: could not read the network adapters", exc_info=True)
        return []
    return await scan(targets, DEFAULT_PORT)


async def _search_network(network: str, port: int) -> list[FoundController]:
    """Ask each of the 254 addresses of a network (given as its first three numbers) directly.

    Reaches controllers a broadcast cannot: Home Assistant in Docker or in a virtual machine behind NAT,
    a controller in another VLAN.
    """
    return await scan([f"{network}.{last}" for last in range(1, 255)], port)


def _typed_network(text: str) -> str | None:
    """First three numbers of the network a form entry names, None when it names one address.

    '192.168.1', '192.168.1.', '192.168.1.*', '192.168.1.0' and '192.168.1.0/24' all give '192.168.1'.
    """
    parts = text.strip().lower().removesuffix("/24").split(".")
    if len(parts) == 4 and parts[3] in ("", "0", "*", "x"):
        parts = parts[:3]
    if len(parts) != 3 or not all(p.isdigit() and int(p) <= 255 for p in parts):
        return None
    return ".".join(str(int(p)) for p in parts)


def _is_local(network: str) -> bool:
    """Only private networks are searched: a mistyped number must not send packets to the internet."""
    return ipaddress.IPv4Address(f"{network}.1").is_private


def _network_of(host: str) -> str | None:
    """First three numbers of a typed IPv4 address in a private network, None for anything else."""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        return None
    return host.rsplit(".", 1)[0] if address.is_private else None


async def _detect_event_host(hass: HomeAssistant, controller: str) -> str | None:
    """Address of Home Assistant in the controller's own network, None when there is none.

    None means the controller cannot reach Home Assistant directly (Docker with its own network,
    a virtual machine behind NAT, another VLAN); events then need the address typed in the options.
    """
    try:
        target = ipaddress.ip_address(controller)
        adapters = await network.async_get_adapters(hass)
    except Exception:  # noqa: BLE001
        return None
    for adapter in adapters:
        for address in adapter.get("ipv4") or []:
            try:
                net = ipaddress.ip_network(f"{address['address']}/{address['network_prefix']}", strict=False)
            except (KeyError, ValueError):
                continue
            if target in net and not net.is_loopback:
                return str(address["address"])
    return None


def _free_event_port(hass: HomeAssistant) -> int:
    """First UDP port from 6969 up that no other controller of this integration listens on."""
    used = {
        entry.options.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT)
        for entry in hass.config_entries.async_entries(DOMAIN) if entry.options.get(CONF_EVENTS)
    }
    port = DEFAULT_EVENT_PORT
    while port in used:
        port += 1
    return port


MANUAL = "manual"
# Texts of list entries built in code (the list itself is made of controllers found on the network)
_TEXTS = {
    "pl": {"manual": "Wpisz adres ręcznie", "unlicensed": "brak licencji TPI Advanced"},
    "en": {"manual": "Enter the address manually", "unlicensed": "no TPI Advanced licence"},
}


class ZenTpiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, FoundController] = {}
        self._port = DEFAULT_PORT              # port the controllers in _found answered on
        self._discovered: tuple[str, Probe] | None = None

    # ------------------------------------------------------------ "Add integration"
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._found, self._port = self._not_added(await _find_controllers(self.hass)), DEFAULT_PORT
        if self._found:
            return await self.async_step_pick()
        return await self.async_step_not_found()

    def _not_added(self, controllers: list[FoundController]) -> dict[str, FoundController]:
        configured = {entry.data.get(CONF_HOST) for entry in self._async_current_entries()}
        return {c.host: c for c in controllers if c.host not in configured}

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose one of the controllers found on the network."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_CONTROLLER]
            if host == MANUAL or host not in self._found:
                return await self.async_step_manual()
            await self.async_set_unique_id(f"{host}:{self._port}")
            self._abort_if_unique_id_configured()
            probe = await _probe(host, self._port)
            if probe.error is None:
                return await self._create(host, self._port, probe)
            errors["base"] = probe.error
        texts = _TEXTS.get(self.hass.config.language, _TEXTS["en"])
        options = [SelectOptionDict(value=c.host, label=self._describe(c, texts)) for c in self._found.values()]
        options.append(SelectOptionDict(value=MANUAL, label=texts["manual"]))
        schema = vol.Schema({
            vol.Required(CONF_CONTROLLER, default=next(iter(self._found))): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)),
        })
        return self.async_show_form(step_id="pick", data_schema=schema, errors=errors)

    @staticmethod
    def _describe(controller: FoundController, texts: dict[str, str]) -> str:
        details = [controller.host]
        if controller.version:
            details.append(f"firmware {controller.version}")
        if not controller.licensed:
            details.append(texts["unlicensed"])
        return f"{controller.label or 'zencontrol'} ({', '.join(details)})"

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Type the controller's address: chosen from the list of found controllers."""
        return await self._address_form("manual", user_input)

    async def async_step_not_found(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The same form, shown when the search found nothing to add; its text says so."""
        return await self._address_form("not_found", user_input)

    async def _address_form(self, step_id: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        """Take one address, or the first three numbers of a network to search address by address."""
        errors: dict[str, str] = {}
        host, port = DEFAULT_HOST, DEFAULT_PORT
        if user_input is not None and CONF_HOST in user_input:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            network = _typed_network(host)
            if network is not None:
                if not _is_local(network):
                    errors[CONF_HOST] = "search_local_only"
                elif await self._offer(network, port):
                    return await self.async_step_pick()
                else:
                    errors[CONF_HOST] = "none_in_network"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                probe = None if user_input.get(CONF_SKIP_TEST) else await _probe(host, port)
                if probe is None or probe.error is None:
                    return await self._create(host, port, probe)
                # no reply (a typing mistake, an address that changed): look at its neighbours
                network = _network_of(host) if probe.error == "cannot_connect" else None
                if network is not None and await self._offer(network, port):
                    return await self.async_step_pick()
                errors["base"] = probe.error
        return self.async_show_form(step_id=step_id, data_schema=_schema(host, port), errors=errors)

    async def _offer(self, network: str, port: int) -> bool:
        """Search a network; True when it has controllers to add (the list step then shows them)."""
        found = self._not_added(await _search_network(network, port))
        if found:
            self._found, self._port = found, port
        return bool(found)

    # ------------------------------------------------------------ found by Home Assistant (DHCP)
    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        host = discovery_info.ip
        await self.async_set_unique_id(f"{host}:{DEFAULT_PORT}")
        self._abort_if_unique_id_configured()
        probe = await _probe(host, DEFAULT_PORT)
        if probe.error is not None:
            # not a controller, TPI Advanced not licensed, or not reachable from Home Assistant
            return self.async_abort(reason="not_tpi_controller")
        self._discovered = (host, probe)
        self.context["title_placeholders"] = {"name": probe.label or host}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._discovered is not None
        host, probe = self._discovered
        if user_input is not None:
            return await self._create(host, DEFAULT_PORT, probe)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm", description_placeholders={"name": probe.label or "zencontrol", "host": host})

    # ------------------------------------------------------------ create
    async def _create(self, host: str, port: int, probe: Probe | None) -> ConfigFlowResult:
        """Add the controller; switch events on when Home Assistant is in the controller's network."""
        options: dict[str, Any] = {}
        if probe is not None:                   # None: the connection test was skipped
            event_host = await _detect_event_host(self.hass, host) or self._event_host_of_neighbour(host)
            if event_host and probe.events_to in (None, event_host):
                options = {
                    CONF_EVENTS: True,
                    CONF_EVENT_HOST: event_host,
                    CONF_EVENT_PORT: _free_event_port(self.hass),
                }
            elif event_host:
                _LOGGER.warning(
                    "zencontrol TPI %s already sends its events to %s; receiving events was left off. "
                    "The controller sends events to one address only - switch it on in the options "
                    "of the integration if Home Assistant should take them over", host, probe.events_to)
        return self.async_create_entry(
            title=(probe.label if probe else None) or f"zencontrol TPI {host}",
            data={CONF_HOST: host, CONF_PORT: port},
            options=options,
        )

    def _event_host_of_neighbour(self, controller: str) -> str | None:
        """This computer's address as typed for another controller of the same network.

        Behind NAT (Docker, a virtual machine) Home Assistant cannot see that address itself; once it was
        typed for one controller, the next controller of that network uses it too, on its own port.
        """
        network = _network_of(controller)
        if network is None:
            return None
        for entry in self._async_current_entries():
            typed = entry.options.get(CONF_EVENT_HOST)
            if entry.options.get(CONF_EVENTS) and typed and _network_of(entry.data.get(CONF_HOST, "")) == network:
                return typed
        return None

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the controller's IP address or port without losing devices and entities."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        host, port = entry.data[CONF_HOST], entry.data[CONF_PORT]
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            error = None if user_input.get(CONF_SKIP_TEST) else (await _probe(host, port)).error
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=f"{host}:{port}", data_updates={CONF_HOST: host, CONF_PORT: port})
            errors["base"] = error
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(host, port), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ZenTpiOptionsFlow:
        return ZenTpiOptionsFlow()


def _ipv4(text: str) -> str | None:
    """'192.168.1.57' -> the same text, anything else -> None."""
    parts = text.strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return None
    return ".".join(str(int(p)) for p in parts)


class ZenTpiOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        values = dict(self.config_entry.options)
        if user_input is not None:
            values = dict(user_input)
            typed = str(user_input.get(CONF_EVENT_HOST) or "").strip()
            host = _ipv4(typed)
            if not typed and user_input[CONF_EVENTS]:
                # left empty: Home Assistant's own address in the controller's network, if it has one
                host = await _detect_event_host(self.hass, self.config_entry.data[CONF_HOST])
            values[CONF_EVENT_HOST] = host or ""
            if not user_input[CONF_EVENTS] and (user_input[CONF_SENSORS] or user_input[CONF_BUTTONS]):
                # without events neither would ever appear, and nothing would say why
                errors[CONF_EVENTS] = "events_required"
            elif user_input[CONF_EVENTS] and host is None:
                errors[CONF_EVENT_HOST] = "event_host_required"
            elif user_input[CONF_EVENTS] and host == self.config_entry.data[CONF_HOST]:
                errors[CONF_EVENT_HOST] = "event_host_is_controller"
            elif user_input[CONF_EVENTS] and user_input[CONF_EVENT_PORT] in self._ports_of_others():
                errors[CONF_EVENT_PORT] = "event_port_in_use"
            else:
                return self.async_create_entry(data=values)
        schema = vol.Schema({
            vol.Required(
                CONF_SCAN_INTERVAL, default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            vol.Required(CONF_INCLUDE_UNLABELLED, default=values.get(CONF_INCLUDE_UNLABELLED, False)): bool,
            vol.Required(CONF_EVENTS, default=values.get(CONF_EVENTS, False)): bool,
            vol.Optional(
                CONF_EVENT_HOST, description={"suggested_value": values.get(CONF_EVENT_HOST) or ""}
            ): str,
            vol.Required(CONF_EVENT_PORT, default=values.get(CONF_EVENT_PORT) or self._free_port()): cv.port,
            vol.Required(CONF_SENSORS, default=values.get(CONF_SENSORS, False)): bool,
            vol.Required(CONF_BUTTONS, default=values.get(CONF_BUTTONS, False)): bool,
            vol.Required(
                CONF_OCCUPANCY_HOLD, default=values.get(CONF_OCCUPANCY_HOLD, DEFAULT_OCCUPANCY_HOLD)
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_OCCUPANCY_HOLD, max=MAX_OCCUPANCY_HOLD)),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _ports_of_others(self) -> set[int]:
        """Event ports the other controllers of this integration listen on."""
        return {
            entry.options.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != self.config_entry.entry_id and entry.options.get(CONF_EVENTS)
        }

    def _free_port(self) -> int:
        taken, port = self._ports_of_others(), DEFAULT_EVENT_PORT
        while port in taken:
            port += 1
        return port
