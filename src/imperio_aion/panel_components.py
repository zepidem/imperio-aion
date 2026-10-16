# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Native dark combat panel. Protocol arithmetic remains in the existing reader."""
from pathlib import Path
import json
import time
import tkinter as tk
from tkinter import ttk
from PIL import Image,ImageTk
from hud_controls import SoftButton,surface_raster,background_rgb,has_focus
from hud_assets import RasterAssets
from combat_charts import DamageChart,row_key,chart_series,skill_colors
from skill_catalog import SkillCatalog
from shortcuts import ACTIONS,DEFAULTS,validate,from_tk

ROOT=Path(__file__).resolve().parent
BG='#241B15';SURFACE='#30251D';RAIL='#201812';TEXT='#FFF5E8';MUTED='#D6BEA2';GOLD='#D5A65F';LINE='#513C29'

class PanelButton(SoftButton):
    def __init__(self,parent,text,command,width=135,primary=False,kind='normal',image=None,height=36):
        self.kind=kind
        super().__init__(parent,text,command,primary,width=width,height=height,image=image)
        self.face.configure(size=9,weight='bold' if primary else 'normal');self.draw()
    def configure(self,cnf=None,**kwargs):
        options=dict(cnf or {},**kwargs)
        if 'image' in options:self.artwork=options.pop('image')
        return super().configure(**options)
    config=configure
    def cget(self,key):
        if key=='text':return self.text
        if key=='state':return self.state
        return super().cget(key)
    __getitem__=cget
    def draw(self):
        if not hasattr(self,'face') or not self.winfo_exists():return
        self.delete('all');w,h=self.allocated_size();disabled=self.state=='disabled'
        fill='#493421' if self.primary else SURFACE;outline='#9c713c' if self.primary else '#77532f';ink='#f6d89e' if self.primary else TEXT
        if self.kind=='capture':fill,outline,ink=('#24432f','#5f8e57','#cde8ca') if self.primary else ('#482a24','#9c5f4b','#f0c2ad')
        elif self.kind in ('adjust','follow'):
            fill,outline,ink=('#24432f','#5f8e57','#d5edd0') if self.primary else (SURFACE,'#77532f',MUTED)
        elif self.kind=='scope':fill,outline,ink=BG,BG,GOLD if self.primary else MUTED
        elif self.kind=='nav' and not self.primary:fill,outline,ink=RAIL,RAIL,MUTED
        if self.hovered and not disabled and self.kind not in ('capture','adjust','follow','scope'):fill='#493525'
        if has_focus(self):outline=GOLD
        self.photo=ImageTk.PhotoImage(surface_raster(w,h,background_rgb(self),fill,outline),master=self)
        self.create_image(0,0,anchor='nw',image=self.photo)
        if disabled:ink='#A68B70'
        if self.artwork:
            self.create_image(22,h/2,image=self.artwork);self.create_text(44,h/2,text=self.text,anchor='w',font=self.face,fill=ink)
        elif self.kind in ('adjust','follow','capture'):
            self.create_oval(13,h/2-4,21,h/2+4,fill='#a6e3a8' if self.primary else '#9b7252',outline='')
            self.create_text(31,h/2,text=self.text,anchor='w',font=self.face,fill=ink)
        else:self.create_text(w/2,h/2,text=self.text,font=self.face,fill=ink)
        if self.kind=='scope' and self.primary:self.create_line(4,h-2,w-4,h-2,fill=GOLD,width=2)

class ScrollPage(tk.Frame):
    def __init__(self,parent):
        super().__init__(parent,bg=BG)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0)
        self.scroll=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set);self.scroll.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.body=tk.Frame(self.canvas,bg=BG,padx=24,pady=20);self.window=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))
    def wheel(self,event):
        if self.body.winfo_reqheight()>self.canvas.winfo_height():self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
    def show(self):self.tkraise()

class PanelArt:
    def __init__(self,master):self.master=master;self.images={}
    def get(self,key,size):
        cache=(key,size)
        if cache not in self.images:
            path=ROOT/'assets/panel'/(key+'.png')
            with Image.open(path) as source:
                im=source.convert('RGBA')
                if key!='logo':
                    box=json.loads((ROOT/'assets/panel/nav-manifest.json').read_text(encoding='utf-8'))[key]['visible_bbox'];im=im.crop(box)
                dims=(size,size) if isinstance(size,int) else size
                im.thumbnail(dims,Image.Resampling.LANCZOS)
                self.images[cache]=ImageTk.PhotoImage(im,master=self.master)
        return self.images[cache]

class ShortcutEditor:
    def __init__(self,app,parent):
        self.app=app;self.editing=None;self.pending=dict(app.shortcuts);self.buttons={};self.suppress_until=0
        app.label(parent,text='Atalhos',size=20,bold=True).pack(anchor='w')
        app.label(parent,text='Clique em uma combinação e pressione as novas teclas para substituir o atalho.',fg=MUTED,size=9).pack(anchor='w',pady=(8,24))
        table=tk.Frame(parent,bg=SURFACE,highlightbackground=LINE,highlightthickness=1);table.pack(fill='x')
        table.columnconfigure(0,weight=1)
        app.label(table,text='Ação',bold=True,size=9,bg=SURFACE).grid(row=0,column=0,sticky='w',padx=16,pady=12)
        app.label(table,text='Combinação',bold=True,size=9,bg=SURFACE).grid(row=0,column=1,padx=20)
        for i,(key,label) in enumerate(ACTIONS.items(),1):
            app.label(table,text=label,bg=SURFACE,bold=True).grid(row=i,column=0,sticky='w',padx=16,pady=17)
            button=PanelButton(table,self.pending[key],lambda k=key:self.listen(k),width=210);button.grid(row=i,column=1,padx=16,pady=10);self.buttons[key]=button
            button.bind('<KeyPress>',self.keypress)
            button.bind('<Return>',self.keypress);button.bind('<space>',self.keypress)
            button.bind('<FocusOut>',lambda e,k=key:self.cancel(k))
        self.message=tk.StringVar(value='Os atalhos serão atualizados depois de aplicar.')
        msg=app.label(parent,var=self.message,fg=MUTED,size=9);msg.configure(wraplength=800,justify='left');msg.pack(fill='x',pady=(18,12))
        app.label(parent,text='ESC cancela a edição de uma tecla. Não abre o Painel de Combate.',fg=MUTED,size=9).pack(anchor='w',pady=(0,12))
        controls=tk.Frame(parent,bg=BG);controls.pack(fill='x')
        PanelButton(controls,'Restaurar padrão',self.restore,width=160).pack(side='left')
        self.apply_button=PanelButton(controls,'Aplicar atalhos',self.apply,width=160,primary=True);self.apply_button.pack(side='right')
        self.state_label=app.label(parent,text='',size=10,fg=GOLD);self.state_label.pack(anchor='w',pady=(26,5))
        app.label(parent,text='As combinações aplicadas são salvas neste computador e usadas na ajuda e no rodapé da HUD.',fg=MUTED,size=9).pack(anchor='w')
        self.refresh_status()
    def refresh_status(self):self.state_label.configure(text=self.app.hud_status_text())
    def listen(self,key):
        self.cancel();self.editing=key;self.buttons[key].configure(text='Pressione as teclas…',selected=True);self.buttons[key].focus_set()
        self.message.set('Pressione a nova combinação. ESC cancela a edição.');self.apply_button.configure(state='disabled')
    def cancel(self,key=None):
        if self.editing is None or key is not None and key!=self.editing:return
        action=self.editing;self.editing=None;self.buttons[action].configure(text=self.pending[action],selected=False)
        self.check()
    def accept(self,combo):
        if self.editing is None:return
        action=self.editing;self.editing=None;self.suppress_until=time.monotonic()+.3;self.pending[action]=combo;self.buttons[action].configure(text=combo,selected=False);self.check()
    def check(self):
        try:validate(self.pending)
        except ValueError as error:self.message.set(str(error));self.apply_button.configure(state='disabled');return False
        self.apply_button.configure(state='normal');self.message.set('Clique em Aplicar atalhos para confirmar as combinações.');return True
    def keypress(self,event):
        if self.editing is None:
            if event.keysym in ('Return','space'):event.widget.invoke();return 'break'
            return None
        if event.keysym=='Escape':self.cancel();self.message.set('Edição cancelada.');return 'break'
        if event.keysym=='Tab':self.cancel();return None
        try:combo=from_tk(event)
        except ValueError as error:self.message.set(str(error));return 'break'
        if combo:self.accept(combo)
        return 'break'
    def restore(self):
        self.cancel();self.pending=dict(DEFAULTS)
        for action,button in self.buttons.items():button.configure(text=self.pending[action])
        self.check()
    def apply(self):
        self.cancel()
        if not self.check():return
        ok,message=self.app.apply_shortcuts(self.pending);self.message.set(message)
        if ok:self.refresh_status()

