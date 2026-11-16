"""Motion (occupancy) sensors of DALI control devices, driven by TPI events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import CONF_OCCUPANCY_HOLD, CONF_SENSORS, DEFAULT_OCCUPANCY_HOLD, SIGNAL_NEW_SENSOR, SIGNAL_OCCUPANCY
from .coordinator import ZenCoordinator
from .discovery import OccupancySensor
from .entity import ecd_device
from .events import ZenEvents


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: ZenCoordinator = entry.runtime_data
    events = coordinator.events
    if events is None or not entry.options.get(CONF_SENSORS, False):
        # receiving events is switched off, or motion sensors are not wanted (integration options)
        return
    hold = entry.options.get(CONF_OCCUPANCY_HOLD, DEFAULT_OCCUPANCY_HOLD)

    @callback
    def add(sensor: OccupancySensor) -> None:
        async_add_entities([ZenOccupancy(coordinator, events, entry, sensor, hold)])

    async_add_entities(
        [ZenOccupancy(coordinator, events, entry, s, hold) for s in coordinator.structure.sensors.values()])
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_SENSOR.format(entry.entry_id), add))


class ZenOccupancy(BinarySensorEntity):
    """On when the sensor reported motion; off again after `hold` seconds without a new report.

    The controller only reports "motion detected" for a single sensor, never "no motion",
    so the way back to off is a timer here.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coordinator: ZenCoordinator, events: ZenEvents, entry: ConfigEntry,
                 sensor: OccupancySensor, hold: int) -> None:
        self._events = events
        self._entry_id = entry.entry_id
        self._sensor = sensor
        self._hold = hold
        self._cancel: CALLBACK_TYPE | None = None
        self._attr_name = sensor.instance_label or None      # None: the entity takes the device name
        self._attr_unique_id = f"{entry.entry_id}_ecd_{sensor.address}_occupancy_{sensor.instance}"
        self._attr_device_info = ecd_device(coordinator, sensor)
        self._attr_is_on = False

    @property
    def _last(self) -> datetime | None:
        return self._events.last_motion.get(self._sensor.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._last
        return {
            "dali_target": f"ECD{self._sensor.address}",
            "dali_instance": self._sensor.instance,
            "last_motion": last.isoformat() if last else None,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_OCCUPANCY.format(self._entry_id), self._on_motion))
        self._apply()                # a sensor added because of its first event starts as "on"

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    @callback
    def _on_motion(self, key: tuple[int, int]) -> None:
        if key == self._sensor.key:
            self._apply()
            self.async_write_ha_state()

    @callback
    def _apply(self) -> None:
        """Work out on/off from the time of the last motion report and (re)start the off timer."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        last = self._last
        remaining = self._hold - (dt_util.utcnow() - last).total_seconds() if last else 0
        self._attr_is_on = remaining > 0
        if remaining > 0:
            self._cancel = async_call_later(self.hass, remaining, self._clear)

    @callback
    def _clear(self, _now: datetime) -> None:
        self._cancel = None
        self._attr_is_on = False
        self.async_write_ha_state()
