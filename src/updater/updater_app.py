# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Native, unprivileged Império AION updater UI.

The interface displays genuine service state. No simulation controls, raw JSON,
user data migration, capture start, or privileged Python process is included.
"""
from __future__ import annotations
from pathlib import Path
import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
from PIL import Image, ImageTk
from updater_view import BUSY, present
from startup_flow import startup_action, apply_startup_view

RES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
HOME = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else RES
BG = '#241B15'
SURFACE = '#30251D'
TEXT = '#FFF5E8'
MUTED = '#D6BEA2'
GOLD = '#D5A65F'
GOLD_LIGHT = '#F0CF96'
LINE = '#62472E'
GREEN = '#B5DFAF'
GREEN_BG = '#263D2B'
RED = '#EFB5A5'
RED_BG = '#432A24'


def non_admin():
    from update_core import UpdateError
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise UpdateError('Abra imperioaion.exe normalmente, sem Executar como administrador. O Windows pode pedir permissão durante a instalação ou a abertura.')


def default_data():
    from update_core import UpdateError
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buffer) != 0:
        raise UpdateError('A pasta local da sua conta do Windows está indisponível.')
    return Path(buffer.value) / 'ImperioBDO-Atualizador'


def rounded(canvas, x1, y1, x2, y2, radius, **options):
    return canvas.create_polygon(
        x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
        x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
        x1, y2, x1, y2-radius, x1, y1+radius, x1, y1,
        smooth=True, splinesteps=18, **options)


def draw_icon(canvas, name, x, y, size=20, color=GOLD_LIGHT, width=1.7):
    """Small authored line icons; consistent stroke, independent of glyph fonts."""
    scale = size / 24
    def line(*points):
        coords = []
        for i, point in enumerate(points):
            coords.append((x if i % 2 == 0 else y) + point * scale)
        canvas.create_line(*coords, fill=color, width=width, capstyle='round', joinstyle='round')
    def polygon(*points):
        coords = [(x if i % 2 == 0 else y) + point * scale for i, point in enumerate(points)]
        canvas.create_polygon(*coords, fill='', outline=color, width=width, joinstyle='round')
    if name == 'download':
        line(12, 3, 12, 15); line(7, 10, 12, 15, 17, 10); line(4, 16, 4, 21, 20, 21, 20, 16)
    elif name == 'check':
        line(5, 12, 10, 17, 20, 6)
    elif name == 'shield':
        polygon(12, 3, 21, 6, 20, 15, 17, 19, 12, 22, 7, 19, 4, 15, 3, 6)
        line(8, 12, 11, 15, 17, 9)
    elif name == 'window':
        polygon(3, 4, 21, 4, 21, 21, 3, 21); line(3, 9, 21, 9); line(13, 13, 18, 18); line(18, 13, 13, 18)
    elif name == 'warning':
        polygon(12, 3, 22, 21, 2, 21); line(12, 9, 12, 14); line(12, 17, 12, 17.3)
    elif name == 'play':
        polygon(7, 4, 20, 12, 7, 20)
    elif name == 'chevron':
        line(6, 9, 12, 15, 18, 9)
    elif name == 'chevron-up':
        line(6, 15, 12, 9, 18, 15)
    elif name == 'arrow':
        line(3, 12, 21, 12); line(15, 6, 21, 12, 15, 18)
    elif name == 'settings':
        line(3, 7, 21, 7); line(3, 17, 21, 17)
        for cx, cy in ((8, 7), (16, 17)):
            canvas.create_oval(x+(cx-2.5)*scale, y+(cy-2.5)*scale, x+(cx+2.5)*scale, y+(cy+2.5)*scale, fill=SURFACE, outline=color, width=width)
    else:
        canvas.create_arc(x+4*scale, y+4*scale, x+20*scale, y+20*scale, start=35, extent=285, outline=color, width=width, style='arc')
        line(18, 2, 20, 7, 15, 7)


class Button(tk.Canvas):
    def __init__(self, parent, text, command, width=180, *, primary=False, icon=None, flat=False):
        super().__init__(parent, width=width, height=42, bg=BG, highlightthickness=0,
                         takefocus=True, cursor='hand2', bd=0)
        self.text = text; self.command = command; self.primary = primary
        self.icon = icon; self.flat = flat; self.enabled = True
        self.focused = False; self.hovered = False; self.pressed = False
        self.bind('<Configure>', lambda _: self.draw())
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Return>', lambda _: self.invoke())
        self.bind('<space>', lambda _: self.invoke())
        self.bind('<FocusIn>', lambda _: self.focus(True))
        self.bind('<FocusOut>', lambda _: self.focus(False))
        self.bind('<Enter>', lambda _: self.hover(True))
        self.bind('<Leave>', lambda _: self.hover(False))
    def focus(self, value): self.focused = value; self.draw()
    def hover(self, value): self.hovered = value; self.draw()
    def press(self, _):
        if self.enabled: self.focus_set(); self.pressed = True; self.draw()
    def release(self, event):
        pressed = self.pressed; self.pressed = False; self.draw()
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height(): self.invoke()
    def invoke(self):
        if self.enabled:
            self.focus_set(); self.command()
    def configure_action(self, text, command, *, enabled=True, icon=None):
        self.text = text; self.command = command; self.icon = icon
        if not self.primary and not self.flat:
            from tkinter import font as tkfont
            self.configure(width=max(116,tkfont.Font(root=self,font=('Segoe UI',10)).measure(text)+28))
        self.set_enabled(enabled)
    def set_enabled(self, value):
        self.enabled = bool(value); self.configure(cursor='hand2' if self.enabled else '')
        self.draw()
    def draw(self):
        self.delete('all'); width=max(self.winfo_width(), int(self.cget('width'))); height=max(self.winfo_height(), 42)
        fill = BG if self.flat else GOLD if self.primary and self.enabled else '#4C3B29' if self.primary else BG
        if self.hovered and self.enabled: fill = '#E6BB7B' if self.primary else SURFACE
        if self.pressed and self.enabled: fill = '#C69A57' if self.primary else '#423124'
        outline = GOLD_LIGHT if self.focused else fill if self.flat else GOLD if self.primary and self.enabled else LINE
        rounded(self, 2, 2, width-2, height-2, 10, fill=fill, outline=outline, width=2 if self.focused else 1)
        color = BG if self.primary and self.enabled else TEXT if self.enabled else '#D7BDA0'
        if self.flat: color = GOLD_LIGHT if self.hovered else MUTED
        font = ('Segoe UI', 10, 'bold' if self.primary else 'normal')
        if self.flat:
            self.create_text(3 if not self.focused else 8, height/2, text=self.text, fill=color, font=font, anchor='w')
            if self.icon: draw_icon(self, self.icon, width-26, (height-18)/2, 18, color)
        else:
            from tkinter import font as tkfont
            text_width=tkfont.Font(root=self, font=font).measure(self.text)
            total=text_width+(25 if self.icon else 0); left=(width-total)/2
            if self.icon: draw_icon(self, self.icon, left, (height-18)/2, 18, color)
            self.create_text(left+(25 if self.icon else 0), height/2, text=self.text, fill=color, font=font, anchor='w')


class ProgressBar(tk.Canvas):
    """Seven-pixel branded track; indeterminate animation never claims a percent."""
    def __init__(self, parent):
        super().__init__(parent, height=8, bg=BG, highlightthickness=0, bd=0)
        self.mode='indeterminate'; self.value=0; self.timer=None; self.offset=0
        self.bind('<Configure>', lambda _: self.draw())
    def configure_progress(self, *, mode, value=0):
        self.mode=mode; self.value=value; self.draw()
    def draw(self):
        self.delete('all'); width=max(self.winfo_width(),2)
        rounded(self,0,0,width,7,3.5,fill='#503D2B',outline='')
        if self.mode=='determinate':
            right=width*max(0,min(100,self.value))/100
            if right>0:rounded(self,0,0,right,7,min(3.5,right/2),fill=GOLD,outline='')
        else:
            left=(self.offset/100)*width-width*.34;right=left+width*.34
            if right>0 and left<width:rounded(self,max(0,left),0,min(width,right),7,3.5,fill=GOLD,outline='')
    def start(self,interval=18):
        if self.timer is not None:return
        def tick():
            self.offset=(self.offset+2)%135;self.draw()
            self.timer=self.after(interval,tick)
        self.timer=self.after(interval,tick)
    def stop(self):
        if self.timer is not None:
            self.after_cancel(self.timer);self.timer=None


class RoundedPanel(tk.Canvas):
    def __init__(self, parent, *, height=52, fill=SURFACE):
        super().__init__(parent, height=height, bg=BG, highlightthickness=0, bd=0)
        self.fill=fill
        self.bind('<Configure>', self.draw)
    def draw(self, _=None):
        self.delete('surface')
        rounded(self, 0, 0, self.winfo_width(), self.winfo_height(), 10, fill=self.fill, outline='', tags='surface')
        self.tag_lower('surface')


class Updater:
    """Rendering is inert by default, including --check-ui and visual fixtures."""
    def __init__(self, root, data, *, service=None, autostart=False, launch_mode=False, local_channel=None, windows_startup=False):
        self.root=root; self.data=Path(data); self.service=service
        self.launch_mode=launch_mode;self.local_channel=local_channel
        self.windows_startup=windows_startup
        from install_preferences import load
        choices=load(self.data)
        import network_preferences
        self.automatic_checks=tk.BooleanVar(root,value=network_preferences.load(self.data))
        self.install_choices={key:tk.BooleanVar(root,value=value) for key,value in choices.items()}
        self.check_finished=False;self.launch_requested=False;self.shortcuts_attempted=False
        self.events=queue.Queue(); self.busy=False; self.closed=False
        self.details_open=False; self.worker=None; self.snapshot={'phase':'idle'}
        self.status=tk.StringVar(value='Conferindo o aplicativo')
        self.poll_id=None; self.check_id=None
        root.title('Império AION · Instalação e atualizações')
        root.geometry('780x740'); root.minsize(710,720); root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        try: root.iconbitmap(str(RES/'imperioaion.ico'))
        except tk.TclError: pass
        self.style=ttk.Style(root); self.style.theme_use('clam')
        self.style.configure('Imperio.Horizontal.TProgressbar', troughcolor='#503D2B', background=GOLD, borderwidth=0, lightcolor=GOLD, darkcolor=GOLD)
        self.style.configure('Imperio.Vertical.TScrollbar', background='#876541', troughcolor=BG, bordercolor=BG, lightcolor='#876541', darkcolor='#876541', arrowcolor=MUTED, arrowsize=11)
        self.style.map('Imperio.Vertical.TScrollbar', background=[('active',GOLD)])
        self.build_header()
        self.build_footer()
        self.build_technical()
        self.build_actions()
        self.build_main()
        self.render(self.snapshot)
        self.poll_id=root.after(75,self.poll)
        if autostart: self.check_id=root.after(30,self.startup)
    def label(self,parent,text=None,size=10,bold=False,color=TEXT,wrap=0,var=None,bg=BG):
        return tk.Label(parent,text=text,textvariable=var,bg=bg,fg=color,font=('Segoe UI',size,'bold' if bold else 'normal'),anchor='w',justify='left',wraplength=wrap,bd=0)
    def build_header(self):
        header=tk.Frame(self.root,bg=BG,padx=30,pady=18); header.pack(fill='x')
        with Image.open(RES/'logo.png') as source:
            image=source.convert('RGBA'); image.thumbnail((58,64),Image.Resampling.LANCZOS)
            self.logo=ImageTk.PhotoImage(image,master=self.root)
        tk.Label(header,image=self.logo,bg=BG,bd=0).pack(side='left',padx=(0,14))
        brand=tk.Frame(header,bg=BG);brand.pack(side='left')
        self.label(brand,'Império AION',18,True).pack(anchor='w')
        self.label(brand,'Instalação e atualizações',10,color=MUTED).pack(anchor='w',pady=(3,0))
        local=tk.Frame(header,bg=BG);local.pack(side='right',anchor='center')
        dot=tk.Canvas(local,width=10,height=14,bg=BG,highlightthickness=0);dot.create_oval(2,4,8,10,fill=GOLD,outline='');dot.pack(side='left',padx=(0,5))
        self.label(local,'Versão beta',9,color=MUTED).pack(side='left')
        tk.Frame(self.root,bg=LINE,height=1).pack(fill='x')
    def build_footer(self):
        footer=tk.Frame(self.root,bg=BG,padx=34,pady=10);footer.pack(side='bottom',fill='x')
        self.label(footer,'Atualizador · v0.4.2',8,color=MUTED).pack(side='left')
        legal=self.label(footer,'Privacidade e termos',8,color=MUTED)
        legal.configure(cursor='hand2',takefocus=True);legal.pack(side='left',padx=(18,0))
        legal.bind('<Button-1>',lambda _:self.show_legal())
        legal.bind('<Return>',lambda _:self.show_legal())
        legal.bind('<space>',lambda _:self.show_legal())
        licenses=self.label(footer,'Licenças',8,color=MUTED)
        licenses.configure(cursor='hand2',takefocus=True);licenses.pack(side='left',padx=(18,0))
        licenses.bind('<Button-1>',lambda _:self.show_licenses())
        licenses.bind('<Return>',lambda _:self.show_licenses())
        licenses.bind('<space>',lambda _:self.show_licenses())
        support=self.label(footer,'Precisa de ajuda?',8,color=MUTED)
        support.configure(cursor='hand2',takefocus=True);support.pack(side='right')
        support.bind('<Button-1>',lambda _:self.support())
        support.bind('<Return>',lambda _:self.support())
        support.bind('<space>',lambda _:self.support())
        support.bind('<FocusIn>',lambda _:support.configure(highlightthickness=1,highlightbackground=GOLD,highlightcolor=GOLD))
        support.bind('<FocusOut>',lambda _:support.configure(highlightthickness=0))
        tk.Frame(self.root,bg=LINE,height=1).pack(side='bottom',fill='x')
    def build_technical(self):
        self.technical=tk.Frame(self.root,bg=BG,padx=34);self.technical.pack(side='bottom',fill='x')
        self.network_check=tk.Checkbutton(self.technical,text='Consultar atualizações automaticamente ao abrir',
            variable=self.automatic_checks,command=self.save_network_choice,bg=BG,fg=TEXT,selectcolor=SURFACE,
            activebackground=BG,activeforeground=GOLD_LIGHT,font=('Segoe UI',9),anchor='w',bd=0)
        self.network_check.pack(fill='x')
        self.label(self.technical,'Consultas e downloads enviam IP e dados da requisição ao site. Privacidade e termos estão abaixo.',8,color=MUTED,wrap=680).pack(fill='x')
        self.details_button=Button(self.technical,'Detalhes da atualização',self.toggle_details,flat=True,icon='chevron')
        self.details_button.pack(fill='x',pady=(2,2))
        self.details_frame=tk.Frame(self.technical,bg=BG)
        self.details_canvas=tk.Canvas(self.details_frame,bg=BG,highlightthickness=0,height=176,bd=0)
        self.detail_scroll=ttk.Scrollbar(self.details_frame,orient='vertical',command=self.details_canvas.yview,style='Imperio.Vertical.TScrollbar')
        self.details_canvas.configure(yscrollcommand=self.detail_scroll.set)
        self.detail_scroll.pack(side='right',fill='y');self.details_canvas.pack(side='left',fill='both',expand=True)
        self.detail_body=tk.Frame(self.details_canvas,bg=BG)
        self.detail_window=self.details_canvas.create_window(0,0,anchor='nw',window=self.detail_body)
        self.detail_body.bind('<Configure>',lambda _:self.details_canvas.configure(scrollregion=self.details_canvas.bbox('all')))
        self.details_canvas.bind('<Configure>',self.resize_details)
        self.details_vars={}
        fields=[('Aplicativo','app'),('Origem deste teste','origin'),('Versão instalada','installed'),('Nova versão','available'),('Antes da instalação','verify'),('Instalação','location'),('Dados do aplicativo','data'),('Permissão do Windows','permission'),('Último resultado','result')]
        self.detail_body.columnconfigure(1,weight=1)
        for row,(caption,key) in enumerate(fields):
            self.label(self.detail_body,caption,9,color=MUTED).grid(row=row,column=0,sticky='nw',padx=(0,18),pady=(0,9))
            var=tk.StringVar(value='—');self.details_vars[key]=var
            label=self.label(self.detail_body,var=var,size=9,wrap=475)
            label.grid(row=row,column=1,sticky='nw',pady=(0,9));label.bind('<MouseWheel>',self.scroll_details)
        self.publisher=self.label(self.detail_body,'O executável de teste ainda não tem assinatura de editor reconhecida pelo Windows. A assinatura do pacote é uma verificação separada.',9,color=MUTED,wrap=640)
        self.publisher.grid(row=len(fields),column=0,columnspan=2,sticky='ew',pady=(4,14))
        for widget in (self.details_canvas,self.detail_body,self.publisher):widget.bind('<MouseWheel>',self.scroll_details)
        tk.Frame(self.root,bg=LINE,height=1).pack(side='bottom',fill='x')
    def build_actions(self):
        self.actions=tk.Frame(self.root,bg=BG,padx=34,pady=20);self.actions.pack(side='bottom',fill='x')
        self.action_note=self.label(self.actions,'O jogo pode permanecer aberto.',8,color=MUTED,wrap=260)
        self.action_note.pack(side='left',fill='x',expand=True,padx=(0,16))
        self.primary=Button(self.actions,'Verificar versão',self.check,width=190,primary=True,icon='download')
        self.primary.pack(side='right')
        self.secondary=Button(self.actions,'Agora não',self.close,width=120)
        self.secondary.pack(side='right',padx=(0,10))
        self.buttons=[self.secondary,self.primary]
    def build_main(self):
        self.main=tk.Frame(self.root,bg=BG,padx=34,pady=24);self.main.pack(fill='both',expand=True)
        headline=tk.Frame(self.main,bg=BG);headline.pack(fill='x')
        self.status_icon=tk.Canvas(headline,width=38,height=38,bg=BG,highlightthickness=0)
        self.status_icon.pack(side='left',anchor='n',padx=(0,13),pady=(2,0))
        heading_text=tk.Frame(headline,bg=BG);heading_text.pack(side='left',fill='both',expand=True)
        self.heading=self.label(heading_text,'',22,True,wrap=625);self.heading.pack(anchor='w',fill='x')
        self.subtitle=self.label(heading_text,'',10,color=MUTED,wrap=625);self.subtitle.pack(anchor='w',fill='x',pady=(6,0))
        self.versions=tk.Frame(self.main,bg=BG,pady=13);self.versions.pack(fill='x',pady=(13,0))
        self.current_caption=self.label(self.versions,'Instalada',9,color=MUTED);self.current_caption.pack(side='left',padx=(0,10))
        self.current=self.label(self.versions,'—',12,True);self.current.pack(side='left')
        self.version_arrow=tk.Canvas(self.versions,width=40,height=24,bg=BG,highlightthickness=0);draw_icon(self.version_arrow,'arrow',10,2,20,MUTED);self.version_arrow.pack(side='left',padx=8)
        self.available_caption=self.label(self.versions,'Nova versão',9,color=MUTED);self.available_caption.pack(side='left',padx=(0,10))
        self.available=self.label(self.versions,'Conferindo…',12,True,color=GOLD_LIGHT);self.available.pack(side='left')
        tk.Frame(self.main,bg=LINE,height=1).pack(fill='x')
        self.state_body=tk.Frame(self.main,bg=BG,height=112);self.state_body.pack(fill='both',expand=True,pady=(19,12));self.state_body.pack_propagate(False)
        self.body_copy=self.label(self.state_body,'',10,color=MUTED,wrap=685);self.body_copy.pack(anchor='w',fill='x')
        self.progress_frame=tk.Frame(self.state_body,bg=BG)
        row=tk.Frame(self.progress_frame,bg=BG);row.pack(fill='x',pady=(0,11))
        self.progress_label=self.label(row,'',10,True);self.progress_label.pack(side='left')
        self.progress_value=self.label(row,'',9,color=MUTED);self.progress_value.pack(side='right')
        self.progress=ProgressBar(self.progress_frame)
        self.progress.pack(fill='x',ipady=0)
        self.steps_frame=tk.Frame(self.progress_frame,bg=BG);self.steps_frame.pack(fill='x',pady=(14,0))
        self.steps=[]
        for index,name in enumerate(('Download','Conferência','Instalação')):
            frame=tk.Frame(self.steps_frame,bg=BG);frame.pack(side='left',expand=True,fill='x')
            icon=tk.Canvas(frame,width=18,height=18,bg=BG,highlightthickness=0);icon.pack(side='left',padx=(0,5))
            label=self.label(frame,name,9,color=MUTED);label.pack(side='left')
            self.steps.append((icon,label,index))
        self.retained=RoundedPanel(self.main,height=52);self.retained.pack(fill='x')
        draw_icon(self.retained,'settings',13,16,18,GREEN)
        self.retained_label=self.label(self.retained,'',9,color=MUTED,wrap=630,bg=SURFACE)
        self.retained_text=self.retained.create_window(42,26,anchor='w',window=self.retained_label)
        self.options=tk.Frame(self.main,bg=BG)
        self.label(self.options,'Como você prefere abrir o aplicativo?',10,True).pack(anchor='w',pady=(0,6))
        self.option_checks=[]
        for key,text in (('start_windows','Iniciar com o Windows'),('start_menu','Criar atalho no menu Iniciar')):
            check=tk.Checkbutton(self.options,text=text,variable=self.install_choices[key],bg=BG,fg=TEXT,
                selectcolor=SURFACE,activebackground=BG,activeforeground=GOLD_LIGHT,disabledforeground=MUTED,
                font=('Segoe UI',10),highlightcolor=GOLD,highlightbackground=BG,anchor='w',padx=0,bd=0)
            check.pack(anchor='w',fill='x');self.option_checks.append(check)
        self.label(self.options,'A HUD abre sem elevação. Ao detectar o jogo, a preparação da captura pode pedir autorização do Windows.',8,color=MUTED,wrap=650).pack(anchor='w',pady=(4,0))
        self.options_save=Button(self.options,'Salvar preferências',self.save_install_choices,width=180)
        self.main.bind('<Configure>',self.resize_main)
    def resize_main(self,_=None):
        width=max(590,self.main.winfo_width()-68)
        self.heading.configure(wraplength=width-51);self.subtitle.configure(wraplength=width-51)
        self.body_copy.configure(wraplength=width);self.retained_label.configure(wraplength=width-58)
        self.retained.itemconfigure(self.retained_text,width=width-58)
    def resize_details(self,event):
        self.details_canvas.itemconfigure(self.detail_window,width=event.width)
        self.publisher.configure(wraplength=max(420,event.width-12))
        for widget in self.detail_body.grid_slaves(column=1):widget.configure(wraplength=max(270,event.width-175))
    def scroll_details(self,event):
        self.details_canvas.yview_scroll(-int(event.delta/120),'units');return 'break'
    def toggle_details(self):
        self.details_open=not self.details_open
        if self.details_open:
            self.details_frame.pack(fill='x',pady=(0,7));self.details_button.icon='chevron-up'
            self.root.update_idletasks();height=min(822,self.root.winfo_screenheight()-80)
            self.root.geometry('%dx%d' % (self.root.winfo_width(),max(620,height)))
        else:
            self.details_frame.pack_forget();self.details_button.icon='chevron'
            self.root.geometry('%dx740' % self.root.winfo_width())
        self.details_button.draw()
    def support(self):webbrowser.open('mailto:support@imperiobdo.com')
    def get_service(self):
        if self.service is None:
            from update_service import UpdateService
            self.service=UpdateService(self.data,self.local_channel,resources_root=RES,on_event=self.events.put)
        return self.service
    def start(self,operation):
        if self.busy or self.closed:return
        self.busy=True;self.primary.set_enabled(False);self.secondary.set_enabled(False)
        for check in self.option_checks:check.configure(state='disabled')
        self.options_save.set_enabled(False)
        def worker():
            failed=False
            try:operation()
            except Exception as error:
                failed=True
                snapshot=self.service.snapshot() if self.service is not None else dict(self.snapshot)
                if snapshot.get('phase')!='offline' or snapshot.get('error')!=str(error):
                    snapshot.update(phase='error',error=str(error),message=str(error))
                self.events.put(dict(type='state',**snapshot))
            finally:self.events.put({'type':'worker_done','failed':failed})
        self.worker=threading.Thread(target=worker,daemon=True);self.worker.start()
    def save_network_choice(self):
        import network_preferences
        try:network_preferences.save(self.data,self.automatic_checks.get())
        except (OSError,ValueError):
            self.automatic_checks.set(network_preferences.load(self.data))
            from tkinter import messagebox
            messagebox.showerror('Preferência não salva','Não foi possível salvar a opção de consulta automática.',parent=self.root)
    def startup(self):
        from network_preferences import start_service
        automatic=self.automatic_checks.get()
        self.check_finished=False;self.launch_requested=False
        def operation():
            try:start_service(self.get_service(),automatic)
            finally:self.check_finished=True
        self.start(operation)
    def check(self):
        self.check_finished=False;self.launch_requested=False
        def operation():
            try:
                service=self.get_service();service.detect();service.check_updates()
            finally:self.check_finished=True
        self.start(operation)
    def start_download(self):
        self.pending_choices={key:var.get() for key,var in self.install_choices.items()}
        def operation():
            service=self.get_service();service.download()
            if service.snapshot().get('phase')=='downloaded':
                service.install();self.register_shortcut(service)
        self.start(operation)
    def install(self):
        self.pending_choices={key:var.get() for key,var in self.install_choices.items()}
        def operation():
            service=self.get_service();service.install();self.register_shortcut(service)
        self.start(operation)
    def launch(self):self.start(lambda:self.get_service().launch(background=True) if self.windows_startup else self.get_service().launch())
    def save_install_choices(self):
        choices={key:var.get() for key,var in self.install_choices.items()}
        def operation():
            self.apply_install_choices(self.get_service(),choices)
        self.start(operation)
    def apply_install_choices(self,service,choices):
        from install_preferences import apply
        info=service.bridge.launcher_info()
        apply(self.data,info['path'],choices,local_channel=self.local_channel)
    def cancel_download(self):
        if self.service is not None:self.service.cancel_download()
    def dispatch(self,name):
        actions={'check':self.check,'download':self.start_download,'install':self.install,'launch':self.launch,'cancel_download':self.cancel_download,'close':self.close}
        if name in actions:actions[name]()
    def render(self,snapshot):
        self.snapshot=dict(snapshot);view=apply_startup_view(present(snapshot),snapshot,launch_mode=self.launch_mode);phase=view['phase'];self.status.set(view['heading'])
        self.heading.configure(text=view['heading']);self.subtitle.configure(text=view['subtitle'])
        self.action_note.configure(text=view['note']);self.retained_label.configure(text=view['retained'])
        self.current.configure(text='v'+str(snapshot['installed_version']) if snapshot.get('installed_version') else 'Primeiro acesso' if phase not in ('idle','checking','detecting') else 'Conferindo…')
        self.available.configure(text='v'+str(snapshot['available_version']) if snapshot.get('available_version') else '—' if phase in ('error','missing','corrupted') else 'Conferindo…')
        for widget in (self.version_arrow,self.available_caption,self.available):widget.pack_forget()
        if phase in ('installed','up_to_date','launched'):
            self.current_caption.configure(text='Versão atual')
        else:
            self.current_caption.configure(text='Instalada')
            self.version_arrow.pack(side='left',padx=8)
            self.available_caption.pack(side='left',padx=(0,10));self.available.pack(side='left')
        self.status_icon.delete('all');tone=view['tone']
        color=GREEN if tone=='success' else RED if tone=='error' else GOLD_LIGHT
        fill=GREEN_BG if tone=='success' else RED_BG if tone=='error' else '#3A2D20'
        rounded(self.status_icon,0,0,38,38,10,fill=fill,outline='')
        draw_icon(self.status_icon,view['symbol'],8,8,22,color)
        enabled=bool(view['action']) and not self.busy
        visible=phase in ('ready','downloaded','blocked_running','installed','up_to_date')
        self.state_body.configure(height=76 if visible else 112)
        self.state_body.pack_configure(expand=not visible)
        if visible:
            self.options.pack(fill='x',before=self.retained,pady=(0,14))
            for check in self.option_checks:check.configure(state='disabled' if self.busy else 'normal')
            if phase in ('installed','up_to_date'):
                self.options_save.place(relx=1,y=0,anchor='ne');self.options_save.set_enabled(not self.busy)
            else:self.options_save.place_forget()
        else:self.options.pack_forget();self.options_save.place_forget()
        # Service capability flags are authoritative whenever the service supplies them.
        capability={'check':'can_check','download':'can_download','install':'can_install','launch':'can_launch'}.get(view['action'])
        if capability in snapshot and not (phase=='blocked_running' and view['action']=='install'):
            enabled=enabled and bool(snapshot[capability])
        self.primary.configure_action(view['primary'],lambda:self.dispatch(view['action']),enabled=enabled,icon=view['symbol'])
        if view['secondary']:
            if not self.secondary.winfo_manager():self.secondary.pack(side='right',padx=(0,10))
            self.secondary.configure_action(view['secondary'],lambda:self.dispatch(view['secondary_action']),enabled=not self.busy or view['secondary_action']=='cancel_download')
        else:self.secondary.pack_forget()
        if view['progress']:
            self.body_copy.pack_forget();self.progress_frame.pack(fill='x',anchor='n')
            self.progress_label.configure(text=view['progress_label'])
            percent=snapshot.get('percent') if phase=='downloading' else None
            self.progress.stop()
            if percent is not None and snapshot.get('total_bytes'):
                percent=max(0,min(100,float(percent)));self.progress.configure_progress(mode='determinate',value=percent)
                received=snapshot.get('received_bytes',0);total=snapshot['total_bytes']
                self.progress_value.configure(text='%d%% · %.1f / %.1f MB' % (percent,received/1000000,total/1000000))
            else:
                self.progress.configure_progress(mode='indeterminate',value=0);self.progress.start(18);self.progress_value.configure(text='Aguarde')
            self.steps_frame.pack(fill='x',pady=(14,0)) if view['steps'] else self.steps_frame.pack_forget()
            active=0 if phase=='downloading' else 1 if phase=='verifying' else 2
            for icon,label,index in self.steps:
                c=GREEN if index<active else GOLD_LIGHT if index==active else MUTED
                label.configure(fg=c);icon.delete('all');draw_icon(icon,'check' if index<active else ('download','shield','arrow')[index],1,1,15,c,1.4)
        else:
            self.progress.stop();self.progress_frame.pack_forget()
            self.body_copy.configure(text=view['body'],fg=RED if tone=='error' else MUTED)
            self.body_copy.pack(anchor='w',fill='x')
        details={
            'app':'Império AION',
            'origin':'Canal local de teste, explicitamente selecionado.' if self.local_channel is not None else 'https://imperiobdo.com/aion2/releases/v2/ · canal HTTPS.',
            'installed':snapshot.get('installed_version') or 'Nenhuma versão autenticada identificada.',
            'available':snapshot.get('available_version') or 'Aguardando conferência.',
            'verify':'Assinatura do pacote, integridade do ZIP e dos arquivos de instalação.',
            'location':snapshot.get('install_directory') or r'Program Files\ImperioAION · pasta separada para cada versão.',
            'data':'Atalhos, posição, tamanho e opacidade da HUD, cache dos personagens e sessões salvas permanecem na pasta de dados da mesma conta do Windows.',
            'permission':'Pode ser solicitada ao instalar e ao abrir o aplicativo para captura.',
            'result':snapshot.get('error') or snapshot.get('message') or 'Aguardando início.',
        }
        if snapshot.get('shortcut_notice'):
            details['result']='Instalação confirmada. O atalho não pôde ser criado: '+snapshot['shortcut_notice']
            self.action_note.configure(text='Instalação concluída. Guarde este instalador para abrir e atualizar.')
        for key,value in details.items():self.details_vars[key].set(str(value))
    def register_shortcut(self,service):
        if service.snapshot().get('phase')!='installed' or self.shortcuts_attempted:return
        self.shortcuts_attempted=True
        try:
            from desktop_shortcut import create_shortcut
            info=service.bridge.launcher_info()
            create_shortcut(info['path'],local_channel=self.local_channel,
                label='Império AION · teste' if self.local_channel is not None else 'Império AION')
            # Tk values are frozen on the UI thread before starting install.
            self.apply_install_choices(service,self.pending_choices)
        except (AttributeError,OSError,ValueError,RuntimeError) as error:
            # A shortcut failure must never misreport a confirmed installation.
            self.events.put(dict(type='notice',**service.snapshot(),shortcut_notice=str(error)))
    def show_licenses(self):
        from tkinter import messagebox
        folder=RES/'licenses'
        if not (folder/'PROJECT-GPL-3.0.txt').is_file():
            messagebox.showerror('Licenças indisponíveis','Baixe novamente o instalador completo.',parent=self.root);return
        os.startfile(str(folder))
    def show_legal(self):
        from tkinter import messagebox
        try:
            legal=json.loads((RES/'legal.json').read_text(encoding='utf8'))
            text='Responsável: '+legal['responsible']+' · '+legal['contact']+'\n\n'
            for key in ('privacy','terms'):
                page=legal[key];text+=page['title']+'\n'+page['intro']+'\n\n'
                for title,body in page['sections']:text+=title+'\n'+body+'\n\n'
            text+='Desinstalação\n'+(RES/'UNINSTALL.txt').read_text(encoding='utf8')
        except (OSError,ValueError,KeyError):
            messagebox.showerror('Documentos indisponíveis','Baixe novamente o pacote completo do instalador.',parent=self.root);return
        dialog=tk.Toplevel(self.root);dialog.title('Império AION · Privacidade e termos')
        dialog.geometry('780x600');dialog.configure(bg=BG);dialog.transient(self.root)
        frame=tk.Frame(dialog,bg=BG,padx=20,pady=20);frame.pack(fill='both',expand=True)
        scroll=ttk.Scrollbar(frame,orient='vertical',style='Imperio.Vertical.TScrollbar');scroll.pack(side='right',fill='y')
        content=tk.Text(frame,bg=BG,fg=TEXT,insertbackground=GOLD,selectbackground=LINE,
            relief='flat',wrap='word',font=('Segoe UI',10),padx=8,pady=8,yscrollcommand=scroll.set)
        content.pack(side='left',fill='both',expand=True);scroll.configure(command=content.yview)
        content.insert('1.0',text);content.configure(state='disabled')
        content.focus_set();dialog.bind('<Escape>',lambda _:dialog.destroy())
    def poll(self):
        latest=None
        shortcut_notice=self.snapshot.get('shortcut_notice')
        try:
            while True:
                event=self.events.get_nowait()
                if event.get('type')=='worker_done':
                    self.busy=False
                    if self.service is not None and not event.get('failed'):latest=self.service.snapshot()
                else:
                    latest=event
                    shortcut_notice=event.get('shortcut_notice',shortcut_notice)
        except queue.Empty:pass
        if latest is not None:
            if latest.get('phase')=='installed' and shortcut_notice:
                latest=dict(latest,shortcut_notice=shortcut_notice)
            self.render(latest)
        action=startup_action(self.snapshot,launch_mode=self.launch_mode,check_finished=self.check_finished,already_requested=self.launch_requested)
        if action=='launch' and not self.busy:
            self.launch_requested=True;self.launch()
        if self.launch_mode and self.snapshot.get('phase')=='launched' and not self.busy:
            self.close();return
        if not self.closed:self.poll_id=self.root.after(75,self.poll)
    def close(self):
        if self.closed:return
        if self.busy:
            # A privileged transaction must finish. Download cancellation is cooperative.
            if self.snapshot.get('can_cancel'):
                self.cancel_download();self.action_note.configure(text='Cancelando o download. Aguarde um instante.')
            else:self.action_note.configure(text='Aguarde a conclusão desta etapa para fechar.')
            return
        self.closed=True
        if self.service is not None:self.service.close()
        for token in (self.poll_id,self.check_id):
            if token:
                try:self.root.after_cancel(token)
                except tk.TclError:pass
        self.progress.stop();self.root.destroy()


def startup_failure(args, error):
    """No traceback window when launched elevated or startup resources fail."""
    from update_core import UpdateError
    message = str(error) if isinstance(error, UpdateError) else 'Não foi possível abrir o atualizador. Baixe novamente o instalador em imperiobdo.com/aion2 e tente novamente.'
    if args.check_ui or args.test_download:
        if args.result:
            try:
                args.result.write_text(json.dumps(dict(status='FAIL',reason=message,admin_requested=False,capture_started=False),ensure_ascii=False,indent=2),encoding='utf8')
            except OSError:
                pass
        return 2
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.MessageBoxW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint]
    user.MessageBoxW.restype=ctypes.c_int
    user.MessageBoxW(None,message,'Império AION · Atualizador',0x30)
    return 2


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--test-download',action='store_true')
    parser.add_argument('--origin',type=Path)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--result',type=Path)
    parser.add_argument('--check-ui',action='store_true')
    parser.add_argument('--launch-mode',action='store_true')
    parser.add_argument('--windows-startup',action='store_true')
    parser.add_argument('--local-channel',type=Path)
    args=parser.parse_args()
    try:non_admin()
    except (ValueError,OSError) as error:return startup_failure(args,error)
    if args.test_download:
        service=None
        try:
            if not args.destination:raise ValueError('O teste exige --destination explícito.')
            from update_service import UpdateService
            service=UpdateService(args.destination,args.origin,resources_root=RES)
            service.check_updates()
            result=service.download()
            snapshot=service.snapshot()
            if snapshot.get('phase')!='downloaded':raise ValueError(snapshot.get('error') or snapshot.get('message') or 'Download não concluído.')
            report=dict(service.receipt(),status='PASS',network='explicit loopback test' if args.origin else 'HTTPS imperiobdo.com',admin_requested=False,capture_started=False)
        except Exception as error:report=dict(status='FAIL',reason=str(error))
        finally:
            if service is not None:service.close()
        if args.result:args.result.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        return 0 if report['status']=='PASS' else 2
    try:
        data=args.destination or (HOME/'ui-check-unused' if args.check_ui else default_data())
        root=tk.Tk()
    except (ValueError,OSError,tk.TclError) as error:return startup_failure(args,error)
    if args.check_ui:
        root.withdraw();ui=Updater(root,args.destination or HOME/'ui-check-unused');root.update_idletasks()
        frozen=bool(getattr(sys,'frozen',False))
        notices=all((RES/'licenses'/p).is_file() for p in ('PROJECT-GPL-3.0.txt','Python-LICENSE.txt','NATIVE_LICENSES.md','CREDITS.txt'))
        passed=not ui.automatic_checks.get() and (not frozen or notices)
        report=dict(status='PASS' if passed else 'FAIL',automatic_update_checks=ui.automatic_checks.get(),bundled_notices=notices,buttons=len(ui.buttons),interface_version='0.4.2',primary_actions=1,details_collapsed=not ui.details_open,raw_json_visible=False,download_started=False,admin_requested=False,capture_started=False,window=[root.winfo_reqwidth(),root.winfo_reqheight()])
        if args.result:args.result.write_text(json.dumps(report),encoding='utf8')
        ui.close();return 0 if passed else 2
    try:Updater(root,data,autostart=True,launch_mode=args.launch_mode or args.windows_startup or Path(sys.executable).name.lower()=='imperioaion-update.exe',local_channel=args.local_channel,windows_startup=args.windows_startup)
    except (ValueError,OSError,tk.TclError) as error:
        root.destroy();return startup_failure(args,error)
    root.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
