"""GTK-only widgets loaded lazily: binary selector and persistent app shell."""

import asyncio
import math
import threading
import time


def selected_mode(x, width):
    return 'normal' if x < width / 2 else 'livestream'


def binary_mode_switch(Gtk, Gdk, GLib, mode, changed):
    import gi
    gi.require_version('PangoCairo','1.0')
    from gi.repository import PangoCairo
    class ModeSwitch(Gtk.DrawingArea):
        def __init__(self):
            super().__init__()
            self.mode = mode
            self.position = 0. if mode == 'normal' else 1.
            self.timer = None
            self.set_size_request(170,48)
            self.set_can_focus(True)
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
            self.connect('draw',self.draw_switch)
            self.connect('button-press-event',self.click)
            self.connect('key-press-event',self.key)
            self.connect('destroy',self.dispose)
            self.get_accessible().set_name('相机模式：左侧常规，右侧直播')

        def dispose(self,*args):
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None

        def get_active_id(self):
            return self.mode

        def set_active_id(self, value):
            if value not in ('normal','livestream'):
                raise ValueError('invalid mode selection')
            if value == self.mode:
                return
            self.mode = value
            self.origin = self.position
            self.animation_start = time.monotonic()
            if self.timer is None:
                self.timer = GLib.timeout_add(16,self.animate)
            self.queue_draw()
            changed(self)

        def animate(self):
            fraction = min(1., (time.monotonic()-self.animation_start)/0.16)
            target = 0. if self.mode=='normal' else 1.
            self.position = self.origin+(target-self.origin)*fraction
            self.queue_draw()
            if fraction>=1:
                self.timer = None
                return False
            return True

        def click(self,widget,event):
            if event.button==1:
                self.grab_focus()
                self.set_active_id(selected_mode(event.x,self.get_allocated_width()))
            return True

        def key(self,widget,event):
            if event.keyval == Gdk.KEY_Left:
                self.set_active_id('normal')
            elif event.keyval == Gdk.KEY_Right:
                self.set_active_id('livestream')
            else:
                return False
            return True

        def draw_switch(self,widget,cr):
            w,h=self.get_allocated_width(),self.get_allocated_height()
            def pill(x,y,width,height):
                r=height/2
                cr.new_sub_path();cr.arc(x+width-r,y+r,r,-math.pi/2,math.pi/2)
                cr.arc(x+r,y+r,r,math.pi/2,3*math.pi/2);cr.close_path();cr.fill()
            cr.set_source_rgb(.18,.21,.24);pill(1,5,w-2,h-10)
            cr.set_source_rgb(.16,.73,.91);pill(4+self.position*(w/2-3),8,w/2-5,h-16)
            for label,x in [('常规',w*.25),('直播',w*.75)]:
                layout=self.create_pango_layout(label)
                width,height=layout.get_pixel_size()
                cr.set_source_rgb(1,1,1);cr.move_to(x-width/2,(h-height)/2)
                PangoCairo.show_layout(cr,layout)
            return False
    return ModeSwitch()


class AppWindowHost:
    """Keep a covering surface visible while native video windows reconnect."""
    def __init__(self, bindings, environment):
        self.Gtk,self.Gdk,self.Gst,self.GLib=bindings
        Gtk=self.Gtk
        self.environment=environment
        self.exit_requested=False
        self.exit_callback=None
        self.async_loop=None
        self.async_task=None
        self.cancellable=False
        self.window=Gtk.Window(title='OpenFrameTap')
        self.window.set_default_size(1280,720)
        self.window.set_decorated(False)
        self.window.fullscreen()
        self.window.connect('delete-event',lambda *_:(self.request_exit(),True)[1])
        self.stack=Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.window.add(self.stack)
        self.content=None
        self.content_window=None
        page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=24)
        page.set_valign(Gtk.Align.CENTER);page.set_halign(Gtk.Align.CENTER)
        self.spinner=Gtk.Spinner();self.spinner.set_size_request(48,48)
        self.title=Gtk.Label(label='OpenFrameTap')
        self.detail=Gtk.Label(label='正在连接相机…')
        self.elapsed=Gtk.Label()
        button=Gtk.Button(label='退出')
        button.connect('clicked',lambda *_:self.request_exit())
        for widget in (self.title,self.spinner,self.detail,self.elapsed,button):
            page.pack_start(widget,False,False,0)
        self.stack.add_named(page,'transition')
        css=Gtk.CssProvider()
        css.load_from_data(b'window {background:#10151c;color:white;} label {color:white;font-size:20px;} button label {color:#111;}')
        Gtk.StyleContext.add_provider_for_screen(self.Gdk.Screen.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.started=time.monotonic()
        self.progress_ticks=0
        self.timer=self.GLib.timeout_add(100,self.tick)
        self.window.show_all()
        self.show_transition('正在连接相机…')

    def tick(self):
        if self.window.get_visible():
            self.progress_ticks+=1
            self.elapsed.set_text(f'已等待 {time.monotonic()-self.started:.0f} 秒')
        return True

    def request_exit(self):
        self.exit_requested=True
        if self.async_loop and self.async_task and self.cancellable:
            self.async_loop.call_soon_threadsafe(self.async_task.cancel)
        elif self.exit_callback:
            self.exit_callback()

    def show_transition(self,message):
        self.started=time.monotonic()
        self.title.set_text('OpenFrameTap · 正在处理')
        self.detail.set_text(message)
        self.elapsed.set_text('')
        self.spinner.start()
        self.stack.set_visible_child_name('transition')
        self.window.show_all()
        self.window.present()

    def progress(self,message):
        def update():
            self.detail.set_text(message)
            return False
        self.GLib.idle_add(update)

    def mount(self,root):
        self.content=root

    def show_content(self):
        self.spinner.stop()
        if self.content_window:
            self.content_window.present()
        self.window.hide()

    def unmount(self):
        self.content=None
        self.content_window=None

    def wait(self, operation, *, asynchronous=False, cancellable=False):
        """Run work off the GTK thread while progress/exit remain responsive."""
        result={}
        wait_loop=self.GLib.MainLoop()
        self.cancellable=cancellable
        def worker():
            try:
                if asynchronous:
                    async def run():
                        self.async_loop=asyncio.get_running_loop()
                        self.async_task=asyncio.current_task()
                        return await operation()
                    result['value']=asyncio.run(run())
                else:
                    result['value']=operation()
            except BaseException as exc:
                result['error']=exc
            finally:
                self.async_loop=self.async_task=None
                self.GLib.idle_add(lambda:(wait_loop.quit(),False)[1])
        thread=threading.Thread(target=worker,name='app-transition',daemon=False)
        thread.start()
        wait_loop.run()
        thread.join()
        self.cancellable=False
        if 'error' in result:
            raise result['error']
        return result.get('value')

    def close(self):
        self.GLib.source_remove(self.timer)
        self.window.destroy()
