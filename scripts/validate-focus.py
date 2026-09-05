"""ROCK-only near/far/near focus check through GTK's event dispatch path.

No gimbal motion. Screen PNGs and exposure-normalized ROI sharpness are evidence,
not automatic proof of lens focus. Source images are never modified.
"""
import argparse
import json
from pathlib import Path
import statistics
import time


def image_measure(path,rect):
    from gi.repository import GdkPixbuf
    pix=GdkPixbuf.Pixbuf.new_from_file(str(path))
    data=pix.get_pixels();stride=pix.get_rowstride();channels=pix.get_n_channels()
    left,top,width,height=rect
    results={}
    for label,x,y,dx,dy in [('near',1/3,.5,.065,.10),('far',11/12,.5,.035,.16)]:
        x0=max(0,int(left+(x-dx)*width));x1=min(pix.get_width(),int(left+(x+dx)*width))
        y0=max(0,int(top+(y-dy)*height));y1=min(pix.get_height(),int(top+(y+dy)*height))
        gray=[]
        for row in range(y0,y1):
            line=[]
            for col in range(x0,x1):
                n=row*stride+col*channels
                line.append((data[n]*.2126+data[n+1]*.7152+data[n+2]*.0722))
            gray.append(line)
        edges=[4*gray[j][i]-gray[j-1][i]-gray[j+1][i]-gray[j][i-1]-gray[j][i+1]
               for j in range(1,len(gray)-1) for i in range(1,len(gray[0])-1)]
        mean=statistics.mean(v for row in gray for v in row)
        variance=statistics.pvariance(edges)
        pix.new_subpixbuf(x0,y0,x1-x0,y1-y0).savev(str(path.with_name(path.stem+'-'+label+'.png')),'png',[],[])
        results[label]=dict(roi=[x0,y0,x1,y1],mean=mean,laplacian_variance=variance,
                            normalized_laplacian=variance/max(1,mean*mean))
    return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--mode',choices=['normal','livestream'],default='livestream')
    args=parser.parse_args()
    from openframetap.app import runtime,modes
    from openframetap.cli import build_parser
    observations=[];progress={};points=[('near',1/3,.5),('far',11/12,.5),('near-return',1/3,.5)]
    old_tick=runtime.GtkReadOnlyApp._tick

    def picture(app,name):
        import subprocess
        p=app.private_output/(name+'.png')
        subprocess.run(['gnome-screenshot','-f',str(p)],timeout=5,check=True)
        allocation=app.video_widget.get_allocation()
        left,top=app.video_widget.translate_coordinates(app.window,0,0)
        _,state=app.state.snapshot()
        scale=min(allocation.width/state.video_width,allocation.height/state.video_height)
        w,h=state.video_width*scale,state.video_height*scale
        rect=(left+(allocation.width-w)/2,top+(allocation.height-h)/2,w,h)
        return dict(image=p.name,video_rect=rect,sharpness=image_measure(p,rect))

    def click(app,x,y):
        Gtk,Gdk,_,GLib=app.bindings
        allocation=app.video_widget.get_allocation()
        _,state=app.state.snapshot()
        scale=min(allocation.width/state.video_width,allocation.height/state.video_height)
        w,h=state.video_width*scale,state.video_height*scale
        x,y=app.video_widget.translate_coordinates(app.window,int((allocation.width-w)/2+x*w),int((allocation.height-h)/2+y*h))
        def event(kind):
            e=Gdk.Event.new(kind);e.window=app.window.get_window();e.send_event=True
            e.time=Gdk.CURRENT_TIME;e.x=x;e.y=y;e.button=1
            e.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
            Gdk.Event.put(e)
            return False
        event(Gdk.EventType.BUTTON_PRESS)
        GLib.timeout_add(100,lambda:event(Gdk.EventType.BUTTON_RELEASE))

    def tick(app):
        keep=old_tick(app)
        if not keep:return False
        state=app.state.snapshot()[1];control=app.live_control.snapshot()
        if control['state']=='fault':app._request_stop('focus_control_fault');return False
        if state.rendered_frames<180 or control['state']!='armed':return True
        entry=progress.setdefault(app,dict(index=0,at=time.monotonic(),sent=False))
        if not observations:
            observations.append(dict(name='baseline',**picture(app,'focus-baseline')))
        name,x,y=points[entry['index']]
        if not entry['sent']:
            entry.update(sent=True,at=time.monotonic(),request_ns=time.monotonic_ns(),marker_saved=False)
            click(app,x,y)
        elif time.monotonic()-entry['at']>.7 and not entry['marker_saved']:
            import subprocess
            subprocess.run(['gnome-screenshot','-f',str(app.private_output/(name+'-feedback.png'))],timeout=5,check=True)
            entry['marker_saved']=True
            if app.focus_mark is None:
                app._request_stop('focus_input_not_delivered');return False
        elif time.monotonic()-entry['at']>5:
            ok=control.get('last_action')=='focus' and control.get('last_action_result_ns',0)>entry['request_ns'] and control.get('last_action_ok')
            observations.append(dict(name=name,ack_ok=bool(ok),**picture(app,'focus-'+name)))
            if not ok:app._request_stop('focus_not_acknowledged');return False
            if entry['index']==2:
                app._request_stop('focus_validation_complete');return False
            entry.update(index=entry['index']+1,sent=False)
        return True

    def safe_tick(app):
        try:return tick(app)
        except Exception as exc:
            app._event(dict(kind='focus_validation_error',error=repr(exc)))
            app._request_stop('focus_validation_error')
            return False
    runtime.GtkReadOnlyApp._tick=safe_tick
    app_args=build_parser().parse_args(['app','--session-mode',args.mode,'--control-mode','live','--duration','120'])
    result=modes.run_app_modes(app_args,args.output,Path(str(args.output).replace('artifacts/private','artifacts/sanitized')))
    (args.output/'focus-observations.json').write_text(json.dumps(observations,indent=2)+'\n')
    from openframetap.app.artifacts import write_manifest
    write_manifest(args.output)
    reversed_focus=False
    if len(observations)==4:
        near,far,back=[entry['sharpness'] for entry in observations[1:]]
        value=lambda entry,roi:entry[roi]['normalized_laplacian']
        reversed_focus=(value(near,'near')>2*value(far,'near') and
                        value(far,'far')>2*value(near,'far') and
                        value(back,'near')>2*value(far,'near') and
                        value(far,'far')>2*value(back,'far'))
    print(json.dumps(dict(error=result['error'],optical_reversal_observed=reversed_focus,observations=observations),indent=2))
    return 0 if result['error'] is None and reversed_focus else 1


if __name__=='__main__':raise SystemExit(main())
