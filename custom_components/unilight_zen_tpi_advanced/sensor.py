"""Diagnostic sensor: when the controller last sent an event (shows that events arrive)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import SIGNAL_EVENT
from .coordinator import ZenCoordinator
from .entity import controller_device
from .events import ZenEvents

MIN_WRITE_INTERVAL = 2.0     # seconds - a fade produces many events, the state is written at most this often


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: ZenCoordinator = entry.runtime_data
    if coordinator.events is not None:
        async_add_entities([ZenLastEvent(coordinator, coordinator.events, entry)])


class ZenLastEvent(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "last_event"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:access-point"

    def __init__(self, coordinator: ZenCoordinator, events: ZenEvents, entry: ConfigEntry) -> None:
        self._events = events
        self._entry_id = entry.entry_id
        self._written: datetime | None = None
        self._pending: CALLBACK_TYPE | None = None
        self._attr_unique_id = f"{entry.entry_id}_last_event"
        self._attr_device_info = controller_device(entry, coordinator.structure)

    @property
    def native_value(self) -> datetime | None:
        return self._events.last

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "event_type": self._events.last_name,
            "events_received": self._events.count,
            "listening": self._events.listening,
            "events_sent_to": f"{self._events.target_host}:{self._events.port}",
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_EVENT.format(self._entry_id), self._on_event))

    async def async_will_remove_from_hass(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    @callback
    def _on_event(self) -> None:
        if self._pending is not None:           # a write is already planned
            return
        now = dt_util.utcnow()
        wait = 0.0 if self._written is None else MIN_WRITE_INTERVAL - (now - self._written).total_seconds()
        if wait <= 0:
            self._write(now)
        else:
            self._pending = async_call_later(self.hass, wait, self._write)

    @callback
    def _write(self, _now: datetime) -> None:
        self._pending = None
        self._written = dt_util.utcnow()
        self.async_write_ha_state()
