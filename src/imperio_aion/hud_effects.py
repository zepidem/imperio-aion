# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, event-driven hover animation for the supplied HUD artwork."""
from collections import OrderedDict
import time
import tkinter as tk
from PIL import Image, ImageDraw, ImageFilter, ImageTk


class HoverAnimation:
    def __init__(self,widget,draw):
        self.widget=widget;self.draw=draw
        self.level=0.;self.target=0.;self.timer=None;self.closed=False
        widget.bind('<Unmap>',lambda event:self.reset(),add='+')
        widget.bind('<Destroy>',self.destroy,add='+')

    def cancel(self):
        if self.timer is not None:
            try:self.widget.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None

    def reset(self):
        self.cancel();self.target=0.;self.level=0.
        if not self.closed:self.draw()

    def destroy(self,event):
        if event.widget is self.widget:
            self.closed=True;self.cancel()

    def hover(self,active):
        target=float(bool(active))
        if self.closed or target==self.target:return
        self.cancel();self.target=target;origin=self.level
        began=time.monotonic();duration=.16 if active else .11
        def step():
            self.timer=None
            if self.closed:return
            progress=min(1.,(time.monotonic()-began)/duration)
            self.level=origin+(target-origin)*(1-(1-progress)**3)
            self.draw()
            if progress<1:self.timer=self.widget.after(16,step)
        step()


class IconPainter:
    """Original assets plus cached light; no permanent surface around an icon."""
    CACHE_LIMIT=32

    def __init__(self,widget,assets):
        self.widget=widget;self.assets=assets;self.photos=OrderedDict()
        widget.bind('<Destroy>',lambda event:self.photos.clear() if event.widget is widget else None,add='+')

    def background(self):
        return tuple(channel//257 for channel in self.widget.winfo_rgb(self.widget['bg']))

    def photo(self,key,build):
        if key not in self.photos:
            self.photos[key]=ImageTk.PhotoImage(build(),master=self.widget)
            if len(self.photos)>self.CACHE_LIMIT:self.photos.popitem(last=False)
        self.photos.move_to_end(key)
        return self.photos[key]

    @staticmethod
    def light(width,height,background,step,logo=False,underlay=None):
        scale=4
        surface=Image.new('RGBA',(width*scale,height*scale),(0,0,0,0) if underlay is not None else background+(255,))
        if not step or min(width,height)<4:
            return underlay.copy() if underlay is not None else surface.resize((width,height),Image.Resampling.LANCZOS)
        glow=Image.new('RGBA',surface.size)
        pad=6 if logo else min(4,max(1,(min(width,height)-1)//2))
        ImageDraw.Draw(glow).ellipse((pad*scale,(pad+(2 if logo else 1))*scale,
            (width-pad)*scale,(height-pad+(2 if logo else 1))*scale),
            fill=(245,192,86,round((110 if logo else 135)*step/12)))
        surface.alpha_composite(glow.filter(ImageFilter.GaussianBlur((5 if logo else 3)*scale)))
        raster=surface.resize((width,height),Image.Resampling.LANCZOS)
        if underlay is not None:
            result=underlay.convert('RGBA').copy();result.alpha_composite(raster);return result
        return raster

    def canvas(self,key,width,height,size,level=0.,selected=False,focused=False,x=0):
        canvas=self.widget;width=max(1,width);height=max(1,height)
        background=self.background();step=round(max(level,.3 if focused else 0)*12)
        provider=getattr(canvas,'background_provider',None)
        tile=provider(width,height) if provider else None
        glow=self.photo(('light',width,height,background,step,tile.tobytes() if tile is not None else None),
                        lambda:self.light(width,height,background,step,underlay=tile))
        canvas.create_image(x,0,anchor='nw',image=glow)
        artwork=self.assets.get(key,round(size*(1+.12*level)))
        if artwork:canvas.create_image(x+width/2,height/2-(1 if selected else 0),image=artwork)
        if selected or focused:
            length=10 if selected else 6
            canvas.create_line(x+(width-length)/2,height-1,x+(width+length)/2,height-1,
                fill='#FFF5E8' if focused else '#D5A65F',width=1,tags='selection' if selected else 'focus')
        return artwork

    def logo(self,level):
        # A fixed 44px footprint preserves drag/click geometry and HUD layout.
        step=round(level*12);background=self.background()
        def build():
            surface=self.light(44,44,background,step,logo=True)
            artwork=self.assets.get('mascot',round(44*(1+.08*step/12)))
            if artwork:
                raster=ImageTk.getimage(artwork).convert('RGBA')
                surface.alpha_composite(raster,((44-raster.width)//2,(44-raster.height)//2))
            return surface
        return self.photo(('logo',background,step),build)
