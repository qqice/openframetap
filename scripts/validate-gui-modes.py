"""ROCK-only physical mode-cycle check. Sends no non-center stick input."""
import argparse
import json
from pathlib import Path
import time

from openframetap.app.runtime import GtkReadOnlyApp
from openframetap.cli import build_parser
from openframetap.app.modes import run_app_modes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--normal-only',action='store_true')
    options=parser.parse_args()
    original=GtkReadOnlyApp._tick
    seen=[]
    screenshots=set()
    stops={}
    rearmed=set()
    def tick(app):
        keep=original(app)
        if not keep:
            return False
        if app not in seen:
            seen.append(app)
        _,state=app.state.snapshot()
        if state.rendered_frames<60:
            return True
        # STOP is exercised before screenshot and selector interaction.
        if state.control_state == 'ARMED' and app not in stops:
            app._control_emergency()
            stops[app]=(time.monotonic(),state.rendered_frames)
        if app in stops and state.control_state == 'DISABLED':
            since,frames=stops[app]
            if time.monotonic()-since<2 or state.rendered_frames<=frames:
                return True
            if app not in rearmed:
                rearmed.add(app)
                del stops[app]
                app._control_arm()
                return True
            if app not in screenshots:
                import subprocess
                screenshots.add(app)
                subprocess.run(['gnome-screenshot','-f',str(app.private_output/'mode-screen.png')],
                               timeout=5,check=True)
            if not options.normal_only and len(seen)<3:
                app.mode_selector.set_active_id('livestream' if app.session_mode=='normal' else 'normal')
            else:
                app._request_stop('mode_cycle_validated')
            return False
        return True
    GtkReadOnlyApp._tick=tick
    args=build_parser().parse_args(['app','--session-mode','normal','--control-mode','live','--duration','240'])
    result=run_app_modes(args,options.output,Path(str(options.output).replace('artifacts/private','artifacts/sanitized')))
    modes=[s['mode'] for s in result['mode_history']]
    passed=(result['error'] is None and modes==(['normal'] if options.normal_only else ['normal','livestream','normal'])
            and result['mode_history'][-1]['stop_reason']=='mode_cycle_validated'
            and all(s['control']['non_center_packets']==0 for s in result['mode_history']))
    print(json.dumps(dict(passed=passed,modes=modes,error=result['error']),indent=2))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
