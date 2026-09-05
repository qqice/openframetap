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


async def restore_livestream(address, output, progress=lambda _:None, *, resolution=720):
    from openframetap.transport.dji_wifi_udp import list_rtmp_server_peer_ips
    from openframetap.video.rtmp_server import make_server
    from openframetap.workflows.pocket3_rtmp import Pocket3RtmpWorkflow
    from openframetap.workflows.prepare_recovery_session import run_prepare_recovery_session
    if resolution==720 and list_rtmp_server_peer_ips():
        return
    progress('正在准备 Pocket 直播…')
    phase = Pocket3RtmpWorkflow.load(Path('artifacts/private/pocket3-rtmp-workflow.json')).phase
    if phase not in {'waiting_for_rtmp','rtmp_connected','media_detected','sample_saved','stability_tested','completed'}:
        raise RuntimeError('existing approved RTMP configuration is not ready')
    root = Path('artifacts/private/approved-full-stream')
    if not all((root/(n+'.json')).is_file() for n in ('prepare','wifi','stream','start')):
        raise RuntimeError('existing RTMP proposals are missing; restore them through the project wrapper')
    make_server().start()
    from openframetap.video.formats import make_resolution_proposal
    stream_proposal=make_resolution_proposal(root/'stream.json',output/'selected-stream.json',address=address,height=resolution)
    result, ok = await run_prepare_recovery_session(address, proposal_path=root/'prepare.json',
        wifi_proposal_path=root/'wifi.json',stream_proposal_path=stream_proposal,
        start_proposal_path=root/'start.json',output_dir=output,
        confirmation_callback=lambda _:True,passive_seconds=3,response_timeout=15,wifi_response_timeout=30,
        progress_callback=lambda event:progress({'stage2_exact_response':'正在让 Pocket 连接外部 Wi-Fi…',
            'wifi_matching_response':'正在配置直播推流…','stream_start_response':'正在等待直播视频…'}.get(event,'正在建立直播连接…')))
    if not ok:
        raise RuntimeError('approved RTMP restoration failed: '+str(result.get('recovery_result')))
    deadline = time.monotonic()+15
    while not list_rtmp_server_peer_ips():
        if time.monotonic()>deadline:
            raise RuntimeError('restored RTMP publisher did not appear')
        await asyncio.sleep(0.2)


async def stop_camera_livestream(address, output):
    """Existing validated RTMP stop profile, once, with explicit response proof."""
    from openframetap.app.artifacts import JsonlWriter,write_manifest
    from openframetap.workflows.pocket3_normal import BleNormalSession
    output.mkdir(parents=True,exist_ok=True)
    writer=JsonlWriter(output/'events.jsonl')
    ble=BleNormalSession(address,writer.write)
    result={'confirmed':False,'error':None}
    try:
        for attempt in range(2):
            try:
                await ble.open_authenticated()
                break
            except Exception as exc:
                # BlueZ can remove the cached Device1 during a mode handoff.
                # Retry connection discovery once, never replay any DJI write.
                message=str(exc).lower()
                if attempt or ble.transport.fff5_write_count or not (
                    'device' in message and ('not found' in message or 'unknown object' in message)
                ):
                    raise
                await asyncio.wait_for(ble.transport.disconnect(),10)
                writer.write(dict(kind='stop_connect_retry_before_any_write',error=str(exc)))
                await asyncio.sleep(1)
                ble=BleNormalSession(address,writer.write)
        await ble.stop_livestream()
        result['confirmed']=True
    except Exception as exc:
        result['error']=str(exc)
        raise
    finally:
        try:
            await asyncio.wait_for(ble.transport.disconnect(),10)
        finally:
            result['fff5_write_count']=ble.transport.fff5_write_count
            writer.close()
            (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
            write_manifest(output)
    return result


def run_app_modes(args, output, sanitized_output):
    from openframetap.app.runtime import GtkReadOnlyApp,_load_gtk_gst
    from openframetap.app.widgets import AppWindowHost
    from openframetap.display.session import discover_active_wayland_session,gnome_overview_active,set_gnome_overview_active
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
    stream_resolution=getattr(args,'stream_resolution',720)
    environment=discover_active_wayland_session().environment()
    os.environ.update(environment)
    overview_before=gnome_overview_active(environment)
    if overview_before:
        set_gnome_overview_active(False,environment)
    host=AppWindowHost(_load_gtk_gst(),environment)
    history = []
    if not 0<=args.duration<=86400:raise ValueError('duration must be 0..86400')
    deadline = time.monotonic()+args.duration if args.duration else float('inf')
    def interrupt(signum, frame):
        host.request_exit()
    previous = signal.signal(signal.SIGTERM,interrupt)
    previous_int=signal.signal(signal.SIGINT,interrupt)
    error = None
    livestream_owned=False
    stop_attempted=False
    stop_result=None
    stop_results=[]
    try:
        while time.monotonic()<deadline and not host.exit_requested:
            owner=registry.load().get('app')
            if owner is None or owner.pid!=os.getpid():
                registry.register_pid('app',os.getpid(),sys.argv)
            Path('runtime/app-last-output.txt').write_text(str(output.relative_to(Path.cwd()))+'\n')
            part = f'mode-{len(history)+1:02d}-{current_mode}'
            host.show_transition('正在连接相机热点…' if current_mode=='normal' else '正在建立直播连接…')
            if current_mode == 'normal':
                spec = normal_gui_spec()
            else:
                livestream_owned=True
                stop_attempted=False
                host.wait(lambda:restore_livestream(args.address,output/part/'rtmp-restore',host.progress,resolution=stream_resolution),
                          asynchronous=True,cancellable=True)
                if host.exit_requested:
                    break
                url = load_fixed_stream_url(args.proposal,expected_address=args.address)
                spec = live_preview_spec(url,source='rtmp',decoder='auto',fullscreen=True,
                                         profile='low-latency',sink='gtkwayland')
            result = GtkReadOnlyApp(spec,address=args.address,private_output=output/part,
                sanitized_output=sanitized_output/part,duration_seconds=max(1,int(deadline-time.monotonic())) if args.duration else 0,
                enable_ble=not args.no_ble,control_mode=args.control_mode,
                session_mode=current_mode,artifact_root=output,
                leave_livestream=current_mode=='normal' and livestream_owned,window_host=host,
                stream_resolution=stream_resolution).run()
            history.append(result)
            if current_mode=='normal' and (result.get('normal_session') or {}).get('livestream_stopped'):
                livestream_owned=False
            mode = next_mode(result)
            if result.get('stop_reason','').startswith('quality_switch:') and not result.get('error'):
                selected=int(result['stop_reason'].split(':')[1])
                if current_mode!='livestream' or selected not in (480,720,1080):
                    raise RuntimeError('invalid monitor format switch')
                mode='livestream'
                stream_resolution=selected
            if livestream_owned and (mode!='normal' or host.exit_requested):
                host.show_transition('正在停止 Pocket 侧直播…')
                stop_attempted=True
                stop_result=host.wait(lambda:stop_camera_livestream(args.address,output/part/'rtmp-stop'),asynchronous=True)
                stop_results.append(stop_result)
                livestream_owned=False
            if mode is None or host.exit_requested:
                break
            current_mode = mode
    except (KeyboardInterrupt,asyncio.CancelledError):
        error = 'interrupted_during_mode_transition'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    finally:
        if livestream_owned and not stop_attempted:
            try:
                host.show_transition('正在停止 Pocket 侧直播并退出…')
                stop_attempted=True
                stop_result=host.wait(lambda:stop_camera_livestream(args.address,output/'exit-rtmp-stop'),asynchronous=True)
                stop_results.append(stop_result)
            except Exception as exc:
                error=error or f'Pocket RTMP stop failed: {exc}'
        registry.unregister('app')
        signal.signal(signal.SIGTERM,previous)
        signal.signal(signal.SIGINT,previous_int)
        host.close()
        if overview_before:
            set_gnome_overview_active(True,environment)
    result = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),
        error=error or next((s['error'] for s in history if s.get('error')),None),
        session_mode=current_mode, mode_history=[dict(mode=s['session_mode'],stop_reason=s['stop_reason'],
            error=s.get('error'),control=s.get('control'),normal_session=s.get('normal_session')) for s in history],
        fff5_write_count=sum(s.get('fff5_write_count',0) for s in history))
    result['rtmp_stop']=stop_result
    result['rtmp_stops']=stop_results
    result['persistent_window']=True
    result['transition_progress_ticks']=host.progress_ticks
    result['fff5_write_count']+=sum(s.get('fff5_write_count',0) for s in stop_results)
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    # Do not copy private network identifiers into the public summary.
    public = dict(error=result['error'],session_mode=current_mode,
                  modes_completed=[s['session_mode'] for s in history])
    (sanitized_output/'summary.json').write_text(json.dumps(public,indent=2)+'\n')
    write_manifest(output)
    write_manifest(sanitized_output)
    return result
