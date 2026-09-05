"""ROCK-only bounded recenter/flip verification, explicitly requested by owner.

Default: one recenter then one flip in each mode. The optional displaced check
uses the existing watchdog-controlled stick for 800 ms, then one recenter in
normal mode. No capture/recording, AE changes, or speculative commands.
"""
import argparse
import json
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--recenter-displaced',action='store_true')
    parser.add_argument('--live-only',action='store_true')
    options=parser.parse_args()
    from openframetap.app import runtime,modes
    from openframetap.cli import build_parser
    observations=[]
    progress={}
    old_tick=runtime.GtkReadOnlyApp._tick

    def sample(app):
        _,state=app.state.snapshot()
        return dict(monotonic_ns=time.monotonic_ns(),yaw=state.gimbal_yaw_raw_candidate,
                    pitch=state.gimbal_pitch_raw_candidate,roll=state.gimbal_roll_raw_candidate,
                    rendered_frames=state.rendered_frames)

    def tick(app):
        keep=old_tick(app)
        if not keep:return False
        entry=progress.setdefault(app,dict(index=0,sent=False,at=time.monotonic()))
        control=app.live_control.snapshot()
        if control['state']=='fault':app._request_stop('validation_fault');return False
        if app.state.snapshot()[1].rendered_frames<90:return True
        if options.recenter_displaced and not entry.get('positioned'):
            if control['state'] not in ('armed','active'):return True
            from openframetap.app.input import ControlInput
            if 'displace_at' not in entry:
                entry['displace_at']=time.monotonic()
                entry['positioning_before']=sample(app)
            elapsed=time.monotonic()-entry['displace_at']
            app._submit_control(ControlInput(source='bounded_recenter_preparation',
                yaw=.5 if elapsed<.8 else 0,active=elapsed<.8,monotonic_ns=time.monotonic_ns()))
            if elapsed<2.8:return True
            entry.update(positioned=True,at=time.monotonic()-4)
        name=('recenter','flip')[entry['index']]
        if not entry['sent']:
            if control['state'] not in ('armed','active'):return True
            # Wait well beyond the two-second cooldown through either facade.
            if time.monotonic()-entry['at']<4:return True
            entry['before']=sample(app)
            app._camera_action(name)
            entry.update(sent=True,at=time.monotonic(),samples=[])
        else:
            entry['samples'].append(sample(app))
            elapsed=time.monotonic()-entry['at']
            if elapsed<4:return True
            ok=control.get('last_action')==name and control.get('last_action_ok') is True
            observations.append(dict(mode=app.session_mode,action=name,ack_ok=ok,
                before=entry['before'],after=sample(app),samples=entry['samples'],
                positioning_before=entry.get('positioning_before')))
            import subprocess
            subprocess.run(['gnome-screenshot','-f',str(app.private_output/f'{name}-after.png')],timeout=5,check=True)
            if not ok:app._request_stop('action_not_confirmed');return False
            if options.recenter_displaced:
                app._request_stop('displaced_recenter_validation_complete');return False
            elif entry['index']==0:
                entry.update(index=1,sent=False,at=time.monotonic())
            elif app.session_mode=='normal':
                app.mode_selector.set_active_id('livestream');return False
            else:
                app._request_stop('action_validation_complete');return False
        return True

    runtime.GtkReadOnlyApp._tick=tick
    args=build_parser().parse_args(['app','--session-mode','livestream' if options.live_only else 'normal','--control-mode','live','--duration','240'])
    result=modes.run_app_modes(args,options.output,Path(str(options.output).replace('artifacts/private','artifacts/sanitized')))
    (options.output/'action-observations.json').write_text(json.dumps(observations,indent=2)+'\n')
    from openframetap.app.artifacts import write_manifest
    write_manifest(options.output)
    expected=1 if options.recenter_displaced else (2 if options.live_only else 4)
    passed=result['error'] is None and len(observations)==expected and all(x['ack_ok'] for x in observations)
    print(json.dumps(dict(passed=passed,error=result['error'],observations=[{k:v for k,v in x.items() if k!='samples'} for x in observations]),indent=2))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
