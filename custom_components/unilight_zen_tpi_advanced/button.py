"""Scene buttons for every DALI group, plus All on / All off for the whole DALI line."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BROADCAST, GROUP_OFFSET, MAX_ARC
from .coordinator import ZenCoordinator
from .discovery import Group
from .entity import ZenEntity, controller_device, group_device, run_command


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: ZenCoordinator = entry.runtime_data
    st = coordinator.structure
    entities: list[ButtonEntity] = [
        ZenAllButton(coordinator, entry, on=True),
        ZenAllButton(coordinator, entry, on=False),
    ]
    for group in st.groups.values():
        for scene, label in sorted(group.scenes.items()):
            entities.append(ZenSceneButton(coordinator, entry, group, scene, label))
    async_add_entities(entities)


class ZenSceneButton(ZenEntity, ButtonEntity):
    _attr_icon = "mdi:palette"

    def __init__(self, coordinator: ZenCoordinator, entry: ConfigEntry, group: Group,
                 scene: int, label: str) -> None:
        super().__init__(coordinator)
        self._wire = GROUP_OFFSET + group.number
        self._scene = scene
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_group_{group.number}_scene_{scene}"
        self._attr_device_info = group_device(coordinator, group)
        self._attr_extra_state_attributes = {"dali_target": f"G{group.number}", "dali_scene": scene}

    async def async_press(self) -> None:
        await run_command(self.coordinator.client.scene(self._wire, self._scene))
        self.coordinator.schedule_refresh()


class ZenAllButton(ZenEntity, ButtonEntity):
    """Broadcast RECALL MAX / OFF to the whole DALI line."""

    def __init__(self, coordinator: ZenCoordinator, entry: ConfigEntry, on: bool) -> None:
        super().__init__(coordinator)
        self._on = on
        self._attr_translation_key = "all_on" if on else "all_off"
        self._attr_icon = "mdi:lightbulb-group" if on else "mdi:lightbulb-group-off"
        self._attr_unique_id = f"{entry.entry_id}_all_{'on' if on else 'off'}"
        self._attr_device_info = controller_device(entry, coordinator.structure)

    async def async_press(self) -> None:
        client = self.coordinator.client
        await run_command(client.recall_max(BROADCAST) if self._on else client.off(BROADCAST))
        self.coordinator.set_optimistic(self.coordinator.members("all", 0), MAX_ARC if self._on else 0)
        self.coordinator.schedule_refresh()
