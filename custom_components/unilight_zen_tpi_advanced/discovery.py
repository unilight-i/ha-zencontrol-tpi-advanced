"""Read fixtures, groups and scenes from the controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .const import DEFAULT_MAX_KELVIN, DEFAULT_MIN_KELVIN
from .tpi import INSTANCE_OCCUPANCY, INSTANCE_PUSH_BUTTON, TpiClient, TpiTimeout

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Fixture:
    """One DALI control gear (ECG) with a short address 0-63."""

    address: int
    label: str                      # "" when the fixture has no label in zencontrol
    groups: list[int] = field(default_factory=list)
    tunable: bool = False           # DT8 colour temperature
    rgb_channels: int = 0           # 0, or 3-6 for RGB / RGBW / RGBWA / RGBWAF
    min_kelvin: int = DEFAULT_MIN_KELVIN
    max_kelvin: int = DEFAULT_MAX_KELVIN

    @property
    def name(self) -> str:
        return f"{self.label} (A{self.address})" if self.label else f"DALI A{self.address}"


@dataclass(slots=True)
class Group:
    """One DALI group 0-15."""

    number: int
    label: str
    members: list[int] = field(default_factory=list)       # fixture addresses
    scenes: dict[int, str] = field(default_factory=dict)   # scene number -> label
    tunable: bool = False
    rgb_channels: int = 0
    min_kelvin: int = DEFAULT_MIN_KELVIN
    max_kelvin: int = DEFAULT_MAX_KELVIN

    @property
    def name(self) -> str:
        return f"{self.label} (G{self.number})" if self.label else f"DALI Group {self.number}"


@dataclass(slots=True)
class OccupancySensor:
    """One occupancy (motion) instance of a DALI control device."""

    address: int                    # control device address 0-63
    instance: int
    device_label: str               # label of the control device, "" when it has none
    instance_label: str = ""

    @property
    def key(self) -> tuple[int, int]:
        return self.address, self.instance

    @property
    def device_name(self) -> str:
        return f"{self.device_label} (ECD{self.address})" if self.device_label else f"DALI ECD{self.address}"


@dataclass(slots=True)
class PushButton:
    """One push-button instance of a DALI control device (a key on a wall panel)."""

    address: int                    # control device address 0-63
    instance: int
    device_label: str
    instance_label: str = ""

    @property
    def key(self) -> tuple[int, int]:
        return self.address, self.instance

    @property
    def device_name(self) -> str:
        return f"{self.device_label} (ECD{self.address})" if self.device_label else f"DALI ECD{self.address}"


@dataclass(slots=True)
class Structure:
    version: str | None
    label: str | None
    fixtures: dict[int, Fixture]
    groups: dict[int, Group]
    skipped_fixtures: list[int]     # in the controller's database but not shown (no label)
    skipped_groups: list[int]       # groups without any shown fixture
    sensors: dict[tuple[int, int], OccupancySensor] = field(default_factory=dict)
    ecd_labels: dict[int, str] = field(default_factory=dict)    # control device address -> label
    buttons: dict[tuple[int, int], PushButton] = field(default_factory=dict)


MAX_SOFT_TIMEOUTS = 3


async def discover(client: TpiClient, include_unlabelled: bool = False,
                   with_control_devices: bool = False) -> Structure:
    """Ask the controller what is on the DALI line. Raises TpiTimeout when it does not reply."""
    timeouts = 0

    async def soft(query, default):
        """A single unanswered detail query must not stop the setup; a silent controller must."""
        nonlocal timeouts
        try:
            return await query
        except TpiTimeout:
            timeouts += 1
            if timeouts > MAX_SOFT_TIMEOUTS:
                raise
            return default

    version = await client.query_version()                  # these two must be answered
    addresses = await client.query_cg_addresses()
    label = await soft(client.query_controller_label(), None)

    fixtures: dict[int, Fixture] = {}
    skipped_fixtures: list[int] = []
    membership_known = False
    for address in addresses:
        fixture_label = await soft(client.query_device_label(address), None) or ""
        if not fixture_label and not include_unlabelled:
            skipped_fixtures.append(address)
            continue
        fixture = Fixture(address, fixture_label)
        groups = await soft(client.query_group_membership(address), None)
        if groups is not None:
            membership_known = True
            fixture.groups = groups
        fixture.tunable, channels = await soft(client.query_colour_features(address), (False, 0))
        fixture.rgb_channels = channels if channels >= 3 else 0
        if fixture.tunable:
            limits = await soft(client.query_colour_temp_limits(address), None)
            if limits:
                fixture.min_kelvin, fixture.max_kelvin = limits
        fixtures[address] = fixture

    # groups the controller lists, plus any group a shown fixture says it belongs to
    numbers = set(await soft(client.query_group_numbers(), []))
    numbers.update(g for f in fixtures.values() for g in f.groups)

    groups: dict[int, Group] = {}
    skipped_groups: list[int] = []
    for number in sorted(numbers):
        members = [f.address for f in fixtures.values() if number in f.groups]
        if membership_known and not members:
            skipped_groups.append(number)
            continue
        group = Group(number, await soft(client.query_group_label(number), None) or "", members)
        for scene in await soft(client.query_scene_numbers_for_group(number), []):
            if scene > 11:      # zencontrol uses scenes 0-11
                continue
            scene_label = await soft(client.query_scene_label_for_group(number, scene), None)
            group.scenes[scene] = scene_label or f"Scena {scene}"
        colour_members = [fixtures[a] for a in members]
        tunable = [f for f in colour_members if f.tunable]
        if tunable:
            group.tunable = True
            group.min_kelvin = min(f.min_kelvin for f in tunable)
            group.max_kelvin = max(f.max_kelvin for f in tunable)
        group.rgb_channels = max((f.rgb_channels for f in colour_members), default=0)
        groups[number] = group

    # control devices: only needed when events are received (motion sensors, wall panel buttons)
    sensors: dict[tuple[int, int], OccupancySensor] = {}
    buttons: dict[tuple[int, int], PushButton] = {}
    ecd_labels: dict[int, str] = {}
    if with_control_devices:
        for ecd in await soft(client.query_ecd_addresses(), []):
            ecd_labels[ecd] = await soft(client.query_ecd_label(ecd), None) or ""
            for instance, kind in await soft(client.query_instances(ecd), []):
                if kind not in (INSTANCE_OCCUPANCY, INSTANCE_PUSH_BUTTON):
                    continue
                label = await soft(client.query_instance_label(ecd, instance), None) or ""
                if kind == INSTANCE_OCCUPANCY:
                    sensors[(ecd, instance)] = OccupancySensor(ecd, instance, ecd_labels[ecd], label)
                else:
                    buttons[(ecd, instance)] = PushButton(ecd, instance, ecd_labels[ecd], label)

    log = _LOGGER.info if fixtures else _LOGGER.warning
    log(
        "zencontrol TPI %s (firmware %s): %d fixtures, %d groups; hidden fixtures without a label %s, "
        "hidden groups without shown fixtures %s, motion sensors %s, buttons %s, unanswered detail queries %d",
        label or client.host, version, len(fixtures), len(groups), skipped_fixtures, skipped_groups,
        sorted(sensors), sorted(buttons), timeouts,
    )
    return Structure(
        version, label, fixtures, groups, skipped_fixtures, skipped_groups, sensors, ecd_labels, buttons)
