"""Devices (controller / fixture / group) and the shared entity base."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZenCoordinator
from .discovery import Fixture, Group, OccupancySensor, PushButton, Structure
from .tpi import TpiError, TpiReply

MANUFACTURER = "zencontrol"


def controller_device(entry: ConfigEntry, structure: Structure) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=structure.label or entry.title,
        manufacturer=MANUFACTURER,
        model="Application Controller (TPI Advanced)",
        sw_version=structure.version,
    )


def _child(coordinator: ZenCoordinator, key: str, name: str, model: str) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{key}")},
        name=name,
        manufacturer=MANUFACTURER,
        model=model,
    )
    if coordinator.controller_device_id:        # shown as "connected via" the controller
        info["via_device_id"] = coordinator.controller_device_id
    return info


def fixture_device(coordinator: ZenCoordinator, fixture: Fixture) -> DeviceInfo:
    return _child(coordinator, f"ecg_{fixture.address}", fixture.name, "DALI fixture")


def group_device(coordinator: ZenCoordinator, group: Group) -> DeviceInfo:
    return _child(coordinator, f"group_{group.number}", group.name, "DALI group")


def ecd_device(coordinator: ZenCoordinator, instance: OccupancySensor | PushButton) -> DeviceInfo:
    """The control device (wall panel, sensor) an instance belongs to."""
    return _child(coordinator, f"ecd_{instance.address}", instance.device_name, "DALI control device")


async def run_command(command: Awaitable[TpiReply]) -> TpiReply:
    """Await a lighting command and turn failures into a message Home Assistant shows."""
    try:
        reply = await command
    except TpiError as err:
        raise HomeAssistantError(f"zencontrol: {err}") from err
    if not reply.ok:
        raise HomeAssistantError(f"zencontrol controller refused the command: {reply.error_name}")
    return reply


class ZenEntity(CoordinatorEntity[ZenCoordinator]):
    _attr_has_entity_name = True
