"""Polls arc levels (and colours) from the controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, REFRESH_AFTER_COMMAND
from .discovery import Structure
from .tpi import TpiClient, TpiError

if TYPE_CHECKING:
    from .events import ZenEvents

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ZenState:
    levels: dict[int, int | None] = field(default_factory=dict)          # fixture address -> arc level
    colours: dict[int, dict[str, Any]] = field(default_factory=dict)     # fixture address -> colour


class ZenCoordinator(DataUpdateCoordinator[ZenState]):
    """Reads the level of every shown fixture; group state is derived from the members."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TpiClient,
                 structure: Structure, interval: int) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.structure = structure
        self.controller_device_id: str | None = None
        self.events: ZenEvents | None = None        # set when receiving events is switched on
        self._cancel_refresh: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> ZenState:
        state = ZenState()
        previous = self.data
        try:
            for address, fixture in self.structure.fixtures.items():
                state.levels[address] = await self.client.query_level(address)
                if fixture.tunable or fixture.rgb_channels:
                    colour = await self.client.query_colour(address)
                    if colour is None and previous is not None:
                        colour = previous.colours.get(address)
                    if colour is not None:
                        state.colours[address] = colour
            if self.events is not None:
                # the event setting is lost when the controller restarts - put it back if needed
                await self.events.async_assert()
        except TpiError as err:
            raise UpdateFailed(str(err)) from err
        return state

    # ------------------------------------------------------------ helpers for entities
    def members(self, kind: str, number: int) -> list[int]:
        """Fixture addresses a command to this target changes."""
        if kind == "ecg":
            return [number]
        if kind == "group":
            return list(self.structure.groups[number].members)
        return list(self.structure.fixtures)

    @callback
    def set_optimistic(self, addresses: list[int], level: int | None = None,
                       colour: dict[str, Any] | None = None) -> None:
        """Show the expected result immediately; the next poll replaces it with the real one."""
        if self.data is None:
            return
        for address in addresses:
            if address not in self.structure.fixtures:
                continue
            if level is not None:
                self.data.levels[address] = level
            if colour is not None:
                fixture = self.structure.fixtures[address]
                if ("kelvin" in colour and fixture.tunable) or ("rgbwaf" in colour and fixture.rgb_channels):
                    self.data.colours[address] = colour
        self.async_update_listeners()

    @callback
    def schedule_refresh(self, delay: float = REFRESH_AFTER_COMMAND) -> None:
        """Read the real levels back once the fade has finished."""
        if self._cancel_refresh is not None:
            self._cancel_refresh()

        async def _refresh(_now: Any) -> None:
            self._cancel_refresh = None
            await self.async_request_refresh()

        self._cancel_refresh = async_call_later(self.hass, delay, _refresh)

    async def async_shutdown(self) -> None:
        if self._cancel_refresh is not None:
            self._cancel_refresh()
            self._cancel_refresh = None
        await super().async_shutdown()
