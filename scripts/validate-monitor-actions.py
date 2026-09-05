"""ROCK-only focus/capability and RTMP-resolution check; no gimbal movements."""
import argparse
import json
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live-only',action='store_true')
    options=parser.parse_args()
    from openframetap.app import runtime,modes
    from openframetap.cli import build_parser
    seen=[];focus_sent=set();times={};observations=[]
    old_tick=runtime.GtkReadOnlyApp._tick
    def tick(app):
        keep=old_tick(app)
        if not keep:return False
        if app not in seen:seen.append(app);times[app]=time.monotonic()
        _,state=app.state.snapshot()
        control=app.live_control.snapshot()
        if control['state']=='fault':app._request_stop('validation_fault');return False
        if state.rendered_frames<90:return True
        if app not in focus_sent and control.get('last_action')=='query_formats':
            app._camera_action('focus',.5,.5);focus_sent.add(app);times[app]=time.monotonic()
        if app in focus_sent and (control.get('last_action')=='focus' or time.monotonic()-times[app]>4):
            observations.append(dict(mode=app.session_mode,requested_height=app.stream_resolution,
                width=state.video_width,height=state.video_height,focus_ack=control.get('last_action_ok') if control.get('last_action')=='focus' else False,
                recording_capability_received=bool(state.recording_capability_raw)))
            import subprocess
            subprocess.run(['gnome-screenshot','-f',str(app.private_output/'monitor-ui.png')],timeout=5,check=True)
            index=len(seen)+(1 if options.live_only else 0)
            if index==1:app.mode_selector.set_active_id('livestream')
            elif index==2:app._request_stop('quality_switch:480')
            elif index==3:app._request_stop('quality_switch:1080')
            else:app._request_stop('monitor_validation_complete')
            return False
        return True
    runtime.GtkReadOnlyApp._tick=tick
    args=build_parser().parse_args(['app','--session-mode','livestream' if options.live_only else 'normal','--control-mode','live','--duration','300'])
    result=modes.run_app_modes(args,options.output,Path(str(options.output).replace('artifacts/private','artifacts/sanitized')))
    (options.output/'monitor-observations.json').write_text(json.dumps(observations,indent=2)+'\n')
    from openframetap.app.artifacts import write_manifest
    write_manifest(options.output)
    passed=result['error'] is None and len(observations)==(3 if options.live_only else 4)
    if passed:passed=all(x['height']==x['requested_height'] for x in observations if x['mode']=='livestream')
    print(json.dumps(dict(passed=passed,error=result['error'],observations=observations),indent=2))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
