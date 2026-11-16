"""Find zencontrol controllers on the local network (TPI Advanced, UDP 5108).

The same version query the setup form uses is sent to every broadcast address; each controller
answers from its own address, so one packet finds all of them. A controller without the
TPI Advanced licence answers too (with PAID_FEATURE) and is reported as not licensed.
Nothing is changed on the controller. This module has no Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass

from .tpi import (
    CMD_QUERY_CONTROLLER_LABEL,
    CMD_QUERY_VERSION,
    REPLY_ANSWER,
    REPLY_NAMES,
    TpiClient,
    TpiError,
    basic_payload,
    build_frame,
    checksum,
    decode_text,
)

SCAN_ROUNDS = 2             # the first packet to a sleeping device can be lost
SCAN_WAIT = 1.2             # seconds in total


@dataclass(slots=True)
class FoundController:
    host: str
    label: str = ""             # name given to the controller in zencontrol, "" when unknown
    version: str = ""           # firmware, "" when unknown
    licensed: bool = True       # False: the controller answered with an error (usually PAID_FEATURE)


class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.replies: dict[str, bytes] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if (len(data) >= 4 and len(data) == data[2] + 4 and data[0] in REPLY_NAMES
                and checksum(data[:-1]) == data[-1]):
            self.replies.setdefault(addr[0], bytes(data))

    def error_received(self, exc: Exception) -> None:
        """An address that cannot be reached must not stop the scan."""


async def scan(targets: list[str], port: int = 5108, wait: float = SCAN_WAIT) -> list[FoundController]:
    """Ask every address in targets (normally broadcast addresses) and return who answered."""
    if not targets:
        return []
    loop = asyncio.get_running_loop()
    try:
        transport, collector = await loop.create_datagram_endpoint(
            _Collector, local_addr=("0.0.0.0", 0), allow_broadcast=True)
    except OSError:
        return []
    try:
        for attempt in range(SCAN_ROUNDS):
            frame = build_frame(0xD0 + attempt, CMD_QUERY_VERSION, basic_payload())
            for target in targets:
                try:
                    transport.sendto(frame, (target, port))
                except OSError:
                    continue
            await asyncio.sleep(wait / SCAN_ROUNDS)
    finally:
        transport.close()

    found: list[FoundController] = []
    for host, raw in collector.replies.items():
        payload = raw[3:-1]
        controller = FoundController(host=host, licensed=raw[0] == REPLY_ANSWER)
        if controller.licensed:
            if len(payload) >= 3:
                controller.version = ".".join(str(b) for b in payload[:3])
            client = TpiClient(host, port)
            try:
                reply = await client.request(CMD_QUERY_CONTROLLER_LABEL)
                if reply.answered:
                    controller.label = decode_text(reply.data)
            except TpiError:
                pass
            finally:
                await client.close()
        found.append(controller)
    return sorted(found, key=lambda c: _sort_key(c.host))


def _sort_key(host: str) -> tuple[int, int | str]:
    try:
        return 0, int(ipaddress.ip_address(host))
    except ValueError:
        return 1, host
