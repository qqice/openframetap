"""ROCK/DSI GUI validation from a saved stream; no BLE or network mutation."""
import argparse
import asyncio
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args()
    from openframetap.app import modes,normal_session,runtime
    from openframetap.devices import pocket3_livestream
    from openframetap.workflows import pocket3_preview
    from openframetap.video.pipelines import offline_h264_pipeline
    from openframetap.cli import build_parser
    from openframetap.app.widgets import AppWindowHost
    seen=[];shots=[]
    class NoNetwork:
        summary={'network_restored':True,'fff5_write_count':0}
        def __init__(self,*a,**kw):pass
        def start(self,*a):pass
        def stop(self,*a):pass
    async def simulate_wait(*a,**kw):
        await asyncio.sleep(0.4)
        return {'confirmed':True,'fff5_write_count':0,'simulated':True}
    def spec(*a,**kw):
        return offline_h264_pipeline(options.input.resolve(),decoder='mppvideodec',sink='gtkwayland')
    normal_session.NormalGuiSession=NoNetwork
    normal_session.normal_gui_spec=spec
    pocket3_preview.live_preview_spec=spec
    pocket3_livestream.load_fixed_stream_url=lambda *a,**kw:'replay'
    modes.restore_livestream=simulate_wait
    modes.stop_camera_livestream=simulate_wait
    old_transition=AppWindowHost.show_transition
    def show(host,message):
        old_transition(host,message)
        if not shots:
            shots.append(True)
            def capture():
                import subprocess
                subprocess.run(['gnome-screenshot','-f',str(options.output/'transition.png')],timeout=5,check=True)
                return False
            host.GLib.timeout_add(150,capture)
    AppWindowHost.show_transition=show
    old_tick=runtime.GtkReadOnlyApp._tick
    def tick(app):
        keep=old_tick(app)
        if not keep:return False
        if app not in seen:seen.append(app)
        state=app.state.snapshot()[1]
        if state.rendered_frames>=35:
            import subprocess
            subprocess.run(['gnome-screenshot','-f',str(app.private_output/'mode-screen.png')],timeout=5,check=True)
            if len(seen)<3:
                app.mode_selector.set_active_id('livestream' if app.session_mode=='normal' else 'normal')
            else:app._request_stop('replay_validation_complete')
            return False
        return True
    runtime.GtkReadOnlyApp._tick=tick
    args=build_parser().parse_args(['app','--session-mode','normal','--control-mode','mock','--no-ble','--duration','60'])
    result=modes.run_app_modes(args,options.output,Path(str(options.output).replace('artifacts/private','artifacts/sanitized')))
    passed=(result['error'] is None and len(seen)==3 and len({id(a.window_host.window) for a in seen})==1
            and result['mode_history'][-1]['stop_reason']=='replay_validation_complete')
    print(json.dumps({'passed':passed,'validation':'local-recording-on-ROCK-no-Pocket',
                      'error':result['error'],'progress_ticks':result['transition_progress_ticks']},indent=2))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
