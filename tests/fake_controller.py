"""A small simulated zencontrol controller (TPI Advanced over UDP) for tests.

The world mirrors the demo case "Walizka testowa" as zencontrol cloud reported it:
fixtures A0-A3 without labels, A56-A63 labelled, groups 0-11 and 14.
Reply behaviour follows the TPI Advanced document: lighting commands acknowledge with
NO_ANSWER, empty labels give NO_ANSWER, a level query for an unknown address gives 0.
"""

from __future__ import annotations

import asyncio

OK, ANSWER, NO_ANSWER, ERROR = 0xA0, 0xA1, 0xA2, 0xA3


def xor(buf: bytes) -> int:
    acc = 0
    for b in buf:
        acc ^= b
    return acc


def reply(rtype: int, seq: int, data: bytes = b"") -> bytes:
    body = bytes([rtype, seq, len(data)]) + data
    return body + bytes([xor(body)])


def be16_mask(numbers) -> bytes:
    value = 0
    for n in numbers:
        value |= 1 << n
    return bytes([value >> 8, value & 0xFF])


class Light:
    def __init__(self, address, label, groups, tunable=False, rgb=0, level=0):
        self.address, self.label, self.groups = address, label, groups
        self.tunable, self.rgb, self.level = tunable, rgb, level
        self.last_active = 200
        self.kelvin = 3000
        self.rgbwaf = [254, 0, 0, 0, 0xFF, 0xFF][:6]
        self.colour_type = 0x20 if tunable else (0x80 if rgb else None)
        self.scene_levels = {0: 0, 1: 254, 2: 128, 10: 254, 11: 60}

    def set_level(self, level):
        self.level = level
        if level > 0:
            self.last_active = level


def walizka() -> tuple[dict[int, Light], dict[int, dict]]:
    lights = [
        Light(0, "", [0]), Light(1, "", [0]), Light(2, "", [0]), Light(3, "", [0]),
        Light(56, "RGBW", [6], rgb=4), Light(57, "Rolety", [5]),
        Light(58, "Tunable White", [1, 8, 9], tunable=True),
        Light(59, "L prawa", [4]), Light(60, "L środek", [4]), Light(61, "L lewa", [4], level=100),
        Light(62, "Downlight 3000K", [2, 8, 9]), Light(63, "GU10", [3, 8, 9]),
    ]
    groups = {
        0: {"label": "", "scenes": {}},
        1: {"label": "Tunable white", "scenes": {0: "Wyłącz", 1: "", 2: ""}},
        2: {"label": "Downlight", "scenes": {0: "Wyłącz", 1: ""}},
        3: {"label": "Gu10", "scenes": {0: "Wyłącz"}},
        4: {"label": "Liniowa", "scenes": {0: "Wyłącz", 1: ""}},
        5: {"label": "Rolety", "scenes": {}},
        6: {"label": "RGBW", "scenes": {0: "Wyłącz"}},
        7: {"label": "xy", "scenes": {0: "", 1: ""}},
        8: {"label": "Strefa 3 lampy", "scenes": {10: "Pełne", 11: "Relaks"}},
        9: {"label": "Akcent", "scenes": {9: "Akcent"}},
        10: {"label": "", "scenes": {}},
        11: {"label": "", "scenes": {}},
        14: {"label": "", "scenes": {}},
    }
    return {light.address: light for light in lights}, groups


# control devices of the demo case: address -> (label, [(instance number, instance type), ...])
# instance types: 1 push button, 3 occupancy sensor, 4 light sensor
ECDS = {
    1: ("panel rolety", [(0, 1), (1, 1), (2, 1)]),
    2: ("dotykowy", [(0, 1), (1, 1)]),
    3: ("klawiszowy", [(0, 1), (1, 1), (2, 1), (3, 1)]),
    4: ("PIR", [(0, 3), (1, 4)]),
    5: ("przycisk", [(0, 1)]),
    7: ("", [(3, 3)]),          # unlabelled sensor the instance query does not report (see handle)
}
MAC = bytes.fromhex("7CBACC2F402E")


class FakeController(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.lights, self.groups = walizka()
        self.ecds = dict(ECDS)
        self.event_flags = 0                 # 0x01 enabled, 0x40 unicast, 0x80 multicast off
        self.unicast = (0, "0.0.0.0")        # (port, ip) events are sent to
        self.events_sent = 0
        self.label = "Walizka testowa"
        self.version = (2, 2, 145)
        self.frames: list[bytes] = []       # every valid request received
        self.silent = False                 # never reply (controller off / wrong IP)
        self.drop_next = 0                  # swallow the next n requests (lost packets)
        self.error_all: int | None = None   # reply ERROR <code> to everything (e.g. 0xB0 PAID_FEATURE)
        self.queue_failures = 0             # reply QUEUE_FAILURE to the next n lighting commands
        self.stale_before_next = False      # send a reply with a wrong sequence number first
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0

    # ------------------------------------------------------------ plumbing
    async def start(self) -> int:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=("127.0.0.1", 0))
        self.port = self.transport.get_extra_info("sockname")[1]
        return self.port

    def stop(self) -> None:
        if self.transport:
            self.transport.close()

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) < 4 or data[0] != 0x04 or xor(data[:-1]) != data[-1]:
            if len(data) >= 2:
                self.transport.sendto(reply(ERROR, data[1], b"\x01"), addr)   # CHECKSUM
            return
        self.frames.append(bytes(data))
        if self.silent:
            return
        if self.drop_next:
            self.drop_next -= 1
            return
        seq, cmd, payload = data[1], data[2], data[3:-1]
        if self.stale_before_next:
            self.stale_before_next = False
            self.transport.sendto(reply(ANSWER, (seq - 1) & 0xFF, b"\x63"), addr)
        if self.error_all is not None:
            self.transport.sendto(reply(ERROR, seq, bytes([self.error_all])), addr)
            return
        self.transport.sendto(self.handle(seq, cmd, payload), addr)

    def emit(self, event_type: int, target: int, data: list[int]) -> bool:
        """Send one TPI event frame the way the controller does (only in unicast mode here)."""
        if self.event_flags & 0x41 != 0x41 or not self.unicast[0]:
            return False
        body = b"ZC" + MAC + bytes([target >> 8, target & 0xFF, event_type, len(data), *data])
        self.transport.sendto(body + bytes([xor(body)]), (self.unicast[1], self.unicast[0]))
        self.events_sent += 1
        return True

    def reboot(self) -> None:
        """Event settings are not persistent on the controller."""
        self.event_flags = 0
        self.unicast = (0, "0.0.0.0")

    def sent(self, command: int) -> list[bytes]:
        """Frames of one command, without the sequence number and checksum."""
        return [f[2:-1] for f in self.frames if f[2] == command]

    # ------------------------------------------------------------ world helpers
    def targets(self, wire: int) -> list[Light] | None:
        if wire == 255:
            return list(self.lights.values())
        if wire <= 63:
            return [self.lights[wire]] if wire in self.lights else None
        if 64 <= wire <= 79:
            if wire - 64 not in self.groups:
                return None
            return [light for light in self.lights.values() if wire - 64 in light.groups]
        return None

    @staticmethod
    def text(seq: int, value: str) -> bytes:
        return reply(ANSWER, seq, value.encode("utf-8")) if value else reply(NO_ANSWER, seq)

    # ------------------------------------------------------------ commands
    def handle(self, seq: int, cmd: int, p: bytes) -> bytes:
        addr = p[0]
        d = p[1:]
        light = self.lights.get(addr)

        if cmd == 0x1C:
            return reply(ANSWER, seq, bytes(self.version))
        if cmd == 0x24:
            return self.text(seq, self.label)
        if cmd == 0x1D:
            bits = bytearray(8)
            for a in self.lights:
                bits[a // 8] |= 1 << (a % 8)
            return reply(ANSWER, seq, bytes(bits))
        if cmd == 0x03:
            if addr >= 64:
                return self.text(seq, self.ecds.get(addr - 64, ("", []))[0])
            return self.text(seq, light.label) if light else reply(NO_ANSWER, seq)
        if cmd == 0x07:
            return reply(ANSWER, seq, bytes([self.event_flags]))
        if cmd == 0x08:
            self.event_flags = addr
            return reply(ANSWER, seq, bytes([self.event_flags]))
        if cmd == 0x40:                                 # dynamic frame: length, port hi, port lo, 4 IP bytes
            if len(p) != 7 or p[0] != 6:
                return reply(ERROR, seq, b"\xB1")
            self.unicast = ((p[1] << 8) | p[2], ".".join(str(b) for b in p[3:7]))
            return reply(OK, seq)
        if cmd == 0x41:
            port, ip = self.unicast
            return reply(ANSWER, seq, bytes([self.event_flags, port & 0xFF, port >> 8, *(int(x) for x in ip.split("."))]))
        if cmd == 0x16:
            found = [64 + n for n in sorted(self.ecds) if 64 + n >= d[2] and n != 7]
            return reply(ANSWER, seq, bytes(found)) if found else reply(NO_ANSWER, seq)
        if cmd == 0x0D:
            instances = self.ecds.get(addr - 64, ("", []))[1] if addr >= 64 else []
            if not instances:
                return reply(NO_ANSWER, seq)
            return reply(ANSWER, seq, b"".join(bytes([number, kind, 0x02, 0x00]) for number, kind in instances))
        if cmd == 0xB7:
            return reply(NO_ANSWER, seq)                # no instance labels on the demo case
        if cmd == 0x09:
            return reply(ANSWER, seq, bytes(sorted(self.groups)))
        if cmd == 0x01:
            return self.text(seq, self.groups[addr]["label"]) if addr in self.groups else reply(NO_ANSWER, seq)
        if cmd == 0x15:
            return reply(ANSWER, seq, be16_mask(light.groups)) if light else reply(NO_ANSWER, seq)
        if cmd == 0x1A:
            if addr not in self.groups:
                return reply(NO_ANSWER, seq)
            return reply(ANSWER, seq, be16_mask(self.groups[addr]["scenes"]))
        if cmd == 0x1B:
            label = self.groups.get(addr, {"scenes": {}})["scenes"].get(d[0], "")
            return self.text(seq, label)
        if cmd == 0xAA:
            members = self.targets(addr)
            if not members:
                return reply(ANSWER, seq, b"\x00")
            levels = {m.level for m in members}
            return reply(ANSWER, seq, bytes([levels.pop() if len(levels) == 1 else 255]))
        if cmd == 0x35:
            if not light:
                return reply(NO_ANSWER, seq)
            return reply(ANSWER, seq, bytes([(0x02 if light.tunable else 0) | (light.rgb << 5)]))
        if cmd == 0x38:
            if not light or not light.tunable:
                return reply(NO_ANSWER, seq)
            # physical 2200-6500, soft 2700-6500, step 1
            return reply(ANSWER, seq, bytes([0x08, 0x98, 0x19, 0x64, 0x0A, 0x8C, 0x19, 0x64, 0x00, 0x01]))
        if cmd == 0x34:
            if not light or light.colour_type is None:
                return reply(NO_ANSWER, seq)
            if light.colour_type == 0x20:
                return reply(ANSWER, seq, bytes([0x20, light.kelvin >> 8, light.kelvin & 0xFF]))
            return reply(ANSWER, seq, bytes([0x80, *light.rgbwaf]))

        # ---- lighting commands
        members = self.targets(addr)
        if cmd in (0xA1, 0xA2, 0xA7, 0xA9, 0xB4, 0xB5, 0x0E):
            if self.queue_failures:
                self.queue_failures -= 1
                return reply(ERROR, seq, b"\xB3")
            if members is None:
                return reply(ERROR, seq, b"\xB8")       # UNKNOWN_TARGET
        if cmd == 0xA2:
            for m in members:
                m.set_level(d[2])
            return reply(NO_ANSWER, seq)
        if cmd == 0xA9:
            for m in members:
                m.set_level(0)
            return reply(NO_ANSWER, seq)
        if cmd == 0xA7:
            for m in members:
                m.set_level(254)
            return reply(NO_ANSWER, seq)
        if cmd == 0xB5:
            for m in members:
                m.set_level(m.last_active)
            return reply(NO_ANSWER, seq)
        if cmd == 0xA1:
            if d[2] > 11:
                return reply(ERROR, seq, b"\xB1")
            for m in members:
                if d[2] in m.scene_levels:
                    m.set_level(m.scene_levels[d[2]])
            return reply(NO_ANSWER, seq)
        if cmd == 0xB4:
            for m in members:
                m.set_level(d[0])
            return reply(OK, seq)
        if cmd == 0x0E:
            if len(p) != 10:
                return reply(ERROR, seq, b"\xB1")       # the colour frame is fixed length
            arc, ctype, cdata = p[1], p[2], p[3:]
            for m in members:
                if ctype == 0x20 and m.tunable:
                    m.kelvin, m.colour_type = (cdata[0] << 8) | cdata[1], 0x20
                elif ctype == 0x80 and m.rgb:
                    m.rgbwaf = [new if new != 0xFF else old for new, old in zip(cdata[:6], m.rgbwaf)]
                    m.colour_type = 0x80
                if arc != 0xFF:
                    m.set_level(arc)
            return reply(OK, seq)
        return reply(ERROR, seq, b"\x04")               # UNKNOWN_CMD
