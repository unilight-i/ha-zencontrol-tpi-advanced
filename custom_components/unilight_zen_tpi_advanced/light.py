"""DALI fixtures and groups as Home Assistant lights."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import GROUP_OFFSET, MAX_ARC, REFRESH_AFTER_COMMAND
from .coordinator import ZenCoordinator
from .discovery import Fixture, Group
from .entity import ZenEntity, fixture_device, group_device, run_command


def to_arc(brightness: int) -> int:
    """HA brightness 1..255 -> DALI arc level 1..254 (the DALI arc scale is already perceptual)."""
    return max(1, min(MAX_ARC, round(brightness * MAX_ARC / 255)))


def to_brightness(arc: int) -> int:
    return max(1, min(255, round(arc * 255 / MAX_ARC)))


def _to_dali(value: int) -> int:
    """Colour channel 0..255 -> 0..254 (255 means 'leave unchanged' on the wire)."""
    return max(0, min(254, int(value)))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: ZenCoordinator = entry.runtime_data
    st = coordinator.structure
    entities: list[ZenLight] = [
        ZenLight(coordinator, entry, "ecg", f.address, f, fixture_device(coordinator, f)) for f in st.fixtures.values()
    ]
    entities += [
        ZenLight(coordinator, entry, "group", g.number, g, group_device(coordinator, g)) for g in st.groups.values()
    ]
    async_add_entities(entities)


class ZenLight(ZenEntity, LightEntity):
    _attr_name = None                                   # the entity takes the device name
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(self, coordinator: ZenCoordinator, entry: ConfigEntry, kind: str, number: int,
                 target: Fixture | Group, device) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._number = number
        self._wire = number if kind == "ecg" else GROUP_OFFSET + number
        self._last_on_level: int | None = None
        self._attr_unique_id = f"{entry.entry_id}_{kind}_{number}"
        self._attr_device_info = device

        modes: set[ColorMode] = set()
        if target.tunable:
            modes.add(ColorMode.COLOR_TEMP)
            self._attr_min_color_temp_kelvin = target.min_kelvin
            self._attr_max_color_temp_kelvin = target.max_kelvin
        if target.rgb_channels >= 4:
            modes.add(ColorMode.RGBW)
        elif target.rgb_channels == 3:
            modes.add(ColorMode.RGB)
        self._rgb_mode = next((m for m in modes if m in (ColorMode.RGBW, ColorMode.RGB)), None)
        self._attr_supported_color_modes = modes or {ColorMode.BRIGHTNESS}

    # ------------------------------------------------------------ state
    @property
    def _members(self) -> list[int]:
        return self.coordinator.members(self._kind, self._number)

    def _levels(self) -> list[int]:
        data = self.coordinator.data
        if data is None:
            return []
        return [lvl for a in self._members if (lvl := data.levels.get(a)) is not None]

    def _colour(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        return next((data.colours[a] for a in self._members if a in data.colours), None)

    @property
    def is_on(self) -> bool | None:
        levels = self._levels()
        return any(lvl > 0 for lvl in levels) if levels else None

    @property
    def brightness(self) -> int | None:
        level = max(self._levels(), default=0)
        if level <= 0:
            return None
        self._last_on_level = level
        return to_brightness(level)

    @property
    def color_mode(self) -> ColorMode:
        modes = self._attr_supported_color_modes
        if len(modes) == 1:
            return next(iter(modes))
        colour = self._colour() or {}
        if "rgbwaf" in colour and self._rgb_mode:
            return self._rgb_mode
        return ColorMode.COLOR_TEMP

    @property
    def color_temp_kelvin(self) -> int | None:
        kelvin = (self._colour() or {}).get("kelvin")
        if kelvin is None:
            return None
        return max(self.min_color_temp_kelvin, min(self.max_color_temp_kelvin, kelvin))

    def _channels(self, count: int) -> tuple[int, ...] | None:
        values = (self._colour() or {}).get("rgbwaf")
        if values is None:
            return None
        return tuple(min(255, round((v or 0) * 255 / 254)) for v in values[:count])

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        return self._channels(4)    # type: ignore[return-value]

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._channels(3)    # type: ignore[return-value]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "dali_target": f"{'A' if self._kind == 'ecg' else 'G'}{self._number}",
            "dali_address": self._wire,
        }
        levels = self._levels()
        if levels:
            attrs["arc_level"] = max(levels)
        return attrs

    # ------------------------------------------------------------ commands
    async def async_turn_on(self, **kwargs: Any) -> None:
        client = self.coordinator.client
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        level = to_arc(brightness) if brightness is not None else None
        seconds = round(kwargs.get(ATTR_TRANSITION) or 0)
        was_on = bool(self.is_on)
        colour: dict[str, Any] | None = None

        if (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
            await run_command(client.colour_temperature(self._wire, kelvin, level))
            colour = {"kelvin": int(kelvin)}
        elif (rgb := kwargs.get(ATTR_RGBW_COLOR) or kwargs.get(ATTR_RGB_COLOR)) is not None:
            channels: list[int | None] = [_to_dali(v) for v in rgb]
            await run_command(client.colour_rgbwaf(self._wire, channels, level))
            colour = {"rgbwaf": channels + [None] * (6 - len(channels))}

        if colour is not None:
            if level is None and not was_on:    # colour was set while off - now switch on
                await run_command(client.last_active_level(self._wire))
            seconds = 0
        elif level is not None:
            if seconds >= 1:
                await run_command(client.custom_fade(self._wire, level, seconds))
            else:
                await run_command(client.arc_level(self._wire, level))
        else:
            await run_command(client.last_active_level(self._wire))

        if level is None and not was_on:
            level = self._last_on_level or MAX_ARC
        self.coordinator.set_optimistic(self._members, level, colour)
        self.coordinator.schedule_refresh(seconds + REFRESH_AFTER_COMMAND)

    async def async_turn_off(self, **kwargs: Any) -> None:
        client = self.coordinator.client
        seconds = round(kwargs.get(ATTR_TRANSITION) or 0)
        if seconds >= 1:
            await run_command(client.custom_fade(self._wire, 0, seconds))
        else:
            await run_command(client.off(self._wire))
        self.coordinator.set_optimistic(self._members, 0)
        self.coordinator.schedule_refresh(seconds + REFRESH_AFTER_COMMAND)
