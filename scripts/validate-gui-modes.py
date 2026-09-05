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
    parser.add_argument('--exit-live',action='store_true')
    parser.add_argument('--live-only',action='store_true')
    parser.add_argument('--reverse-cycle',action='store_true')
    options=parser.parse_args()
    original=GtkReadOnlyApp._tick
    seen=[]
    screenshots=set()
    stops={}
    rearmed=set()
    from openframetap.app.widgets import AppWindowHost
    original_transition=AppWindowHost.show_transition
    progress_capture=[]
    def show_transition(host,message):
        original_transition(host,message)
        if not progress_capture:
            progress_capture.append(True)
            def capture():
                import subprocess
                if host.stack.get_visible_child_name()=='transition':
                    subprocess.run(['gnome-screenshot','-f',str(options.output/'transition.png')],timeout=5,check=True)
                return False
            host.GLib.timeout_add(500,capture)
    AppWindowHost.show_transition=show_transition
    def tick(app):
        keep=original(app)
        if not keep:
            return False
        if app not in seen:
            seen.append(app)
        _,state=app.state.snapshot()
        if state.control_state=='FAULT':
            app._request_stop('validation_control_fault')
            return False
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
            target=1 if options.live_only else (2 if options.exit_live else 3)
            if not options.normal_only and len(seen)<target:
                app.mode_selector.set_active_id('livestream' if app.session_mode=='normal' else 'normal')
            else:
                app._request_stop('mode_cycle_validated')
            return False
        return True
    GtkReadOnlyApp._tick=tick
    args=build_parser().parse_args(['app','--session-mode','livestream' if options.live_only or options.reverse_cycle else 'normal','--control-mode','live','--duration','240'])
    result=run_app_modes(args,options.output,Path(str(options.output).replace('artifacts/private','artifacts/sanitized')))
    modes=[s['mode'] for s in result['mode_history']]
    expected=['livestream'] if options.live_only else (['normal'] if options.normal_only else (['normal','livestream'] if options.exit_live else ['normal','livestream','normal']))
    if options.reverse_cycle:expected=['livestream','normal','livestream']
    passed=(result['error'] is None and modes==expected
            and result['mode_history'][-1]['stop_reason']=='mode_cycle_validated'
            and all(s['control']['non_center_packets']==0 for s in result['mode_history'])
            and len({id(s.window_host.window) for s in seen})==1
            and result['transition_progress_ticks']>0
            and all(max(s.video_bitrate_samples or [0])>0 for s in seen))
    if options.exit_live or options.live_only or options.reverse_cycle:
        passed=passed and bool(result['rtmp_stop'] and result['rtmp_stop']['confirmed'])
    print(json.dumps(dict(passed=passed,modes=modes,error=result['error'],
        bitrate=[sum(s.video_bitrate_samples)/len(s.video_bitrate_samples) if s.video_bitrate_samples else 0 for s in seen],
        persistent_window=result['persistent_window'],progress_ticks=result['transition_progress_ticks'],rtmp_stop=result['rtmp_stop']),indent=2))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
