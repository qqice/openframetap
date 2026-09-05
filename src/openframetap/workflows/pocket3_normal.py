"""Bounded normal-mode session: BLE credentials, temporary SoftAP, UDP and MPP."""

import asyncio
from collections import Counter
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from openframetap.devices.pocket3 import POCKET3_PROFILE, build_set_pairing_pin_frame
from openframetap.devices.pocket3_normal import (
    NORMAL_COMMANDS, SoftAPCredentials, build_normal_frame, parse_wifi_string,
)
from openframetap.network.secrets import require_private_directory
from openframetap.network.softap import TemporarySoftAP, run_command
from openframetap.protocol.commands import PAIRING_COMMANDS, SendAuthorization
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.transport.bluez_ble import BluezBleTransport
from openframetap.transport.normal_udp import NormalUdpTransport
from openframetap.video.normal_stream import OnlineMediaAssembler, NormalVideoPlayer


class BleNormalSession:
    def __init__(self, address, event, *, transport=None):
        self.event = event
        self.transport = transport or BluezBleTransport(address, POCKET3_PROFILE, event_handler=event)
        self.pending = {}
        self.sequence = 0x9000
        self.stream = DumlStreamReassembler()
        self.counts = Counter()
        self.send_lock = asyncio.Lock()

    def notification(self, record):
        # Raw credentials, if captured by btmon, stay in its private directory.
        for item in self.stream.feed(record.data):
            if item.frame is None:
                continue
            frame = item.frame
            self.counts[f'{frame.cmd_set:02X}/{frame.cmd_id:02X}'] += 1
            key = (frame.cmd_set, frame.cmd_id, frame.sequence)
            pending = self.pending.get(key)
            if pending and frame.flags & 0x80 and frame.crc8_valid and frame.crc16_valid:
                receiver, future = pending
                if frame.sender == receiver and frame.receiver == 2 and not future.done():
                    future.set_result(frame.payload)
            if (frame.cmd_set, frame.cmd_id) not in ((7,7), (7,14)):
                self.event(dict(kind='ble_frame', **frame.to_dict()))

    async def send(self, name, *, reply=True, timeout=5):
        async with self.send_lock:
            seq = self.sequence
            self.sequence = (seq + 1) & 65535
            if name == 'set_pairing_pin':
                raw = build_set_pairing_pin_frame(sequence=seq)
                command = PAIRING_COMMANDS[name]
            else:
                raw = build_normal_frame(name, seq)
                command = NORMAL_COMMANDS[name]
            future = asyncio.get_running_loop().create_future()
            key = (command.cmd_set, command.cmd_id, seq)
            if reply:
                self.pending[key] = (command.receiver, future)
            try:
                authorization = SendAuthorization.single_command(
                    name, purpose='Pocket 3 normal-view connection',
                    approval_reference='owner normal-mode implementation request 2026-09-05')
                await asyncio.wait_for(self.transport.send_frame(raw, command=command, authorization=authorization), 5)
                if reply:
                    return await asyncio.wait_for(future, timeout)
            finally:
                self.pending.pop(key, None)

    async def credentials(self):
        await self.transport.connect()
        await self.transport.acquire_mtu()
        await self.transport.subscribe(self.notification)
        await self.send('normal_session_open', reply=False)
        status = await self.send('set_pairing_pin')
        if status != b'\x00\x01':
            raise RuntimeError('Pocket did not report already_paired; camera confirmation is required')
        # Pocket 3's 53/10 is E0 in Osmosis; its AP comes up via 00/2B.
        await asyncio.sleep(0.6)
        ssid = parse_wifi_string(await self.send('normal_get_ssid'))
        password = parse_wifi_string(await self.send('normal_get_password'), password=True)
        self.event(dict(kind='credentials_read', ssid_length=len(ssid.encode()), password_length=len(password)))
        return SoftAPCredentials(ssid, password)

    async def heartbeat(self):
        while True:
            await self.send('normal_session_keepalive', reply=False)
            await asyncio.sleep(1)


async def run_normal_session(address: str, *, seconds: int, output: Path, display=True):
    if not 1 <= seconds <= 3600:
        raise ValueError('normal-mode duration must be 1..3600 seconds')
    output = require_private_directory(output)
    if any(output.iterdir()):
        raise ValueError('normal-mode evidence directory must be empty')
    os.umask(0o077)
    # Linux flock covers the complete lifecycle, including rollback.
    import fcntl
    runtime = Path('runtime')
    runtime.mkdir(exist_ok=True)
    lock = (runtime / 'normal.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise RuntimeError('another normal-mode session owns the device')
    log = (output / 'events.jsonl').open('w', encoding='utf-8')
    def event(payload):
        log.write(json.dumps({'wall_time_utc': datetime.now(timezone.utc).isoformat(),
                              'monotonic_ns': time.monotonic_ns(), **payload}) + '\n')
        log.flush()
    ble = BleNormalSession(address, event)
    network = TemporarySoftAP(runtime / 'normal-network.json')
    udp = None
    player = NormalVideoPlayer() if display else None
    assembler = OnlineMediaAssembler()
    tasks = []
    captures = []
    capture_logs = []
    summary = dict(mode='normal', error=None, network_restored=False, live_enable_count=0,
                   software_git_head=os.environ.get('OPENFRAMETAP_GIT_HEAD', 'unknown'))
    code_hash = hashlib.sha256()
    for source in sorted(Path('src/openframetap').rglob('*.py')):
        code_hash.update(source.as_posix().encode() + b'\0' + source.read_bytes())
    summary['source_tree_sha256'] = code_hash.hexdigest()
    started = time.monotonic()
    stage = 'preflight'
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, current.cancel)
    try:
        from openframetap.video.player_process import ProcessRegistry
        if ProcessRegistry(Path('runtime/media-processes.json')).load():
            raise RuntimeError('stop the existing OpenFrameTap app/preview before normal mode')
        await network.preflight()
        # Bounded root captures are children of a new, specifically owned process group.
        for tool, filename, args in (
            ('btmon', 'btmon.txt', ('btmon', '-w', str(output / 'capture.btsnoop'))),
            ('tcpdump', 'tcpdump.txt', ('tcpdump', '-i', 'wlan0', '-s', '0', '-U', '-w',
                                      str(output / 'capture.pcap'), 'host', '192.168.2.1', 'and', 'udp', 'port', '9004')),
        ):
            stream = (output / filename).open('w')
            capture_logs.append(stream)
            proc = await asyncio.create_subprocess_exec('sudo','-n','timeout','--signal=INT',
                '--kill-after=3',str(seconds+120), *args, stdout=stream, stderr=stream, start_new_session=True)
            captures.append(proc)
        await asyncio.sleep(0.2)
        if any(p.returncode is not None for p in captures):
            raise RuntimeError('required private btmon/tcpdump capture could not start')
        stage = 'ble_credentials'
        print('Normal mode: reading Pocket 3 hotspot credentials over BLE.', flush=True)
        credentials = await ble.credentials()
        tasks.append(asyncio.create_task(ble.heartbeat()))
        stage = 'hotspot_join'
        print('Normal mode: joining temporary hotspot profile on wlan0.', flush=True)
        local_ip = await network.join(credentials)
        del credentials
        summary['hotspot_connected'] = True
        summary['local_ip'] = local_ip
        stage = 'udp_handshake'
        udp = NormalUdpTransport(local_ip, event_handler=event)
        await udp.open(handshake_timeout=3)
        summary['local_udp_port'] = udp.local_port
        print('Normal mode: UDP handshake complete; 40 Hz ACK active.', flush=True)
        if player:
            player.start()
        counts = Counter()
        registered = asyncio.Event()
        video_file = (output / 'video.h264').open('wb')
        async def receive():
            try:
                while True:
                    record = await udp.receive_datagram()
                    kind = record.data[6]
                    counts[f'{kind:02X}'] += 1
                    if kind == 2:
                        unit = assembler.feed(record.data, time.monotonic())
                        if unit:
                            raw, timestamp = unit
                            video_file.write(raw)
                            if player:
                                player.push(raw)
                    elif kind in (1,3):
                        # Reuse packet-local DUML reassembly for telemetry/enable ACK.
                        parser = DumlStreamReassembler()
                        for item in parser.feed(record.data) + parser.finish():
                            if item.frame:
                                frame = item.frame
                                if (frame.sender == 0x48 and frame.receiver == 2 and frame.cmd_set == 0
                                        and frame.cmd_id in (0x81,0x82) and frame.flags == 0x40):
                                    await udp.answer_registration(frame)
                                    if frame.cmd_id == 0x82:
                                        registered.set()
                                if (frame.cmd_set,frame.cmd_id) == (9,168) and frame.sender == 0x41 and frame.flags & 0x80:
                                    summary['enable_response'] = frame.payload.hex()
                                    if frame.payload != bytes(1):
                                        raise RuntimeError('Pocket refused normal-view enable')
                    # A continuously readable UDP socket can otherwise starve
                    # the independent 40 Hz ACK task in one asyncio thread.
                    await asyncio.sleep(0)
            finally:
                video_file.close()
        async def ack():
            while True:
                await udp.send_flow_ack_if_due()
                due = (udp.last_flow_ack_ns or time.monotonic_ns()) + 25_000_000
                await asyncio.sleep(max(0.001, (due-time.monotonic_ns())/1e9))
        tasks.extend((asyncio.create_task(receive()), asyncio.create_task(ack())))
        stage = 'application_registration'
        await udp.open_application()
        await asyncio.wait_for(registered.wait(), 5)
        await udp.enable_once()
        stage = 'monitoring'
        deadline = time.monotonic() + seconds
        listening_started = time.monotonic()
        first_picture = None
        screenshot_done = False
        first_picture_deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for task in tasks:
                if task.done():
                    task.result()
                    raise RuntimeError('normal-mode worker stopped unexpectedly')
            if player:
                player.poll()
                if player.stats['rendered_frames'] and first_picture is None:
                    first_picture = time.monotonic()
                    summary['first_picture_seconds'] = first_picture-listening_started
                if first_picture and time.monotonic()-first_picture > 3 and not screenshot_done:
                    screenshot_done = True
                    async def screenshot():
                        try:
                            await run_command('gnome-screenshot','-f',str(output/'screen.png'), timeout=5)
                        except Exception:
                            summary['screenshot_error'] = 'desktop screenshot unavailable'
                    # Short independent capture task; no image copy in streaming callbacks.
                    shot_task = asyncio.create_task(screenshot())
            await udp.presence()
            if time.monotonic() > first_picture_deadline and not assembler.stats['access_units']:
                raise RuntimeError('no complete video units after one enable; no camera format changes attempted')
            await asyncio.sleep(0.1)
        summary['udp_packet_counts'] = dict(counts)
        summary['listening_seconds'] = time.monotonic()-listening_started
        summary['success'] = bool(assembler.stats['access_units'] and (not player or player.stats['rendered_frames']))
    except asyncio.CancelledError:
        summary['error'] = 'cancelled_by_user_or_parent'
    except Exception as exc:
        # Exceptions from parsers/network helpers never include the credential value.
        summary['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        summary['last_stage'] = stage
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if 'shot_task' in locals():
            await asyncio.gather(shot_task, return_exceptions=True)
        if player:
            try:
                player.close()
            except Exception as exc:
                summary['player_cleanup_error'] = type(exc).__name__
            summary['video'] = player.stats
        if udp:
            summary['live_enable_count'] = udp.enable_count
            summary['flow_ack_count'] = udp.flow_ack_sent_count
            summary['registration_reply_count'] = udp.registration_count
            summary['udp_packet_counts'] = dict(counts) if 'counts' in locals() else {}
            await udp.close()
        try:
            await asyncio.wait_for(ble.transport.disconnect(), 10)
        except Exception as exc:
            summary['ble_cleanup_error'] = type(exc).__name__
        for proc in captures:
            if proc.returncode is None:
                with suppress(Exception):
                    await run_command('sudo','-n','kill','-INT','--',f'-{proc.pid}')
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), 5)
            if proc.returncode is None:
                await run_command('sudo','-n','kill','-KILL','--',f'-{proc.pid}')
                await proc.wait()
        for stream in capture_logs:
            stream.close()
        try:
            await network.cleanup()
            summary['network_restored'] = True
        except Exception as exc:
            summary['network_cleanup_error'] = str(exc)
        summary.update(actual_duration_seconds=time.monotonic()-started,
                       media=dict(assembler.stats), ble_command_counts=dict(ble.counts),
                       fff5_write_count=ble.transport.fff5_write_count)
        event(dict(kind='session_finished', **summary))
        log.close()
        (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        # btmon may own the capture as root; change only files inside this session.
        await run_command('sudo','-n','chown',f'{os.getuid()}:{os.getgid()}',
                          *[str(p) for p in output.iterdir() if p.is_file()])
        for p in output.iterdir():
            if p.is_file():
                p.chmod(0o600)
        with (output / 'checksums.sha256').open('w', newline='\n') as manifest:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name != 'checksums.sha256':
                    digest = hashlib.sha256()
                    with p.open('rb') as stream:
                        while chunk := stream.read(1048576):
                            digest.update(chunk)
                    manifest.write(f'{digest.hexdigest()}  {p.name}\n')
        lock.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
    return summary
