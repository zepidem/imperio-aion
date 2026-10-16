# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Small native controls sharing the approved rounded HUD vocabulary."""
import tkinter as tk
from tkinter import font as tkfont
from functools import lru_cache
from PIL import Image,ImageDraw,ImageTk
from hud_assets import RasterAssets
from hud_effects import HoverAnimation,IconPainter

ACTIVE_INK='#140E09'
INACTIVE_INK='#FFF5E8'


def background_rgb(widget):
    # Tk accepts system colour names that Pillow cannot parse directly.
    return tuple(channel//257 for channel in widget.winfo_rgb(widget['bg']))


@lru_cache(maxsize=256)
def surface_raster(width,height,background,fill,outline=None,radius=8):
    """Stroke-safe real arcs at 4x, sized to the allocated native control."""
    width=max(1,int(width));height=max(1,int(height));scale=4
    image=Image.new('RGB',(width*scale,height*scale),background)
    if width>2 and height>2:
        ImageDraw.Draw(image).rounded_rectangle((scale,scale,(width-1)*scale-1,(height-1)*scale-1),
            radius=min(radius,(width-2)/2,(height-2)/2)*scale,
            fill=fill,outline=outline,width=scale)
    return image.resize((width,height),Image.Resampling.LANCZOS)


def rounded_rect(canvas,x,y,w,h,r=8,**options):
    r=max(1,min(r,w/2,h/2))
    return canvas.create_polygon(x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,
        x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y,
        smooth=True,splinesteps=24,**options)


def has_focus(widget):
    # ttk creates its combobox popdown in Tcl, without a Python widget object.
    # Compare Tk paths directly rather than resolving that internal widget.
    try:return widget.tk.call('focus')==str(widget)
    except tk.TclError:return False


class SoftButton(tk.Canvas):
    def __init__(self,parent,text,command,primary=False,width=None,height=29,symbol=None,accent=False,image=None):
        self.accent=accent
        self.artwork=image
        self.symbol=symbol
        self.text=text;self.command=command;self.primary=primary;self.state='normal';self.hovered=False
        self.face=tkfont.Font(root=parent,family='Segoe UI',size=8,weight='bold' if primary else 'normal')
        super().__init__(parent,width=width or self.face.measure(text)+22,height=height,
                         bg=parent['bg'],highlightthickness=0,takefocus=True,cursor='hand2')
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<Map>',lambda event:self.draw())
        self.bind('<Enter>',lambda event:self.hover(True));self.bind('<Leave>',lambda event:self.hover(False))
        self.bind('<ButtonRelease-1>',lambda event:self.invoke() if 0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height() else None)
        self.bind('<Return>',lambda event:self.invoke());self.bind('<space>',lambda event:self.invoke())
        self.bind('<FocusIn>',lambda event:self.draw());self.bind('<FocusOut>',lambda event:self.draw())
        self.draw()

    def hover(self,value):self.hovered=value;self.draw()

    def invoke(self):
        if self.state!='disabled':return self.command()

    def configure(self,cnf=None,**kwargs):
        options=dict(cnf or {},**kwargs)
        if 'text' in options:self.text=options.pop('text')
        if 'state' in options:self.state=options.pop('state')
        if 'selected' in options:self.primary=options.pop('selected')
        result=super().configure(**options) if options else None
        if hasattr(self,'face'):self.draw()
        return result
    config=configure

    def draw(self):
        self.delete('all')
        w,h=self.allocated_size()
        disabled=self.state=='disabled'
        fill='#60462F' if self.hovered and not disabled else '#4B3524' if self.primary else '#2B2019'
        if self.accent and not disabled:fill='#E8C386' if self.hovered else '#D5A65F'
        line='#D5A65F' if has_focus(self) or self.primary else '#785839'
        raster=surface_raster(w,h,background_rgb(self),fill,line)
        if self.symbol:
            scale=4;raster=raster.resize((w*scale,h*scale));draw=ImageDraw.Draw(raster)
            cx,cy=w*scale/2,h*scale/2;ink='#FFF5E8'
            if self.symbol=='panel':
                draw.ellipse((cx-5*scale,cy-5*scale,cx+3*scale,cy+3*scale),outline=ink,width=scale)
                draw.line((cx+2*scale,cy+2*scale,cx+6*scale,cy+6*scale),fill=ink,width=scale)
            elif self.symbol=='close':
                for sign in (-1,1):draw.line((cx-3*scale,cy-sign*3*scale,cx+3*scale,cy+sign*3*scale),fill=ink,width=scale)
            elif self.symbol=='reload':
                draw.arc((cx-5*scale,cy-5*scale,cx+5*scale,cy+5*scale),40,320,fill=ink,width=scale)
                draw.line((cx+5*scale,cy-5*scale,cx+5*scale,cy-1*scale,cx+1*scale,cy-1*scale),fill=ink,width=scale)
            raster=raster.resize((w,h),Image.Resampling.LANCZOS)
        self.photo=ImageTk.PhotoImage(raster,master=self)
        self.create_image(0,0,anchor='nw',image=self.photo)
        if self.artwork:self.create_image(w/2,h/2,image=self.artwork)
        elif not self.symbol:self.create_text(w/2,h/2,text=self.text,font=self.face,fill='#A68B70' if disabled else ACTIVE_INK if self.accent else '#F0CE99' if self.primary else '#E5CDB0')

    def allocated_size(self):
        if self.winfo_ismapped():return max(1,self.winfo_width()),max(1,self.winfo_height())
        return max(1,int(self['width'])),max(1,int(self['height']))


class IconButton(SoftButton):
    """Unboxed image control; retains SoftButton actions and keyboard support."""
    def __init__(self,parent,text,command,assets,key,size,width=22,height=22):
        self.painter=None;self.animation=None;self.background_provider=None;self.icon_key=key;self.icon_size=size
        super().__init__(parent,text,command,width=width,height=height,image=assets.get(key,size))
        self.painter=IconPainter(self,assets)
        self.animation=HoverAnimation(self,self.draw);self.draw()

    def hover(self,value):
        self.hovered=value
        self.animation.hover(value and self.state!='disabled')

    def configure(self,cnf=None,**kwargs):
        result=super().configure(cnf,**kwargs)
        if self.state=='disabled' and self.animation:self.animation.reset()
        return result
    config=configure

    def draw(self):
        if not self.painter or not self.animation or not self.winfo_exists():return
        self.delete('all');w,h=self.allocated_size()
        self.artwork=self.painter.canvas(self.icon_key,w,h,self.icon_size,
            self.animation.level if self.state!='disabled' else 0,
            selected=self.primary,focused=has_focus(self))


class LogoIcon(tk.Label):
    """Animated fixed-size label; the HUD binds its existing click/drag gestures."""
    def __init__(self,parent,assets,**kwargs):
        super().__init__(parent,image=assets.get('mascot',44),borderwidth=0,cursor='hand2',**kwargs)
        self.painter=IconPainter(self,assets)
        self.animation=HoverAnimation(self,self.draw)
        self.bind('<Enter>',lambda event:self.animation.hover(True),add='+')
        self.bind('<Leave>',lambda event:self.animation.hover(False),add='+')
        self.draw()

    def draw(self):
        if self.winfo_exists():self.configure(image=self.painter.logo(self.animation.level))


class CaptureStartButton(SoftButton):
    """One start action with red readiness and green selected feedback."""
    def __init__(self,parent,command):
        self.running=False;self.pulse=0;self.pulse_timer=None
        super().__init__(parent,'Iniciar captura dos dados',command,width=232,height=42,primary=True)
        self.face.configure(size=10,weight='bold')
        self.bind('<Map>',lambda event:self.animate(),add='+')
        self.bind('<Unmap>',lambda event:self.stop_animation(),add='+')
        self.bind('<Destroy>',lambda event:self.stop_animation() if event.widget is self else None,add='+')
        self.draw()

    def configure(self,cnf=None,**kwargs):
        options=dict(cnf or {},**kwargs)
        if 'active' in options:
            self.running=bool(options.pop('active'))
            options['text']='Captura iniciada' if self.running else 'Iniciar captura dos dados'
            options['state']='disabled' if self.running else 'normal'
        return super().configure(options)
    config=configure

    def stop_animation(self):
        if self.pulse_timer is not None:
            try:self.after_cancel(self.pulse_timer)
            except tk.TclError:pass
            self.pulse_timer=None

    def animate(self):
        self.stop_animation()
        if not self.winfo_ismapped():return
        self.pulse=(self.pulse+1)%12;self.draw()
        self.pulse_timer=self.after(180,self.animate)

    def draw(self):
        self.delete('all');w,h=self.allocated_size()
        phase=abs(6-getattr(self,'pulse',0))/6
        if self.running:fill='#245A43';line='#76D9A1'
        else:fill='#69332B' if self.hovered else '#512720';line='#F9A188' if phase>.5 else '#DE7765'
        if has_focus(self):line='#FFF5E8'
        self.photo=ImageTk.PhotoImage(surface_raster(w,h,background_rgb(self),fill,line,9),master=self)
        self.create_image(0,0,anchor='nw',image=self.photo)
        self.create_text(w/2,h/2,text=self.text,font=self.face,fill='#FFF5E8')


class CaptureToggle(SoftButton):
    """Supplied ON/OFF artwork follows the confirmed collection state."""
    def __init__(self,parent,command,assets=None):
        self.running=False
        self.assets=assets if assets is not None else RasterAssets(parent)
        super().__init__(parent,'Iniciar captura',command,width=28,height=22)

    def configure(self,cnf=None,**kwargs):
        options=dict(cnf or {},**kwargs)
        if 'active' in options:
            active=bool(options.pop('active'))
            if active==self.running and not options:return
            self.running=active
            options['text']='Parar captura' if self.running else 'Iniciar captura'
        return super().configure(**options)
    config=configure

    def draw(self):
        self.delete('all');w,h=self.allocated_size()
        # Original transparent artwork, with a focus/hover outline only.
        if has_focus(self) or self.hovered:
            self.create_rectangle(1,1,w-2,h-2,outline='#FFF5E8' if has_focus(self) else '#D5A65F')
        self.artwork=self.assets.get('capture_on' if self.running else 'capture_off',
                                    max(1,min(20,w-4,h-2)))
        if self.artwork:
            self.create_image(w/2,h/2,image=self.artwork)
        else:
            self.create_text(w/2,h/2,text='ON' if self.running else 'OFF',font=self.face,
                             fill=INACTIVE_INK if self.running else ACTIVE_INK)


class OpacitySlider(tk.Canvas):
    """Persistent slider: immediate visual feedback, one save per gesture."""
    def __init__(self,parent,variable,preview,commit):
        super().__init__(parent,width=220,height=22,bg=parent['bg'],highlightthickness=0,takefocus=True,cursor='hand2')
        self.variable=variable;self.preview=preview;self.commit=commit;self.dragging=False
        self.trace=variable.trace_add('write',lambda *args:self.draw())
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<Map>',lambda event:self.draw())
        self.bind('<ButtonPress-1>',self.begin);self.bind('<B1-Motion>',self.move)
        self.bind('<ButtonRelease-1>',lambda event:self.finish())
        for key,delta in (('Left',-1),('Down',-1),('Right',1),('Up',1)):
            self.bind('<'+key+'>',lambda event,delta=delta:self.key(delta))
        self.bind('<Home>',lambda event:self.key(value=30));self.bind('<End>',lambda event:self.key(value=100))
        self.bind('<Escape>',lambda event:self.finish())
        self.bind('<FocusIn>',lambda event:self.draw());self.bind('<FocusOut>',lambda event:self.draw())
        self.draw()

    def track(self):
        width=self.winfo_width() if self.winfo_ismapped() else int(self['width'])
        return 7,max(8,width-38)

    def set_value(self,value):
        value=max(30,min(100,int(round(value))))
        if value!=self.variable.get():self.variable.set(value);self.preview()

    def begin(self,event):
        left,right=self.track()
        if not left-10<=event.x<=right+10:return
        self.focus_set();self.dragging=True;self.grab_set();self.move(event)

    def move(self,event):
        if not self.dragging:return
        left,right=self.track();self.set_value(30+(event.x-left)*70/(right-left))

    def finish(self):
        if not self.dragging:return
        self.dragging=False
        try:self.grab_release()
        except tk.TclError:pass
        self.commit()

    def key(self,delta=0,value=None):
        self.set_value(self.variable.get()+delta if value is None else value);self.commit()
        return 'break'

    def draw(self):
        self.delete('all');left,right=self.track();w=right+38
        value=self.variable.get();x=left+(right-left)*(value-30)/70
        image=Image.new('RGB',(w*4,22*4),background_rgb(self));draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((left*4,9*4,right*4,13*4),radius=8,fill='#584430')
        if x>left:draw.rounded_rectangle((left*4,9*4,x*4,13*4),radius=8,fill='#D5A65F')
        draw.ellipse(((x-6)*4,5*4,(x+6)*4,17*4),fill='#FFF5E8')
        if has_focus(self):draw.rounded_rectangle((2,2,w*4-3,22*4-3),radius=24,outline='#D5A65F',width=4)
        self.photo=ImageTk.PhotoImage(image.resize((w,22),Image.Resampling.LANCZOS),master=self)
        self.create_image(0,0,image=self.photo,anchor='nw')
        self.create_text(w-1,11,text=f'{value}%',anchor='e',font=('Segoe UI',8),fill='#FFF5E8')

    def destroy(self):
        self.finish();self.variable.trace_remove('write',self.trace);super().destroy()


class ScopeSwitch(tk.Canvas):
    def __init__(self,parent,variable,command,assets=None):
        self.assets=assets;self.images={};self.active=True
        super().__init__(parent,width=56,height=22,bg=parent['bg'],highlightthickness=0,takefocus=True,cursor='hand2')
        self.variable=variable;self.command=command
        self.painter=IconPainter(self,assets) if assets else None
        self.animations={mode:HoverAnimation(self,self.draw) for mode in ('bosses','all')}
        self.bind('<ButtonRelease-1>',lambda event:self.choose(self.mode_at(event.x)) if 0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height() else None)
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<Map>',lambda event:self.draw())
        self.bind('<Left>',lambda event:self.choose('bosses'));self.bind('<Right>',lambda event:self.choose('all'))
        self.bind('<space>',lambda event:self.choose('all' if variable.get()=='bosses' else 'bosses'))
        self.bind('<FocusIn>',lambda event:self.draw());self.bind('<FocusOut>',lambda event:self.draw())
        self.bind('<Enter>',lambda event:self.hover(self.mode_at(event.x)))
        self.bind('<Motion>',lambda event:self.hover(self.mode_at(event.x)))
        self.bind('<Leave>',lambda event:self.hover(None))
        self.trace=variable.trace_add('write',lambda *args:self.draw());self.draw()

    def hover(self,mode):
        for name,animation in self.animations.items():animation.hover(name==mode)

    def choose(self,mode):self.variable.set(mode);self.command()

    def split(self):
        width=self.winfo_width() if self.winfo_ismapped() else int(self['width'])
        return round(width/2)

    def mode_at(self,x):return 'bosses' if x<self.split() else 'all'

    def draw(self):
        self.delete('all')
        w,h=(self.winfo_width(),self.winfo_height()) if self.winfo_ismapped() else (int(self['width']),int(self['height']))
        split=self.split()
        for x,bw,mode in ((0,max(1,split-2),'bosses'),(split+2,max(1,w-split-2),'all')):
            selected=self.active and self.variable.get()==mode
            if self.painter:
                self.images[mode]=self.painter.canvas('filter_'+mode,bw,h,20,
                    self.animations[mode].level,selected=selected,
                    focused=has_focus(self) and self.variable.get()==mode,x=x)

    def destroy(self):
        self.variable.trace_remove('write',self.trace);super().destroy()
