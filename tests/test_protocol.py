"""Frames against the byte examples of the TPI Advanced document, and the UDP client."""

import pytest

from custom_components.unilight_zen_tpi_advanced import tpi
from custom_components.unilight_zen_tpi_advanced.discovery import discover


def h(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


def test_frames_match_document_examples():
    # request frames printed in the TPI Advanced document (sequence number 0)
    assert tpi.build_frame(0, 0xA2, tpi.basic_payload(1, [0, 0, 0x7F])) == h("04 00 A2 01 00 00 7F D8")   # arc level
    assert tpi.build_frame(0, 0xB4, tpi.basic_payload(1, [0, 0, 0x0A])) == h("04 00 B4 01 00 00 0A BB")   # custom fade
    assert tpi.build_frame(0, 0x15, tpi.basic_payload(0x0F)) == h("04 00 15 0F 00 00 00 1E")              # membership
    assert tpi.build_frame(0, 0x1D, tpi.basic_payload()) == h("04 00 1D 00 00 00 00 19")                  # addresses
    assert tpi.build_frame(0, 0x1A, tpi.basic_payload(2)) == h("04 00 1A 02 00 00 00 1C")                 # group scenes
    assert tpi.build_frame(0, 0x1B, tpi.basic_payload(2, [2])) == h("04 00 1B 02 02 00 00 1F")            # scene label
    assert tpi.build_frame(0, 0xAA, tpi.basic_payload(1)) == h("04 00 AA 01 00 00 00 AF")                 # query level
    assert tpi.build_frame(0, 0xB5, tpi.basic_payload(1)) == h("04 00 B5 01 00 00 00 B0")                 # last active
    assert tpi.build_frame(0, 0x34, tpi.basic_payload(0)) == h("04 00 34 00 00 00 00 30")                 # query colour
    assert tpi.build_frame(0, 0x38, tpi.basic_payload(0)) == h("04 00 38 00 00 00 00 3C")                 # Tc limits
    assert tpi.build_frame(0, 0x09, tpi.basic_payload()) == h("04 00 09 00 00 00 00 0D")                  # group numbers


def test_helpers():
    assert tpi.bitmask_be16(h("00 01")) == [0]
    assert tpi.bitmask_be16(h("00 04")) == [2]
    assert tpi.bitmask_be16(h("81 02")) == [1, 8, 15]
    assert tpi.decode_text("L środek".encode("utf-8")) == "L środek"
    assert tpi.decode_text(b"Wy\xb3\xb1cz") == "Wy\xb3\xb1cz"          # not UTF-8: falls back, never raises
    assert tpi.decode_text(b"Label\x00\x00") == "Label"
    with pytest.raises(ValueError):
        tpi.basic_payload(0, [1, 2, 3, 4])


async def test_client_queries(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.3)
    assert await client.query_version() == "2.2.145"
    assert await client.query_controller_label() == "Walizka testowa"
    assert await client.query_cg_addresses() == [0, 1, 2, 3, 56, 57, 58, 59, 60, 61, 62, 63]
    assert await client.query_device_label(60) == "L środek"
    assert await client.query_device_label(0) is None
    assert await client.query_group_numbers() == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 14]
    assert await client.query_group_label(8) == "Strefa 3 lampy"
    assert await client.query_group_membership(58) == [1, 8, 9]
    assert await client.query_scene_numbers_for_group(8) == [10, 11]
    assert await client.query_scene_label_for_group(8, 10) == "Pełne"
    assert await client.query_level(61) == 100
    assert await client.query_colour_features(58) == (True, 0)
    assert await client.query_colour_features(56) == (False, 4)
    assert await client.query_colour_features(62) == (False, 0)
    assert await client.query_colour_temp_limits(58) == (2700, 6500)
    assert await client.query_colour(58) == {"kelvin": 3000}
    assert await client.query_colour(56) == {"rgbwaf": [254, 0, 0, 0, None, None]}
    await client.close()


async def test_client_commands_and_frames(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.3)
    assert (await client.arc_level(62, 127)).type == tpi.REPLY_NO_ANSWER
    assert fake.lights[62].level == 127
    assert fake.sent(0xA2) == [h("A2 3E 00 00 7F")]
    await client.off(64 + 4)
    assert [fake.lights[a].level for a in (59, 60, 61)] == [0, 0, 0]
    await client.scene(64 + 8, 11)
    assert fake.sent(0xA1) == [h("A1 48 00 00 0B")]
    assert fake.lights[63].level == 60
    await client.custom_fade(63, 200, 300)
    assert fake.sent(0xB4) == [h("B4 3F C8 01 2C")]
    await client.colour_temperature(58, 4000, 254)
    assert fake.sent(0x0E)[-1] == h("0E 3A FE 20 0F A0 FF FF FF FF FF")      # fixed-length colour frame
    assert (fake.lights[58].kelvin, fake.lights[58].level) == (4000, 254)
    await client.colour_rgbwaf(56, [0, 254, 10, 20])
    assert fake.sent(0x0E)[-1] == h("0E 38 FF 80 00 FE 0A 14 FF FF FF")      # arc FF = colour only
    assert fake.lights[56].rgbwaf[:4] == [0, 254, 10, 20] and fake.lights[56].level == 0
    reply = await client.arc_level(20, 100)                                  # not in the database
    assert not reply.ok and reply.error_name == "UNKNOWN_TARGET"
    await client.close()


async def test_client_resilience(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.2)
    fake.drop_next = 1                          # a lost query is repeated once
    assert await client.query_version() == "2.2.145"
    fake.stale_before_next = True               # a late reply with an old sequence number is ignored
    assert await client.query_level(61) == 100
    fake.queue_failures = 2                     # QUEUE_FAILURE: nothing executed, retried with a pause
    assert (await client.arc_level(62, 50)).ok
    assert fake.lights[62].level == 50
    fake.drop_next = 1                          # a lost lighting command is NOT blindly repeated
    with pytest.raises(tpi.TpiTimeout):
        await client.arc_level(62, 10)
    assert len(fake.sent(0xA2)) == 4            # 3 for the queue failures + 1 lost
    fake.silent = True
    with pytest.raises(tpi.TpiTimeout):
        await client.query_version()
    await client.close()


async def test_send_raw_and_reply_dict(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.3)
    reply = await client.send_raw(h("04 07 1C 00 00 00 00 1F"))
    assert reply.as_dict() == {
        "type": "ANSWER", "type_hex": "0xA1", "ok": True, "data": [2, 2, 145], "data_hex": "02 02 91",
        "request_hex": "04 07 1C 00 00 00 00 1F", "response_hex": "A1 07 03 02 02 91 34",
    }
    bad = await client.send_raw(h("04 08 1C 00 00 00 00 FF"))     # wrong checksum on purpose
    assert bad.as_dict()["error"] == "CHECKSUM"
    assert (await client.request(0x24)).as_dict()["text"] == "Walizka testowa"
    await client.close()


async def test_discovery_walizka(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.3)
    st = await discover(client)
    assert (st.version, st.label) == ("2.2.145", "Walizka testowa")
    assert sorted(st.fixtures) == [56, 57, 58, 59, 60, 61, 62, 63]
    assert st.skipped_fixtures == [0, 1, 2, 3]
    assert sorted(st.groups) == [1, 2, 3, 4, 5, 6, 8, 9]
    assert st.skipped_groups == [0, 7, 10, 11, 14]
    assert st.fixtures[58].name == "Tunable White (A58)" and st.fixtures[58].tunable
    assert (st.fixtures[58].min_kelvin, st.fixtures[58].max_kelvin) == (2700, 6500)
    assert st.fixtures[56].rgb_channels == 4
    assert st.groups[8].name == "Strefa 3 lampy (G8)"
    assert st.groups[8].members == [58, 62, 63] and st.groups[8].tunable
    assert st.groups[8].scenes == {10: "Pełne", 11: "Relaks"}
    assert st.groups[1].scenes == {0: "Wyłącz", 1: "Scena 1", 2: "Scena 2"}
    assert st.groups[4].members == [59, 60, 61] and not st.groups[4].tunable

    everything = await discover(client, include_unlabelled=True)
    assert sorted(everything.fixtures) == [0, 1, 2, 3, 56, 57, 58, 59, 60, 61, 62, 63]
    assert everything.fixtures[0].name == "DALI A0"
    assert everything.groups[0].name == "DALI Group 0" and everything.groups[0].members == [0, 1, 2, 3]
    assert everything.skipped_groups == [7, 10, 11, 14]
    await client.close()


async def test_discovery_survives_single_lost_answers(fake):
    """One unanswered detail query (lost twice) must not stop the setup; a silent controller must."""
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.15)
    await client.query_version()
    original = fake.handle

    def flaky(seq, cmd, payload):
        if cmd == 0x03 and payload[0] == 62:        # the label of A62 is never answered
            return b""
        if cmd == 0x09:                             # the group list is never answered either
            return b""
        return original(seq, cmd, payload)

    fake.handle = flaky
    st = await discover(client)
    assert 62 in st.skipped_fixtures                # no label known -> hidden, the rest is complete
    assert sorted(st.fixtures) == [56, 57, 58, 59, 60, 61, 63]
    assert sorted(st.groups) == [1, 3, 4, 5, 6, 8, 9]      # rebuilt from the fixtures' memberships

    fake.handle = lambda seq, cmd, payload: original(seq, cmd, payload) if cmd in (0x1C, 0x1D) else b""
    with pytest.raises(tpi.TpiTimeout):
        await discover(client)
    await client.close()


async def test_colour_busy_is_retried(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.3)
    original, calls = fake.handle, []

    def busy_once(seq, cmd, payload):
        if cmd == 0x0E and not calls:
            calls.append(1)
            from .fake_controller import ERROR, reply
            return reply(ERROR, seq, b"\x01")
        return original(seq, cmd, payload)

    fake.handle = busy_once
    assert (await client.colour_temperature(58, 5000, 200)).ok
    assert (fake.lights[58].kelvin, fake.lights[58].level) == (5000, 200)
    await client.close()


# ------------------------------------------------------------------ events (version 0.5.0)
def test_event_command_frames_match_document_examples():
    assert tpi.build_frame(0, 0x40, bytes([6, 0x22, 0x6B, 0xC0, 0xA8, 0x0A, 0x0A])) == h("04 00 40 06 22 6B C0 A8 0A 0A 63")
    assert tpi.build_frame(0, 0x08, tpi.basic_payload(0x01)) == h("04 00 08 01 00 00 00 0D")     # enable events
    assert tpi.build_frame(0, 0x41, tpi.basic_payload()) == h("04 00 41 00 00 00 00 45")         # query unicast
    assert tpi.build_frame(0, 0x07, tpi.basic_payload()) == h("04 00 07 00 00 00 00 03")         # query state
    assert tpi.build_frame(0, 0x16, tpi.basic_payload(0, [0, 0, 0])) == h("04 00 16 00 00 00 00 12")
    assert tpi.build_frame(0, 0x0D, tpi.basic_payload(0x41)) == h("04 00 0D 41 00 00 00 48")     # instances
    assert tpi.build_frame(0, 0xB7, tpi.basic_payload(0x41, [0, 0, 1])) == h("04 00 B7 41 00 00 01 F3")


def test_event_frames_from_the_document_are_decoded():
    """Every example event frame of the TPI document, including its checksum."""
    mac = "7c:ba:cc:2f:40:2e"
    cases = [
        ("5A 43 7C BA CC 2F 40 2E 00 7B 00 01 05 2D", "button_press", 123, [5]),
        ("5A 43 7C BA CC 2F 40 2E 00 3B 03 01 FE 95", "level_change", 59, [0xFE]),
        ("5A 43 7C BA CC 2F 40 2E 00 06 04 01 F0 A1", "group_level_change", 6, [0xF0]),
        ("5A 43 7C BA CC 2F 40 2E 00 7B 05 02 05 01 2A", "scene_change", 123, [5, 1]),
        ("5A 43 7C BA CC 2F 40 2E 00 7B 06 02 05 01 29", "occupancy", 123, [5, 1]),
        ("5A 43 7C BA CC 2F 40 2E 00 3B 08 07 80 FF 00 00 00 00 00 19", "colour_change", 59, [0x80, 0xFF, 0, 0, 0, 0, 0]),
        ("5A 43 7C BA CC 2F 40 2E 00 3B 08 03 20 FF 00 BD", "colour_change", 59, [0x20, 0xFF, 0]),
        ("5A 43 7C BA CC 2F 40 2E 00 00 09 02 00 0F 56", "profile_change", 0, [0, 0x0F]),
        ("5A 43 7C BA CC 2F 40 2E 00 40 0A 02 FF 01 E4", "group_occupancy", 64, [0xFF, 1]),
        ("5A 43 7C BA CC 2F 40 2E 00 3B 0B 02 FE 00 9E", "level_change_v2", 59, [0xFE, 0]),
        ("5A 43 7C BA CC 2F 40 2E 00 3B 0B 02 00 00 60", "level_change_v2", 59, [0, 0]),
    ]
    for frame, name, target, data in cases:
        event = tpi.parse_event(h(frame))
        assert event is not None, frame
        assert (event.name, event.target, list(event.data), event.mac) == (name, target, data, mac)
    assert tpi.parse_event(h("5A 43 7C BA CC 2F 40 2E 00 7B 00 01 05 2E")) is None      # wrong checksum
    assert tpi.parse_event(h("A1 00 03 02 02 91 33")) is None                           # a command reply
    assert tpi.parse_event(h("5A 43 7C BA CC 2F 40 2E 00 7B 00 02 05 2D")) is None      # wrong length


async def test_event_and_control_device_queries(fake):
    client = tpi.TpiClient("127.0.0.1", fake.port, timeout=0.3)
    assert await client.query_event_unicast() == (0, 0, "0.0.0.0")
    assert (await client.set_event_unicast("192.168.1.57", 6969)).ok
    assert fake.sent(0x40) == [h("40 06 1B 39 C0 A8 01 39")]
    assert (await client.enable_events(0x41)).ok
    assert await client.query_event_unicast() == (0x41, 6969, "192.168.1.57")
    # as real controllers answer: the port's low byte first, the opposite of the SET command above
    assert (await client.request(0x41)).data == h("41 39 1B C0 A8 01 39")
    with pytest.raises(ValueError):
        await client.set_event_unicast("192.168.1", 6969)
    assert await client.query_ecd_addresses() == [1, 2, 3, 4, 5]
    assert await client.query_instances(4) == [(0, 3), (1, 4)]
    assert await client.query_instances(9) == []
    assert await client.query_ecd_label(4) == "PIR"
    assert await client.query_instance_label(4, 0) is None

    st = await discover(client, with_control_devices=True)
    assert sorted(st.sensors) == [(4, 0)]
    assert st.sensors[(4, 0)].device_name == "PIR (ECD4)"
    assert st.ecd_labels == {1: "panel rolety", 2: "dotykowy", 3: "klawiszowy", 4: "PIR", 5: "przycisk"}
    assert (await discover(client)).sensors == {}           # not queried while events are off
    await client.close()
