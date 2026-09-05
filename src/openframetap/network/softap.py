"""Temporary NetworkManager station profile, pinned routes and explicit rollback."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import uuid

from openframetap.devices.pocket3_normal import SoftAPCredentials


async def run_command(*args: str, timeout: float = 40) -> str:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
        if proc.returncode:
            # nmcli can print SSID / credential details: keep errors categorical.
            raise RuntimeError(f"{args[0]} {args[1] if len(args)>1 else ''} failed (exit {proc.returncode})")
        return out.decode(errors="replace").strip()
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


def wired_default(routes: list[dict]) -> str:
    default = sorted((r for r in routes if r.get('dst') == 'default'),
                     key=lambda r: r.get('metric', 0))
    if not default or default[0].get('dev') != 'end0':
        raise RuntimeError("normal mode requires the default route on wired end0")
    return 'end0'


def temporary_profile_args(profile_id: str, ssid: str) -> tuple[str, ...]:
    return ('sudo', '-n', 'nmcli', 'connection', 'add', 'save', 'no',
            'type', 'wifi', 'ifname', 'wlan0', 'con-name', 'openframetap-normal-' + profile_id,
            'connection.uuid', profile_id, 'connection.autoconnect', 'no', 'ssid', ssid,
            '802-11-wireless.mode', 'infrastructure',
            'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk-flags', '2',
            'ipv4.method', 'auto', 'ipv4.never-default', 'yes',
            'ipv4.ignore-auto-dns', 'yes', 'ipv4.route-metric', '700',
            'ipv6.method', 'disabled')


class TemporarySoftAP:
    def __init__(self, state_path: Path, *, runner=run_command):
        self.runner = runner
        self.state_path = state_path
        self.profile_id = str(uuid.uuid4())
        self.previous_uuid = None
        self.created = False
        self.local_ip = None

    async def preflight(self):
        wired_default(json.loads(await self.runner('ip', '-j', 'route', 'show', 'default')))
        await self.runner('sudo', '-n', 'true')
        self.previous_uuid = await self.runner('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', 'wlan0')
        if self.previous_uuid in ('', '--'):
            self.previous_uuid = None

    async def join(self, credentials: SoftAPCredentials):
        if self.state_path.exists():
            raise RuntimeError("normal-mode rollback state already exists; run normal-cleanup first")
        await self.preflight()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = dict(profile_uuid=self.profile_id, previous_uuid=self.previous_uuid)
        self.state_path.write_text(json.dumps(state), encoding='utf-8')
        self.state_path.chmod(0o600)
        # Mark before add so cancellation during creation is also cleaned up.
        self.created = True
        await self.runner(*temporary_profile_args(self.profile_id, credentials.ssid))
        runtime = Path('/run/user') / str(os.getuid())
        fd, name = tempfile.mkstemp(prefix='openframetap-nm-', dir=runtime)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write('802-11-wireless-security.psk:' + credentials.password + '\n')
            await self.runner('sudo', '-n', 'nmcli', '--wait', '30', 'connection', 'up',
                              'uuid', self.profile_id, 'ifname', 'wlan0', 'passwd-file', name)
        finally:
            Path(name).unlink(missing_ok=True)
        routes = json.loads(await self.runner('ip', '-j', 'route', 'show', 'default'))
        wired_default(routes)
        route = json.loads(await self.runner('ip', '-j', 'route', 'get', '192.168.2.1'))[0]
        if route.get('dev') != 'wlan0':
            raise RuntimeError('Pocket route does not use wlan0')
        self.local_ip = route.get('prefsrc') or route.get('src')
        if not self.local_ip or not self.local_ip.startswith('192.168.2.') or self.local_ip.endswith(('.0','.1','.255')):
            raise RuntimeError('Pocket DHCP address is not a usable 192.168.2.x station address')
        return self.local_ip

    async def cleanup(self):
        if not self.created:
            return
        # Restrict operations to our random UUID; never delete another profile.
        profiles = await self.runner('nmcli', '-t', '-f', 'UUID', 'connection', 'show')
        if self.profile_id in profiles.splitlines():
            await self.runner('sudo', '-n', 'nmcli', 'connection', 'delete', 'uuid', self.profile_id)
        if self.previous_uuid:
            await self.runner('sudo', '-n', 'nmcli', '--wait', '30', 'connection', 'up',
                              'uuid', self.previous_uuid, 'ifname', 'wlan0')
        wired_default(json.loads(await self.runner('ip', '-j', 'route', 'show', 'default')))
        self.state_path.unlink(missing_ok=True)
        self.created = False


async def recover_network():
    import fcntl
    path = Path('runtime/normal-network.json')
    path.parent.mkdir(exist_ok=True)
    with (path.parent / 'normal.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not path.exists():
            return
        state = json.loads(path.read_text())
        profile = str(uuid.UUID(state['profile_uuid']))
        profiles = await run_command('nmcli', '-t', '-f', 'UUID', 'connection', 'show')
        if profile in profiles.splitlines():
            name = await run_command('nmcli', '-g', 'connection.id', 'connection', 'show', 'uuid', profile)
            if name != 'openframetap-normal-' + profile:
                raise RuntimeError('rollback refused: profile ownership mismatch')
        manager = TemporarySoftAP(path)
        manager.profile_id = profile
        manager.previous_uuid = state.get('previous_uuid')
        if manager.previous_uuid:
            manager.previous_uuid = str(uuid.UUID(manager.previous_uuid))
        manager.created = True
        await manager.cleanup()


if __name__ == '__main__':
    asyncio.run(recover_network())
