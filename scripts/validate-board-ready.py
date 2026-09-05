"""Bounded board check of unlimited GUI, RTMP movement, and fresh normal re-entry."""
import argparse
import json
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from openframetap.app import runtime,modes
    from openframetap.app.input import ControlInput
    from openframetap.cli import build_parser
    old_tick=runtime.GtkReadOnlyApp._tick
    entries={};observations=[]
    def tick(app):
        keep=old_tick(app)
        if not keep:return False
        state=app.state.snapshot()[1];control=app.live_control.snapshot()
        if control['state']=='fault':app._request_stop('validation_fault');return False
        if state.rendered_frames<120 or control['state'] not in ('armed','active'):return True
        e=entries.setdefault(app,dict(start=time.monotonic(),samples=[],last_frames=state.rendered_frames,last_change=time.monotonic(),max_stall=0))
        now=time.monotonic();elapsed=now-e['start']
        yaw=(.1 if 5<=elapsed<6 else -.1 if 10<=elapsed<11 else 0) if app.session_mode=='livestream' else 0
        app._submit_control(ControlInput(yaw=yaw,active=bool(yaw),source='bounded_board_validation',monotonic_ns=time.monotonic_ns()))
        if state.rendered_frames!=e['last_frames']:e['last_change']=now
        e['max_stall']=max(e['max_stall'],now-e['last_change'])
        e['last_frames']=state.rendered_frames
        e['samples'].append(dict(seconds=elapsed,frames=state.rendered_frames,yaw=yaw,cpu=state.rock_cpu_percent))
        limit=15 if app.session_mode=='livestream' else 5
        if elapsed<limit:return True
        samples=e['samples']
        observations.append(dict(mode=app.session_mode,unlimited=app.duration_seconds==0,
            measured_fps=(samples[-1]['frames']-samples[0]['frames'])/(samples[-1]['seconds']-samples[0]['seconds']),
            maximum_frame_stall_seconds=e['max_stall'],samples=samples))
        if len(observations)==1:app.mode_selector.set_active_id('livestream')
        elif len(observations)==2:app.mode_selector.set_active_id('normal')
        else:app._request_stop('board_validation_complete')
        return False
    def safe_tick(app):
        try:return tick(app)
        except Exception as exc:
            app._event(dict(kind='validation_error',error=repr(exc)))
            app._request_stop('validation_error');return False
    runtime.GtkReadOnlyApp._tick=safe_tick
    cli=build_parser().parse_args(['app','--session-mode','normal','--control-mode','live','--duration','0'])
    result=modes.run_app_modes(cli,args.output,Path(str(args.output).replace('artifacts/private','artifacts/sanitized')))
    (args.output/'validation.json').write_text(json.dumps(observations,indent=2)+'\n')
    from openframetap.app.artifacts import write_manifest
    write_manifest(args.output)
    print(json.dumps(dict(error=result['error'],observations=[{k:v for k,v in x.items() if k!='samples'} for x in observations]),indent=2))
    return 0 if result['error'] is None and len(observations)==3 else 1


if __name__=='__main__':raise SystemExit(main())
