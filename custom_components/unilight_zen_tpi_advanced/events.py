"""Receives TPI events (buttons, motion sensors, level and colour changes) pushed by the controller.

The controller sends events as UDP packets. Inside Docker on Windows multicast does not arrive,
so the controller is told to send them straight (unicast) to the computer running Home Assistant:
SET_TPI_EVENT_UNICAST_ADDRESS + ENABLE_TPI_EVENT_EMIT. That setting is not persistent on the
controller (it is lost when the controller restarts), so it is checked again on every poll.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_TPI,
    GROUP_OFFSET,
    REFRESH_AFTER_EVENT,
    SIGNAL_BUTTON,
    SIGNAL_EVENT,
    SIGNAL_NEW_BUTTON,
    SIGNAL_NEW_SENSOR,
    SIGNAL_OCCUPANCY,
)
from .coordinator import ZenCoordinator
from .discovery import OccupancySensor, PushButton
from .tpi import (
    COLOUR_RGBWAF,
    COLOUR_TC,
    ECD_OFFSET,
    EVENT_ABSOLUTE_INPUT,
    EVENT_BUTTON_HOLD,
    EVENT_BUTTON_PRESS,
    EVENT_COLOUR_CHANGE,
    EVENT_GROUP_LEVEL_CHANGE,
    EVENT_GROUP_OCCUPANCY,
    EVENT_LEVEL_CHANGE,
    EVENT_LEVEL_CHANGE_V2,
    EVENT_OCCUPANCY,
    EVENT_PROFILE_CHANGE,
    EVENT_SCENE_CHANGE,
    EVENT_SYSTEM_VARIABLE,
    EVENTS_ENABLED,
    EVENTS_NO_MULTICAST,
    EVENTS_UNICAST,
    TpiEvent,
    parse_event,
)

_LOGGER = logging.getLogger(__name__)

# Events passed on to automations as "unilight_zen_tpi_advanced_event" (level and colour changes are
# already visible as light states, so they are not repeated there)
BUS_EVENTS = {
    EVENT_BUTTON_PRESS, EVENT_BUTTON_HOLD, EVENT_ABSOLUTE_INPUT, EVENT_SCENE_CHANGE, EVENT_OCCUPANCY,
    EVENT_SYSTEM_VARIABLE, EVENT_PROFILE_CHANGE, EVENT_GROUP_OCCUPANCY,
}
INSTANCE_EVENTS = {EVENT_BUTTON_PRESS, EVENT_BUTTON_HOLD, EVENT_ABSOLUTE_INPUT, EVENT_OCCUPANCY}


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: ZenEvents) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        event = parse_event(data)
        if event is not None:
            self._owner.handle(event)


class ZenEvents:
    """Listens for events of one controller and applies them."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: ZenCoordinator,
                 target_host: str, port: int) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.target_host = target_host      # where the controller must send events (this computer)
        self.port = port
        self.count = 0
        self.last: datetime | None = None
        self.last_name: str | None = None
        self.last_motion: dict[tuple[int, int], datetime] = {}
        self.listening = False
        self._transport: asyncio.DatagramTransport | None = None
        self._has_v2 = False
        self._assert_failed = False

    # ------------------------------------------------------------ socket
    async def async_start(self) -> None:
        try:
            self._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Protocol(self), local_addr=("0.0.0.0", self.port))
        except OSError as err:
            _LOGGER.error("zencontrol TPI: cannot listen for events on UDP port %s: %s", self.port, err)
            return
        self.listening = True

    async def async_stop(self) -> None:
        transport, self._transport = self._transport, None
        self.listening = False
        if transport is not None:
            transport.close()

    # ------------------------------------------------------------ controller side
    async def async_assert(self) -> bool:
        """Make sure the controller sends events to us. Returns True when it had to be (re)configured."""
        if not self.listening:
            return False
        client = self.coordinator.client
        want = EVENTS_ENABLED | EVENTS_UNICAST
        state = await client.query_event_unicast()
        if state and (state[0] & want) == want and state[1] == self.port and state[2] == self.target_host:
            self._assert_failed = False
            return False
        reply = await client.set_event_unicast(self.target_host, self.port)
        if reply.ok:
            keep = (state[0] if state else 0) & EVENTS_NO_MULTICAST     # leave multicast as it was
            reply = await client.enable_events(keep | want)
        if not reply.ok:
            if not self._assert_failed:
                _LOGGER.warning("zencontrol TPI: the controller refused to send events: %s", reply.error_name)
            self._assert_failed = True
            return False
        self._assert_failed = False
        _LOGGER.info("zencontrol TPI: controller now sends events to %s:%s", self.target_host, self.port)
        return True

    # ------------------------------------------------------------ incoming events
    @callback
    def handle(self, event: TpiEvent) -> None:
        self.count += 1
        self.last = dt_util.utcnow()
        self.last_name = event.name
        kind, data = event.type, event.data
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("event %s target=%s data=%s", event.name, event.target, list(data))

        # Only a fixture's own "arrived at this level" report is shown at once. Reports about a group
        # describe the group as the controller sees it (a lamp belongs to several groups and may be at
        # another level), and a report sent while dimming is not the final level - both just cause
        # a read-back of the real levels.
        if kind == EVENT_LEVEL_CHANGE_V2 and len(data) >= 2:
            self._has_v2 = True
            if event.target <= 63 and data[0] == data[1]:
                self._level(event.target, data[0])
            else:
                self.coordinator.schedule_refresh(REFRESH_AFTER_EVENT)
        elif kind == EVENT_LEVEL_CHANGE and data and not self._has_v2:
            if event.target <= 63:
                self._level(event.target, data[0])              # controllers without V2 events
        elif kind == EVENT_GROUP_LEVEL_CHANGE:
            self.coordinator.schedule_refresh(REFRESH_AFTER_EVENT)
        elif kind == EVENT_COLOUR_CHANGE and data:
            if event.target <= 63:
                self._colour(event.target, data)
            else:
                self.coordinator.schedule_refresh(REFRESH_AFTER_EVENT)
        elif kind == EVENT_SCENE_CHANGE:
            self.coordinator.schedule_refresh(REFRESH_AFTER_EVENT)
        elif kind == EVENT_OCCUPANCY and data and event.target >= ECD_OFFSET:
            self._occupancy(event.target - ECD_OFFSET, data[0])
        elif kind in (EVENT_BUTTON_PRESS, EVENT_BUTTON_HOLD) and data and event.target >= ECD_OFFSET:
            self._button(event.target - ECD_OFFSET, data[0], "press" if kind == EVENT_BUTTON_PRESS else "hold")

        if kind in BUS_EVENTS:
            self.hass.bus.async_fire(EVENT_TPI, self._describe(event))
        async_dispatcher_send(self.hass, SIGNAL_EVENT.format(self.entry.entry_id))

    def _level(self, address: int, level: int) -> None:
        if level == 255 or address not in self.coordinator.structure.fixtures:      # 255: unknown
            return
        self.coordinator.set_optimistic([address], level=level)
        self.coordinator.schedule_refresh(REFRESH_AFTER_EVENT)

    def _colour(self, address: int, data: bytes) -> None:
        colour: dict[str, Any] | None = None
        if data[0] == COLOUR_TC and len(data) >= 3:
            kelvin = (data[1] << 8) | data[2]
            colour = {"kelvin": kelvin} if 0 < kelvin < 0xFFFF else None
        elif data[0] == COLOUR_RGBWAF and len(data) >= 4:
            channels = [(None if v == 0xFF else v) for v in data[1:7]]
            colour = {"rgbwaf": channels + [None] * (6 - len(channels))}
        if colour and address in self.coordinator.structure.fixtures:
            self.coordinator.set_optimistic([address], colour=colour)

    def _occupancy(self, ecd: int, instance: int) -> None:
        st = self.coordinator.structure
        key = (ecd, instance)
        self.last_motion[key] = dt_util.utcnow()
        if key not in st.sensors:       # a sensor the discovery did not list - add it now
            st.sensors[key] = OccupancySensor(ecd, instance, st.ecd_labels.get(ecd, ""))
            async_dispatcher_send(self.hass, SIGNAL_NEW_SENSOR.format(self.entry.entry_id), st.sensors[key])
        async_dispatcher_send(self.hass, SIGNAL_OCCUPANCY.format(self.entry.entry_id), key)

    def _button(self, ecd: int, instance: int, action: str) -> None:
        st = self.coordinator.structure
        key = (ecd, instance)
        if key not in st.buttons:       # a button the discovery did not list - add it with this first press
            st.buttons[key] = PushButton(ecd, instance, st.ecd_labels.get(ecd, ""))
            async_dispatcher_send(self.hass, SIGNAL_NEW_BUTTON.format(self.entry.entry_id), st.buttons[key], action)
            return
        async_dispatcher_send(self.hass, SIGNAL_BUTTON.format(self.entry.entry_id), key, action)

    def _describe(self, event: TpiEvent) -> dict[str, Any]:
        """Event data for automations (trigger: event, event_type: unilight_zen_tpi_advanced_event)."""
        st = self.coordinator.structure
        kind, data = event.type, event.data
        out: dict[str, Any] = {
            "type": event.name,
            "host": self.coordinator.client.host,
            "mac": event.mac,
            "target": event.target,
            "data": list(data),
        }
        if kind in INSTANCE_EVENTS and event.target >= ECD_OFFSET:
            ecd = event.target - ECD_OFFSET
            out["address"] = ecd
            out["instance"] = data[0] if data else None
            out["name"] = st.ecd_labels.get(ecd) or f"ECD{ecd}"
            if kind == EVENT_ABSOLUTE_INPUT and len(data) >= 3:
                out["value"] = (data[1] << 8) | data[2]
        elif kind == EVENT_GROUP_OCCUPANCY and len(data) >= 2:
            number = event.target - GROUP_OFFSET
            group = st.groups.get(number)
            out["group"] = number
            out["occupied"] = bool(data[1])
            out["name"] = group.name if group else f"G{number}"
        elif kind == EVENT_SCENE_CHANGE and data:
            out["scene"] = data[0]
            if len(data) >= 2:
                out["at_scene"] = bool(data[1])
        elif kind == EVENT_PROFILE_CHANGE and len(data) >= 2:
            out["profile"] = (data[0] << 8) | data[1]
        elif kind == EVENT_SYSTEM_VARIABLE:
            out["variable"] = event.target
        return out
