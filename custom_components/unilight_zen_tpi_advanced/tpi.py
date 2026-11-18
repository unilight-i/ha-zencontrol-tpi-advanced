"""Async UDP client for zencontrol TPI Advanced (Third Party Interface, UDP 5108).

Frame format
  request  [0x04, seq, command, address, d0, d1, d2, XOR checksum]            (basic frame)
           [0x04, seq, 0x0E, address, arc, colour type, 7 colour bytes, XOR]  (DALI colour frame)
  reply    [type, seq, data_len, data..., XOR checksum]
           type: 0xA0 OK, 0xA1 ANSWER, 0xA2 NO_ANSWER, 0xA3 ERROR (data[0] = error code)

Only one request is in flight at a time; replies are matched by sequence number and
stale replies are ignored. This module has no Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

MAGIC = 0x04

REPLY_OK = 0xA0
REPLY_ANSWER = 0xA1
REPLY_NO_ANSWER = 0xA2
REPLY_ERROR = 0xA3
REPLY_NAMES = {REPLY_OK: "OK", REPLY_ANSWER: "ANSWER", REPLY_NO_ANSWER: "NO_ANSWER", REPLY_ERROR: "ERROR"}

ERR_BUSY = 0x01              # on DALI_COLOUR the document says: system busy, try again
ERR_PAID_FEATURE = 0xB0
ERR_QUEUE_FAILURE = 0xB3
ERROR_NAMES = {
    0x01: "CHECKSUM",
    0x02: "SHORT_CIRCUIT",
    0x03: "RECEIVE_ERROR",
    0x04: "UNKNOWN_CMD",
    0xB0: "PAID_FEATURE",
    0xB1: "INVALID_ARGS",
    0xB2: "CMD_REFUSED",
    0xB3: "QUEUE_FAILURE",
    0xB4: "RESPONSE_UNAVAIL",
    0xB5: "OTHER_DALI_ERROR",
    0xB6: "MAX_LIMIT",
    0xB7: "UNEXPECTED_RESULT",
    0xB8: "UNKNOWN_TARGET",
}

# Commands
CMD_QUERY_GROUP_LABEL = 0x01
CMD_QUERY_DEVICE_LABEL = 0x03
CMD_QUERY_TPI_EVENT_EMIT_STATE = 0x07
CMD_ENABLE_TPI_EVENT_EMIT = 0x08
CMD_QUERY_GROUP_NUMBERS = 0x09
CMD_QUERY_INSTANCES_BY_ADDRESS = 0x0D
CMD_DALI_COLOUR = 0x0E
CMD_QUERY_GROUP_MEMBERSHIP = 0x15
CMD_QUERY_ADDRESSES_WITH_INSTANCES = 0x16
CMD_QUERY_SCENE_NUMBERS_FOR_GROUP = 0x1A
CMD_QUERY_SCENE_LABEL_FOR_GROUP = 0x1B
CMD_QUERY_VERSION = 0x1C
CMD_QUERY_CG_ADDRESSES = 0x1D
CMD_QUERY_CONTROLLER_LABEL = 0x24
CMD_QUERY_DALI_COLOUR = 0x34
CMD_QUERY_COLOUR_FEATURES = 0x35
CMD_QUERY_COLOUR_TEMP_LIMITS = 0x38
CMD_SET_TPI_EVENT_UNICAST_ADDRESS = 0x40
CMD_QUERY_TPI_EVENT_UNICAST_ADDRESS = 0x41
CMD_DALI_SCENE = 0xA1
CMD_DALI_ARC_LEVEL = 0xA2
CMD_DALI_RECALL_MAX = 0xA7
CMD_DALI_OFF = 0xA9
CMD_DALI_QUERY_LEVEL = 0xAA
CMD_DALI_CUSTOM_FADE = 0xB4
CMD_DALI_LAST_ACTIVE_LEVEL = 0xB5
CMD_QUERY_INSTANCE_LABEL = 0xB7

COLOUR_XY = 0x10
COLOUR_TC = 0x20
COLOUR_RGBWAF = 0x80

# Event modes (flags of ENABLE_TPI_EVENT_EMIT / QUERY_TPI_EVENT_EMIT_STATE)
EVENTS_ENABLED = 0x01
EVENTS_FILTERING = 0x02
EVENTS_UNICAST = 0x40
EVENTS_NO_MULTICAST = 0x80

# Event types sent by the controller
EVENT_BUTTON_PRESS = 0x00
EVENT_BUTTON_HOLD = 0x01
EVENT_ABSOLUTE_INPUT = 0x02
EVENT_LEVEL_CHANGE = 0x03
EVENT_GROUP_LEVEL_CHANGE = 0x04
EVENT_SCENE_CHANGE = 0x05
EVENT_OCCUPANCY = 0x06
EVENT_SYSTEM_VARIABLE = 0x07
EVENT_COLOUR_CHANGE = 0x08
EVENT_PROFILE_CHANGE = 0x09
EVENT_GROUP_OCCUPANCY = 0x0A
EVENT_LEVEL_CHANGE_V2 = 0x0B
EVENT_NAMES = {
    0x00: "button_press",
    0x01: "button_hold",
    0x02: "absolute_input",
    0x03: "level_change",
    0x04: "group_level_change",
    0x05: "scene_change",
    0x06: "occupancy",
    0x07: "system_variable",
    0x08: "colour_change",
    0x09: "profile_change",
    0x0A: "group_occupancy",
    0x0B: "level_change_v2",
}

# Instance types of control devices (QUERY_INSTANCES_BY_ADDRESS)
INSTANCE_PUSH_BUTTON = 0x01
INSTANCE_ABSOLUTE_INPUT = 0x02
INSTANCE_OCCUPANCY = 0x03
INSTANCE_LIGHT_SENSOR = 0x04
INSTANCE_GENERAL_SENSOR = 0x06

ECD_OFFSET = 64                 # control device n -> 64 + n on the wire

DEFAULT_TIMEOUT = 1.5
QUEUE_RETRIES = 3
QUEUE_BASE_DELAY = 0.05


class TpiError(Exception):
    """Base error for TPI communication."""


class TpiTimeout(TpiError):
    """The controller did not reply."""


def checksum(buf: bytes) -> int:
    """XOR of all bytes."""
    acc = 0
    for b in buf:
        acc ^= b
    return acc


def build_frame(seq: int, command: int, payload: bytes) -> bytes:
    """Complete request frame: magic, seq, command, payload (address + data), checksum."""
    body = bytes([MAGIC, seq & 0xFF, command & 0xFF]) + bytes(payload)
    return body + bytes([checksum(body)])


def basic_payload(address: int = 0, data: bytes | list[int] = b"") -> bytes:
    """Address + exactly 3 data bytes (zero padded)."""
    data = bytes(data)
    if len(data) > 3:
        raise ValueError("A basic TPI frame carries at most 3 data bytes")
    return bytes([address & 0xFF]) + data + bytes(3 - len(data))


def decode_text(data: bytes) -> str:
    """Label bytes -> text (UTF-8, falling back to Latin-1), trimmed."""
    data = bytes(data).split(b"\x00", 1)[0]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text.strip()


def bitmask_be16(data: bytes) -> list[int]:
    """Two bytes [bits 8-15][bits 0-7] -> sorted list of set bit numbers."""
    if len(data) != 2:
        return []
    value = (data[0] << 8) | data[1]
    return [i for i in range(16) if value & (1 << i)]


def hexs(buf: bytes) -> str:
    return " ".join(f"{b:02X}" for b in buf)


@dataclass(slots=True)
class TpiEvent:
    """One event frame pushed by the controller: 'ZC', MAC, target, type, data, XOR checksum."""

    mac: str
    target: int
    type: int
    data: bytes
    raw: bytes

    @property
    def name(self) -> str:
        return EVENT_NAMES.get(self.type, f"0x{self.type:02X}")


def parse_event(raw: bytes) -> TpiEvent | None:
    """Decode an event frame; None when it is not a valid one."""
    if len(raw) < 13 or raw[0] != 0x5A or raw[1] != 0x43:
        return None
    if len(raw) != 13 + raw[11] or checksum(raw[:-1]) != raw[-1]:
        return None
    return TpiEvent(
        mac=":".join(f"{b:02x}" for b in raw[2:8]),
        target=(raw[8] << 8) | raw[9],
        type=raw[10],
        data=bytes(raw[12:-1]),
        raw=bytes(raw),
    )


@dataclass(slots=True)
class TpiReply:
    """One decoded reply together with the request that caused it."""

    type: int
    data: bytes
    request: bytes
    raw: bytes

    @property
    def ok(self) -> bool:
        """True unless the controller answered ERROR."""
        return self.type != REPLY_ERROR

    @property
    def answered(self) -> bool:
        return self.type == REPLY_ANSWER

    @property
    def type_name(self) -> str:
        return REPLY_NAMES.get(self.type, f"0x{self.type:02X}")

    @property
    def error_code(self) -> int | None:
        if self.type == REPLY_ERROR and self.data:
            return self.data[0]
        return None

    @property
    def error_name(self) -> str | None:
        code = self.error_code
        if self.type != REPLY_ERROR:
            return None
        if code is None:
            return "ERROR"
        return ERROR_NAMES.get(code, f"0x{code:02X}")

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form used for action responses and events."""
        out: dict[str, Any] = {
            "type": self.type_name,
            "type_hex": f"0x{self.type:02X}",
            "ok": self.ok,
            "data": list(self.data),
            "data_hex": hexs(self.data),
            "request_hex": hexs(self.request),
            "response_hex": hexs(self.raw),
        }
        if self.type == REPLY_ERROR:
            out["error"] = self.error_name
        elif self.data and all(32 <= b < 127 for b in self.data):
            out["text"] = self.data.decode("ascii")
        return out


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, client: TpiClient) -> None:
        self._client = client

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._client._on_datagram(data)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("TPI socket error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self._client._on_connection_lost()


class TpiClient:
    """One UDP conversation with one controller."""

    def __init__(self, host: str, port: int = 5108, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._transport: asyncio.DatagramTransport | None = None
        self._lock = asyncio.Lock()
        self._seq = 0
        self._waiting_seq: int | None = None
        self._future: asyncio.Future[tuple[int, bytes, bytes]] | None = None

    # ------------------------------------------------------------ transport
    async def connect(self) -> None:
        if self._transport is not None and not self._transport.is_closing():
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), remote_addr=(self.host, self.port)
        )
        self._transport = transport

    async def close(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()

    def _on_connection_lost(self) -> None:
        self._transport = None

    def _on_datagram(self, raw: bytes) -> None:
        if len(raw) < 4 or len(raw) != raw[2] + 4:
            _LOGGER.debug("TPI: dropped malformed reply %s", hexs(raw))
            return
        if checksum(raw[:-1]) != raw[-1]:
            _LOGGER.debug("TPI: dropped reply with bad checksum %s", hexs(raw))
            return
        if raw[0] not in REPLY_NAMES:
            return  # not a command reply (e.g. an event frame)
        fut = self._future
        if fut is None or fut.done() or raw[1] != self._waiting_seq:
            return  # stale reply of an earlier, timed-out request
        fut.set_result((raw[0], bytes(raw[3:-1]), bytes(raw)))

    # ------------------------------------------------------------ requests
    async def _exchange(self, frame: bytes, retries: int) -> TpiReply:
        """Send one frame and wait for the reply with the same sequence number."""
        loop = asyncio.get_running_loop()
        for _ in range(retries + 1):
            try:
                await self.connect()
            except OSError as err:
                raise TpiTimeout(f"Cannot open UDP socket to {self.host}:{self.port}: {err}") from err
            assert self._transport is not None
            self._future = loop.create_future()
            self._waiting_seq = frame[1]
            try:
                self._transport.sendto(frame)
                rtype, data, raw = await asyncio.wait_for(self._future, self.timeout)
            except TimeoutError:
                _LOGGER.debug("sent %s -> no reply within %s s", hexs(frame), self.timeout)
                continue
            except OSError as err:
                _LOGGER.debug("TPI send failed: %s", err)
                await self.close()
                continue
            finally:
                self._future = None
                self._waiting_seq = None
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug("sent %s -> reply %s", hexs(frame), hexs(raw))
            return TpiReply(rtype, data, frame, raw)
        raise TpiTimeout(f"No reply from {self.host}:{self.port}")

    async def _send(self, command: int, payload: bytes, retries: int) -> TpiReply:
        async with self._lock:
            reply: TpiReply | None = None
            for attempt in range(QUEUE_RETRIES + 1):
                seq = self._seq
                self._seq = (self._seq + 1) & 0xFF
                reply = await self._exchange(build_frame(seq, command, payload), retries)
                busy = reply.error_code == ERR_QUEUE_FAILURE or (
                    command == CMD_DALI_COLOUR and reply.error_code == ERR_BUSY)
                if busy and attempt < QUEUE_RETRIES:
                    # controller's DALI queue is briefly full - nothing was executed, try again
                    await asyncio.sleep(QUEUE_BASE_DELAY * (2 ** attempt))
                    continue
                break
            assert reply is not None
            return reply

    async def request(self, command: int, address: int = 0, data: bytes | list[int] = b"",
                      *, retries: int = 1) -> TpiReply:
        """Basic frame: command + address + up to 3 data bytes."""
        return await self._send(command, basic_payload(address, data), retries)

    async def send_raw(self, frame: bytes) -> TpiReply:
        """Send bytes exactly as given (the caller built the whole frame)."""
        frame = bytes(frame)
        if len(frame) < 3:
            raise ValueError("A TPI frame needs at least 3 bytes")
        async with self._lock:
            return await self._exchange(frame, 0)

    # ------------------------------------------------------------ queries
    async def _text(self, command: int, address: int = 0, data: bytes | list[int] = b"") -> str | None:
        reply = await self.request(command, address, data)
        return (decode_text(reply.data) or None) if reply.answered else None

    async def query_version(self) -> str | None:
        reply = await self.request(CMD_QUERY_VERSION)
        if reply.answered and len(reply.data) >= 3:
            return ".".join(str(b) for b in reply.data[:3])
        return None

    async def query_controller_label(self) -> str | None:
        return await self._text(CMD_QUERY_CONTROLLER_LABEL)

    async def query_cg_addresses(self) -> list[int]:
        """Short addresses (0-63) of control gear in the controller's database."""
        reply = await self.request(CMD_QUERY_CG_ADDRESSES)
        if not reply.answered:
            return []
        return [i * 8 + bit for i, byte in enumerate(reply.data[:8]) for bit in range(8) if byte & (1 << bit)]

    async def query_device_label(self, address: int) -> str | None:
        return await self._text(CMD_QUERY_DEVICE_LABEL, address)

    async def query_group_numbers(self) -> list[int]:
        reply = await self.request(CMD_QUERY_GROUP_NUMBERS)
        return sorted(set(b for b in reply.data if b < 16)) if reply.answered else []

    async def query_group_label(self, group: int) -> str | None:
        return await self._text(CMD_QUERY_GROUP_LABEL, group)

    async def query_group_membership(self, address: int) -> list[int] | None:
        """Groups a fixture belongs to. None when the controller gave no answer."""
        reply = await self.request(CMD_QUERY_GROUP_MEMBERSHIP, address)
        if reply.answered and len(reply.data) == 2:
            return bitmask_be16(reply.data)
        return None

    async def query_scene_numbers_for_group(self, group: int) -> list[int]:
        reply = await self.request(CMD_QUERY_SCENE_NUMBERS_FOR_GROUP, group)
        return bitmask_be16(reply.data) if reply.answered else []

    async def query_scene_label_for_group(self, group: int, scene: int) -> str | None:
        return await self._text(CMD_QUERY_SCENE_LABEL_FOR_GROUP, group, [scene])

    async def query_level(self, wire: int) -> int | None:
        """Arc level 0-254 of a fixture or group; None when unknown or mixed."""
        reply = await self.request(CMD_DALI_QUERY_LEVEL, wire)
        if reply.answered and len(reply.data) == 1 and reply.data[0] != 255:
            return reply.data[0]
        return None

    async def query_colour_features(self, address: int) -> tuple[bool, int]:
        """(supports tunable white, number of RGBWAF channels)."""
        reply = await self.request(CMD_QUERY_COLOUR_FEATURES, address)
        if reply.answered and len(reply.data) == 1:
            flags = reply.data[0]
            return bool(flags & 0x02), (flags & 0xE0) >> 5
        return False, 0

    async def query_colour_temp_limits(self, address: int) -> tuple[int, int] | None:
        """(warmest kelvin, coolest kelvin) - the soft limits when set, else the physical ones."""
        reply = await self.request(CMD_QUERY_COLOUR_TEMP_LIMITS, address)
        if not reply.answered or len(reply.data) != 10:
            return None
        d = reply.data
        physical = ((d[0] << 8) | d[1], (d[2] << 8) | d[3])
        soft = ((d[4] << 8) | d[5], (d[6] << 8) | d[7])
        for warm, cool in (soft, physical):
            if 1000 <= warm < cool <= 20000:
                return warm, cool
        return None

    async def query_colour(self, address: int) -> dict[str, Any] | None:
        """{'kelvin': n} or {'rgbwaf': [r, g, b, w, a, f]} (missing channels are None)."""
        reply = await self.request(CMD_QUERY_DALI_COLOUR, address)
        if not reply.answered or not reply.data:
            return None
        d = reply.data
        if d[0] == COLOUR_TC and len(d) >= 3:
            kelvin = (d[1] << 8) | d[2]
            return {"kelvin": kelvin} if 0 < kelvin < 0xFFFF else None
        if d[0] == COLOUR_RGBWAF and len(d) >= 4:
            channels = [(None if v == 0xFF else v) for v in d[1:7]]
            return {"rgbwaf": channels + [None] * (6 - len(channels))}
        return None

    # ------------------------------------------------------------ commands
    # Lighting commands are sent once (no blind re-send). The controller acknowledges
    # most of them with NO_ANSWER, which is success; only ERROR is a failure.
    async def arc_level(self, wire: int, level: int) -> TpiReply:
        return await self.request(CMD_DALI_ARC_LEVEL, wire, [0, 0, max(0, min(254, level))], retries=0)

    async def off(self, wire: int) -> TpiReply:
        return await self.request(CMD_DALI_OFF, wire, retries=0)

    async def recall_max(self, wire: int) -> TpiReply:
        return await self.request(CMD_DALI_RECALL_MAX, wire, retries=0)

    async def last_active_level(self, wire: int) -> TpiReply:
        return await self.request(CMD_DALI_LAST_ACTIVE_LEVEL, wire, retries=0)

    async def scene(self, wire: int, scene: int) -> TpiReply:
        return await self.request(CMD_DALI_SCENE, wire, [0, 0, scene & 0x0F], retries=0)

    async def custom_fade(self, wire: int, level: int, seconds: int) -> TpiReply:
        seconds = max(0, min(65535, int(seconds)))
        return await self.request(
            CMD_DALI_CUSTOM_FADE, wire, [max(0, min(254, level)), seconds >> 8, seconds & 0xFF], retries=0)

    async def _colour(self, wire: int, level: int | None, colour: list[int]) -> TpiReply:
        arc = 0xFF if level is None else max(0, min(254, level))   # 0xFF = change colour only
        payload = bytes([wire & 0xFF, arc]) + bytes(colour) + bytes([0xFF] * (8 - len(colour)))
        return await self._send(CMD_DALI_COLOUR, payload, 0)

    async def colour_temperature(self, wire: int, kelvin: int, level: int | None = None) -> TpiReply:
        kelvin = max(1000, min(20000, int(kelvin)))
        return await self._colour(wire, level, [COLOUR_TC, kelvin >> 8, kelvin & 0xFF])

    async def colour_rgbwaf(self, wire: int, channels: list[int | None], level: int | None = None) -> TpiReply:
        """channels: up to six values 0-254 for R, G, B, W, A, F; None leaves a channel unchanged."""
        values = [(0xFF if v is None else max(0, min(254, int(v)))) for v in channels[:6]]
        values += [0xFF] * (6 - len(values))
        return await self._colour(wire, level, [COLOUR_RGBWAF, *values])

    # ------------------------------------------------------------ control devices (panels, sensors)
    async def query_ecd_addresses(self) -> list[int]:
        """Control devices (0-63) that have instances, e.g. buttons or a motion sensor."""
        found: set[int] = set()
        for start in (0, 60, 120):          # the controller returns at most 60 addresses per request
            reply = await self.request(CMD_QUERY_ADDRESSES_WITH_INSTANCES, 0, [0, 0, start])
            if reply.answered:
                found.update(b - ECD_OFFSET for b in reply.data if ECD_OFFSET <= b <= 127)
        return sorted(found)

    async def query_instances(self, ecd: int) -> list[tuple[int, int]]:
        """(instance number, instance type) of one control device."""
        reply = await self.request(CMD_QUERY_INSTANCES_BY_ADDRESS, ECD_OFFSET + ecd)
        if not reply.answered:
            return []
        d = reply.data
        return [(d[i], d[i + 1]) for i in range(0, len(d) - 3, 4)]

    async def query_ecd_label(self, ecd: int) -> str | None:
        return await self._text(CMD_QUERY_DEVICE_LABEL, ECD_OFFSET + ecd)

    async def query_instance_label(self, ecd: int, instance: int) -> str | None:
        return await self._text(CMD_QUERY_INSTANCE_LABEL, ECD_OFFSET + ecd, [0, 0, instance])

    # ------------------------------------------------------------ events
    async def query_event_unicast(self) -> tuple[int, int, str] | None:
        """(mode flags, UDP port, IP address) the controller currently sends events to."""
        reply = await self.request(CMD_QUERY_TPI_EVENT_UNICAST_ADDRESS)
        if reply.answered and len(reply.data) == 7:
            d = reply.data
            # The controller answers with the port low byte first, although SET takes it high byte first
            # (firmware 2.2.145 and 2.2.160: port 6969 = 0x1B39 comes back as 39 1B).
            return d[0], d[1] | (d[2] << 8), ".".join(str(b) for b in d[3:7])
        return None

    async def set_event_unicast(self, ip: str, port: int) -> TpiReply:
        """Tell the controller where to send events (not yet switched on)."""
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4 or any(not 0 <= x <= 255 for x in parts):
            raise ValueError(f"Not an IPv4 address: {ip}")
        data = bytes([port >> 8, port & 0xFF, *parts])
        return await self._send(CMD_SET_TPI_EVENT_UNICAST_ADDRESS, bytes([len(data)]) + data, 1)   # dynamic frame

    async def enable_events(self, mode: int) -> TpiReply:
        """Set the event mode flags (EVENTS_ENABLED | EVENTS_UNICAST ...). Not persistent on the controller."""
        return await self.request(CMD_ENABLE_TPI_EVENT_EMIT, mode & 0xFF)
