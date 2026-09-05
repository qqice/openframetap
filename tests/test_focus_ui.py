import time
from types import SimpleNamespace

import pytest

from openframetap.app.runtime import GtkReadOnlyApp
from openframetap.app.state import AppStateSnapshot,StateStore


def fake_app(x,y):
    calls=[];marks=[]
    app=SimpleNamespace(window_host=None,content_shown=True,video_press=(x,y,time.monotonic()),
        window=SimpleNamespace(translate_coordinates=lambda target,x,y:(x,y-54)),
        video_widget=SimpleNamespace(get_allocation=lambda:SimpleNamespace(width=1280,height=576)),
        touch=None,state=StateStore(AppStateSnapshot(video_width=1280,video_height=720)),
        _event=lambda event:None)
    app._camera_action=lambda *args:(calls.append(args) or True)
    app._show_focus_marker=lambda *args:marks.append(args)
    return app,calls,marks


def test_window_capture_coordinates_are_mapped_into_video_and_show_feedback():
    app,calls,marks=fake_app(640,342)
    GtkReadOnlyApp._on_video_tap(app,None,1,640,342)
    assert calls==[('focus',.5,.5)]
    assert marks[0][:3]==(640,288,'对焦中…')
    assert app.focus_request_ns is not None


@pytest.mark.parametrize('x,y',[(30,342),(1260,342)])
def test_letterbox_tap_has_feedback_but_does_not_write(x,y):
    app,calls,marks=fake_app(x,y)
    GtkReadOnlyApp._on_video_tap(app,None,1,x,y)
    assert not calls
    assert marks[0][2]=='黑边区域'


def test_buttons_drag_and_joystick_do_not_trigger_focus():
    for x,y,release_x in [(640,690,640),(640,342,670),(100,480,100)]:
        app,calls,marks=fake_app(x,y)
        app.touch=SimpleNamespace(config=SimpleNamespace(overlay_size=340))
        GtkReadOnlyApp._on_video_tap(app,None,1,release_x,y)
        assert not calls and not marks
