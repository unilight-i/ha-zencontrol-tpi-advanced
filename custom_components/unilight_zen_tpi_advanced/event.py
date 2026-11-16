"""Keys of DALI wall panels (push-button instances) as Home Assistant event entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_BUTTONS, SIGNAL_BUTTON, SIGNAL_NEW_BUTTON
from .coordinator import ZenCoordinator
from .discovery import PushButton
from .entity import ecd_device

PRESS = "press"
HOLD = "hold"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: ZenCoordinator = entry.runtime_data
    if coordinator.events is None or not entry.options.get(CONF_BUTTONS, False):
        # receiving events is switched off, or panel keys are not wanted (integration options)
        return

    @callback
    def add(button: PushButton, first_action: str) -> None:
        async_add_entities([ZenButtonEvent(coordinator, entry, button, first_action)])

    async_add_entities([ZenButtonEvent(coordinator, entry, b) for b in coordinator.structure.buttons.values()])
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_BUTTON.format(entry.entry_id), add))


class ZenButtonEvent(EventEntity):
    """Fires "press" (short press) and "hold" (key kept down) for one key of a panel."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [PRESS, HOLD]
    _attr_translation_key = "button"

    def __init__(self, coordinator: ZenCoordinator, entry: ConfigEntry, button: PushButton,
                 first_action: str | None = None) -> None:
        self._entry_id = entry.entry_id
        self._button = button
        self._first_action = first_action
        if button.instance_label:
            self._attr_name = button.instance_label
        else:
            self._attr_translation_placeholders = {"number": str(button.instance)}
        self._attr_unique_id = f"{entry.entry_id}_ecd_{button.address}_button_{button.instance}"
        self._attr_device_info = ecd_device(coordinator, button)
        self._attr_extra_state_attributes: dict[str, Any] = {
            "dali_target": f"ECD{button.address}",
            "dali_instance": button.instance,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_BUTTON.format(self._entry_id), self._on_button))
        if self._first_action:          # added because of this very press
            self._trigger_event(self._first_action)
            self._first_action = None

    @callback
    def _on_button(self, key: tuple[int, int], action: str) -> None:
        if key == self._button.key:
            self._trigger_event(action)
            self.async_write_ha_state()
