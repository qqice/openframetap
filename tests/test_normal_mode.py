import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openframetap.devices.pocket3_normal import (
    NORMAL_COMMANDS, SoftAPCredentials, build_normal_frame, parse_wifi_string, registration_reply,
)
from openframetap.network.softap import TemporarySoftAP, temporary_profile_args, wired_default
from openframetap.protocol.commands import validate_command_frame, CommandRejected
from openframetap.protocol.dji_wifi import DjiWifiOperatorSequencer, DjiWifiBasicHeader, DjiWifiFlowStatus, DjiWifiSequenceWindow
from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame
from openframetap.transport.bluez_ble import NotificationRecord
from openframetap.transport.normal_udp import NormalUdpTransport
from openframetap.video.normal_stream import OnlineMediaAssembler
from openframetap.workflows.pocket3_normal import BleNormalSession


def test_normal_profile_uses_pocket3_captured_receiver_and_no_camera_writes():
    frame = decode_duml_frame(build_normal_frame('normal_live_enable', 19))
    assert (frame.receiver, frame.cmd_set, frame.cmd_id, frame.payload.hex()) == (
        0x41, 9, 168, '00040200000000000000')
    for name, definition in NORMAL_COMMANDS.items():
        frame = decode_duml_frame(build_normal_frame(name, 19))
        validate_command_frame(definition, frame)
        with pytest.raises(CommandRejected):
            validate_command_frame(definition, replace(frame, payload=frame.payload+b'\0'))
    assert all(c.cmd_set not in (2,4) for c in NORMAL_COMMANDS.values())


@pytest.mark.parametrize('payload', [b'', b'\0', b'\xe0', b'\0\x05abc', b'\0\x08bad\npass'])
def test_credential_parser_rejects_truncation_refusal_and_newlines(payload):
    with pytest.raises(ValueError):
        parse_wifi_string(payload, password=True)


def test_credentials_do_not_leak_in_repr():
    assert parse_wifi_string(b'\0\x08password', password=True) == 'password'
    value = b'OsmoPocket3-TEST'
    assert parse_wifi_string(b'\0'+bytes([len(value)])+value) == value.decode()
    assert 'password' not in repr(SoftAPCredentials('test', 'password'))


def test_nm_profile_is_temporary_and_cannot_take_default_or_dns():
    args = temporary_profile_args('test-uuid', 'OsmoPocket3-TEST')
    assert args[args.index('save')+1] == 'no'
    for key in ('ipv4.never-default', 'ipv4.ignore-auto-dns'):
        assert args[args.index(key)+1] == 'yes'
    assert args[args.index('connection.autoconnect')+1] == 'no'
    assert args[args.index('wifi-sec.psk-flags')+1] == '2'
    assert 'wifi-sec.psk' not in args
    with pytest.raises(RuntimeError):
        wired_default([dict(dst='default', dev='wlan0', metric=600)])
    assert wired_default([dict(dst='default', dev='wlan0', metric=600),
                          dict(dst='default', dev='end0', metric=100)]) == 'end0'


def test_rollback_deletes_only_owned_uuid_and_restores_previous(tmp_path):
    async def scenario():
        calls = []
        async def runner(*args):
            calls.append(args)
            if args[0] == 'ip':
                return '[{"dst":"default","dev":"end0","metric":100}]'
            if args[:4] == ('nmcli','-t','-f','UUID'):
                return 'owned\nother'
            return ''
        state = tmp_path / 'rollback.json'
        state.write_text('{}')
        manager = TemporarySoftAP(state, runner=runner)
        manager.created = True
        manager.profile_id = 'owned'
        manager.previous_uuid = 'previous'
        await manager.cleanup()
        assert ('sudo','-n','nmcli','connection','delete','uuid','owned') in calls
        assert not any('other' in call for call in calls)
        assert calls[-2][-4:] == ('uuid','previous','ifname','wlan0')
        assert not state.exists()
    asyncio.run(scenario())


def test_ble_query_matches_sequence_sender_and_redacts_credentials():
    async def scenario():
        events = []
        session = BleNormalSession('fake', events.append, transport=SimpleNamespace())
        future = asyncio.get_running_loop().create_future()
        session.pending[(7,14,1)] = (7,future)
        def deliver(sender, sequence):
            raw = encode_duml_frame(sender=sender, receiver=2, sequence=sequence, flags=0xC0,
                                    cmd_set=7, cmd_id=14, payload=b'\0\x08password')
            session.notification(NotificationRecord('now',1,'fff4',1,raw))
        deliver(7,2)
        deliver(8,1)
        assert not future.done()
        deliver(7,1)
        assert await future == b'\0\x08password'
        assert 'password' not in json.dumps(events)
    asyncio.run(scenario())


def test_ble_send_timeout_removes_pending_waiter():
    async def scenario():
        class Transport:
            async def send_frame(self, *a, **kw):
                return
        session = BleNormalSession('fake', lambda _: None, transport=Transport())
        with pytest.raises(asyncio.TimeoutError):
            await session.send('normal_get_ssid', timeout=0.001)
        assert session.pending == {}
    asyncio.run(scenario())


def test_udp_enable_attempt_is_single_even_if_send_fails():
    async def scenario():
        udp = NormalUdpTransport('192.168.2.2')
        assert udp.local_port == 0
        udp.sequencer = DjiWifiOperatorSequencer(1,8,0)
        async def fail(raw):
            raise OSError('mock failure')
        udp._sendto = fail
        with pytest.raises(OSError):
            await udp.enable_once()
        assert udp.enable_count == 1
        with pytest.raises(RuntimeError):
            await udp.enable_once()
        with pytest.raises(PermissionError):
            await udp.send_stick(None)
    asyncio.run(scenario())


def test_registration_response_matches_own_pocket3_capture_and_request_sequence():
    request = decode_duml_frame(encode_duml_frame(sender=0x48, receiver=2, sequence=100,
        flags=0x40, cmd_set=0, cmd_id=0x81, payload=bytes(64)))
    response = decode_duml_frame(registration_reply(request))
    assert response.sequence == 100 and response.flags == 0x80
    assert len(response.payload) == 64
    assert [(i,b) for i,b in enumerate(response.payload) if b] == [(1,65),(2,80),(3,80),(34,2),(41,2),(42,8)]
    with pytest.raises(CommandRejected):
        registration_reply(replace(request, sender=4))


def test_ethernet_capture_reader(tmp_path):
    import struct
    from openframetap.analysis.mimo_wifi import _read_udp
    from test_dji_wifi_media import _udp
    packet = bytes(12) + bytes.fromhex('0800') + _udp(b'payload')
    capture = tmp_path/'ethernet.pcap'
    capture.write_bytes(struct.pack('<IHHIIII',0xA1B2C3D4,2,4,0,0,65535,1) +
                        struct.pack('<IIII',1,0,len(packet),len(packet)) + packet)
    records,_ = _read_udp(capture)
    assert records[0].payload == b'payload'


def fragment(frame_id, index, count, data, seq=8):
    header = DjiWifiBasicHeader(20+len(data),8,1,seq,2,0,True).encode()
    return header+bytes(8)+bytes((frame_id,count|((index&1)<<7),index//2,0))+data


def test_online_emits_without_waiting_for_next_frame_and_handles_duplicates():
    raw = bytes.fromhex('0000000165')+b'picture'
    data = bytes.fromhex('000001ff')+len(raw).to_bytes(4,'little')+bytes(8)+raw
    asm = OnlineMediaAssembler()
    tail = fragment(255,1,2,data[18:])
    assert asm.feed(tail,1) is None
    assert asm.feed(tail,1.01) is None
    assert asm.feed(fragment(255,0,2,data[:18]),1.02)[0] == raw
    assert asm.feed(tail,1.03) is None
    assert asm.feed(fragment(0,0,1,data),1.04)[0] == raw
    assert asm.feed(fragment(255,0,1,data),4)[0] == raw


def test_online_drops_conflicts_lengths_and_expires_incomplete_frames():
    data = bytes.fromhex('000001ff')+(100).to_bytes(4,'little')+bytes(8)+b'x'
    asm = OnlineMediaAssembler()
    assert asm.feed(fragment(1,0,1,data),1) is None
    assert asm.stats['length_mismatch'] == 1
    assert asm.feed(fragment(2,0,2,data),1) is None
    assert asm.feed(fragment(2,0,2,data+b'y'),1.01) is None
    assert asm.stats['conflicts'] == 1
    asm.feed(fragment(3,0,2,data),1.1)
    asm.feed(fragment(4,0,2,data),2)
    assert asm.stats['incomplete_dropped'] == 1
