"""The network finder (tools/znajdz_kontroler.py) against the simulated controller."""

import asyncio
import importlib.util
import pathlib

from custom_components.unilight_zen_tpi_advanced import tpi

_path = pathlib.Path(__file__).parent.parent / "tools" / "znajdz_kontroler.py"
_spec = importlib.util.spec_from_file_location("znajdz_kontroler", _path)
finder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finder)

FAST = (0.2, 0.2)


def test_frame_is_the_one_the_integration_sends():
    for command in (finder.CMD_VERSION, finder.CMD_LABEL):
        assert finder.frame(7, command) == tpi.build_frame(7, command, tpi.basic_payload())
    assert finder.frame(0, 0x1C) == bytes.fromhex("04001C0000000018")


def test_only_well_formed_tpi_replies_count():
    good = bytes([0xA1, 5, 3, 2, 2, 145])
    good += bytes([finder.xor(good)])
    assert finder.is_tpi_reply(good)
    assert not finder.is_tpi_reply(good[:-1] + bytes([good[-1] ^ 1]))          # wrong checksum
    assert not finder.is_tpi_reply(bytes([0xA1, 5, 9, 2, 2, 145, 0]))           # wrong length byte
    assert not finder.is_tpi_reply(b"ZC" + bytes(12))                           # a TPI event, not a reply
    assert not finder.is_tpi_reply(b"")
    assert not finder.is_tpi_reply(b"HTTP/1.1 200 OK")


def test_network_text_the_user_may_type():
    assert finder.parse_prefix("192.168.2") == "192.168.2"
    assert finder.parse_prefix(" 192.168.2.x ") == "192.168.2"
    assert finder.parse_prefix("192.168.002.15") == "192.168.2"
    for wrong in ("", "abc", "192.168", "192.168.300", "192.168.1.1.1"):
        assert finder.parse_prefix(wrong) is None


async def test_finds_the_controller_and_reads_its_name(fake):
    found = await asyncio.to_thread(finder.scan, "127.0.0", fake.port, FAST)
    assert list(found) == ["127.0.0.1"]
    info = await asyncio.to_thread(finder.describe, "127.0.0.1", found["127.0.0.1"], fake.port)
    assert info == {"ip": "127.0.0.1", "name": "Walizka testowa", "firmware": "2.2.145", "note": ""}
    sent = {frame[2] for frame in fake.frames}
    assert sent == {0x1C, 0x24}                 # only two read-only questions, nothing is switched or set


async def test_controller_without_tpi_licence_is_still_found(fake):
    fake.error_all = 0xB0
    found = await asyncio.to_thread(finder.scan, "127.0.0", fake.port, FAST)
    info = await asyncio.to_thread(finder.describe, "127.0.0.1", found["127.0.0.1"], fake.port)
    assert "PAID_FEATURE" in info["note"] and info["firmware"] == ""


async def test_empty_network_gives_nothing(fake):
    assert await asyncio.to_thread(finder.scan, "127.0.9", fake.port, FAST) == {}
