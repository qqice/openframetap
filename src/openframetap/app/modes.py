"""Sequential camera mode lifecycle: teardown/center/rollback precedes reconnect."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time


def next_mode(result):
    if result.get('error'):
        return None
    reason = result.get('stop_reason','')
    if reason not in ('mode_switch:normal','mode_switch:livestream'):
        return None
    if (result.get('control') or {}).get('fault'):
        raise RuntimeError('control fault must be resolved before switching camera modes')
    if result.get('normal_session') and not result['normal_session'].get('network_restored'):
        raise RuntimeError('cannot switch while Pocket network rollback is incomplete')
    return reason.split(':',1)[1]


async def restore_livestream(address, output):
    from openframetap.transport.dji_wifi_udp import list_rtmp_server_peer_ips
    from openframetap.video.rtmp_server import make_server
    from openframetap.workflows.pocket3_rtmp import Pocket3RtmpWorkflow
    from openframetap.workflows.prepare_recovery_session import run_prepare_recovery_session
    if list_rtmp_server_peer_ips():
        return
    phase = Pocket3RtmpWorkflow.load(Path('artifacts/private/pocket3-rtmp-workflow.json')).phase
    if phase not in {'waiting_for_rtmp','rtmp_connected','media_detected','sample_saved','stability_tested','completed'}:
        raise RuntimeError('existing approved RTMP configuration is not ready')
    root = Path('artifacts/private/approved-full-stream')
    if not all((root/(n+'.json')).is_file() for n in ('prepare','wifi','stream','start')):
        raise RuntimeError('existing RTMP proposals are missing; restore them through the project wrapper')
    make_server().start()
    result, ok = await run_prepare_recovery_session(address, proposal_path=root/'prepare.json',
        wifi_proposal_path=root/'wifi.json',stream_proposal_path=root/'stream.json',
        start_proposal_path=root/'start.json',output_dir=output,
        confirmation_callback=lambda _:True,passive_seconds=3,response_timeout=15,wifi_response_timeout=30)
    if not ok:
        raise RuntimeError('approved RTMP restoration failed: '+str(result.get('recovery_result')))
    deadline = time.monotonic()+15
    while not list_rtmp_server_peer_ips():
        if time.monotonic()>deadline:
            raise RuntimeError('restored RTMP publisher did not appear')
        await asyncio.sleep(0.2)


def run_app_modes(args, output, sanitized_output):
    from openframetap.app.runtime import GtkReadOnlyApp
    from openframetap.app.artifacts import write_manifest
    from openframetap.app.normal_session import normal_gui_spec
    from openframetap.devices.pocket3_livestream import load_fixed_stream_url
    from openframetap.workflows.pocket3_preview import live_preview_spec
    from openframetap.video.player_process import ProcessRegistry
    output = output.resolve()
    sanitized_output = sanitized_output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sanitized_output.mkdir(parents=True, exist_ok=True)
    registry = ProcessRegistry(Path('runtime/media-processes.json'))
    current_mode = args.session_mode
    history = []
    deadline = time.monotonic()+args.duration
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM,interrupt)
    error = None
    try:
        while time.monotonic()<deadline:
            registry.register_pid('app',os.getpid(),sys.argv)
            Path('runtime/app-last-output.txt').write_text(str(output.relative_to(Path.cwd()))+'\n')
            part = f'mode-{len(history)+1:02d}-{current_mode}'
            if current_mode == 'normal':
                spec = normal_gui_spec()
            else:
                asyncio.run(restore_livestream(args.address,output/part/'rtmp-restore'))
                url = load_fixed_stream_url(args.proposal,expected_address=args.address)
                spec = live_preview_spec(url,source='rtmp',decoder='auto',fullscreen=True,
                                         profile='low-latency',sink='gtkwayland')
            result = GtkReadOnlyApp(spec,address=args.address,private_output=output/part,
                sanitized_output=sanitized_output/part,duration_seconds=max(1,int(deadline-time.monotonic())),
                enable_ble=not args.no_ble,control_mode=args.control_mode,
                session_mode=current_mode,artifact_root=output,
                leave_livestream=bool(current_mode=='normal' and history and history[-1]['session_mode']=='livestream')).run()
            history.append(result)
            mode = next_mode(result)
            if mode is None:
                break
            current_mode = mode
    except KeyboardInterrupt:
        error = 'interrupted_during_mode_transition'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    finally:
        registry.unregister('app')
        signal.signal(signal.SIGTERM,previous)
    result = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),
        error=error or next((s['error'] for s in history if s.get('error')),None),
        session_mode=current_mode, mode_history=[dict(mode=s['session_mode'],stop_reason=s['stop_reason'],
            error=s.get('error'),control=s.get('control'),normal_session=s.get('normal_session')) for s in history],
        fff5_write_count=sum(s.get('fff5_write_count',0) for s in history))
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    # Do not copy private network identifiers into the public summary.
    public = dict(error=result['error'],session_mode=current_mode,
                  modes_completed=[s['session_mode'] for s in history])
    (sanitized_output/'summary.json').write_text(json.dumps(public,indent=2)+'\n')
    write_manifest(output)
    write_manifest(sanitized_output)
    return result
