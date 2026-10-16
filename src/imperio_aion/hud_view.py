# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Compact native view; presentation only, never computes combat contributions."""
import math
import time
import tkinter as tk
from tkinter import font as tkfont
from pathlib import Path
from class_icons import draw_icon
from presentation import CLASSES
from hud_assets import RasterAssets,ACTION_SIZES
from od_energy import energy_parts
from resource_view import ResourceStrip
from activity_view import ActivityView
from hud_controls import SoftButton, IconButton, LogoIcon, ScopeSwitch, CaptureToggle, CaptureStartButton, OpacitySlider, rounded_rect, surface_raster, background_rgb
from hud_border import border_raster
from capture_readiness import source_ready,preparing,onboarding_progress,initial_readings
from PIL import Image, ImageTk, ImageDraw, ImageFilter

BG = '#241B15'
SURFACE = '#30251D'
TEXT = '#FFF5E8'
SECONDARY = '#D6BEA2'
GOLD = '#D5A65F'


def preparation_card(canvas,x,y,width,height,fill,outline,tag=''):
    """One stroke-safe surface for all three preparation steps."""
    photo=ImageTk.PhotoImage(surface_raster(width,height,background_rgb(canvas),fill,outline,8),master=canvas)
    canvas.create_image(x,y,anchor='nw',image=photo,tags=tag)
    return photo


def page_slice(rows, page, capacity=6):
    count = max(1, math.ceil(len(rows) / capacity))
    page = max(0, min(page, count - 1))
    return rows[page * capacity:(page + 1) * capacity], page, count


class ConfigureLaterButton(SoftButton):
    """The sole preparation-card action; bounded light only while mapped."""
    def __init__(self,parent,command,assets):
        self.assets=assets;self.pulse_timer=None;self.frames={};self.began=time.monotonic()
        self.motion=True
        try:
            import ctypes
            allowed=ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(allowed),0):
                self.motion=bool(allowed.value)
        except (AttributeError,OSError):pass
        super().__init__(parent,'Configurar depois',command,width=100,height=54,primary=True)
        self.bind('<Map>',lambda event:self.animate(),add='+')
        self.bind('<Unmap>',lambda event:self.cancel(),add='+')
        self.bind('<Destroy>',lambda event:self.cancel() if event.widget is self else None,add='+')

    def cancel(self):
        if self.pulse_timer is not None:
            try:self.after_cancel(self.pulse_timer)
            except tk.TclError:pass
            self.pulse_timer=None

    def animate(self):
        self.cancel()
        if not self.winfo_exists() or not self.winfo_ismapped():return
        self.draw()
        if self.motion:self.pulse_timer=self.after(80,self.animate)

    def draw(self):
        self.delete('all');w,h=self.allocated_size()
        if w<2 or h<2:return
        phase=(1-math.cos((time.monotonic()-self.began)*math.tau/2.4))/2 if self.motion else .65
        level=round(phase*12);pad=5 if h>=40 else 2
        key=(w,h,level,self.hovered, self.tk.call('focus')==str(self))
        if key not in self.frames:
            image=Image.new('RGBA',(w,h),background_rgb(self)+(255,))
            light=Image.new('RGBA',(w,h));d=ImageDraw.Draw(light)
            d.rounded_rectangle((4,3,w-5,h-3),radius=9,fill=(246,185,63,40+level*12))
            image.alpha_composite(light.filter(ImageFilter.GaussianBlur(5)))
            surface=surface_raster(w-2*pad,h-2*pad,background_rgb(self),
                '#80602C' if self.hovered else '#684A25','#FFE0A0' if key[-1] else '#D9AD61',8)
            image.alpha_composite(surface.convert('RGBA'),(pad,pad))
            self.frames[key]=ImageTk.PhotoImage(image,master=self)
            if len(self.frames)>60:self.frames.pop(next(iter(self.frames)))
        self.photo=self.frames[key];self.create_image(0,0,anchor='nw',image=self.photo)
        self.badge=self.assets.get('capture_step_later',min(31,max(12,h-14)))
        self.create_image(23,h/2,image=self.badge)
        self.create_text(46,h/2 if h<40 else h/2-7,text=self.text,anchor='w',fill='#FFE8B8',font=('Segoe UI',8,'bold'))
        if h>=40:self.create_text(46,h/2+9,text='Comece usar agora o dps meter',anchor='w',fill='#F0D8AE',font=('Segoe UI',7))


class CompactHUD:
    def __init__(self, app, window):
        self.app = app; self.window = window; self.rows = []; self.page = 0; self.fight_key = None; self.capacity=5
        self.tooltip = None; self.tip_timer = None; self.tip_row = None
        self.status_snapshot={};self.status_fight=None;self.interactive=True
        self.assets=RasterAssets(window)
        self.border_raster=None;self.border_size=None;self.border_photos={}
        self.border=tk.Canvas(window,bg=BG,highlightthickness=0)
        self.border.place(x=0,y=0,relwidth=1,relheight=1)
        self.border.bind('<Configure>',self.draw_border)
        self.area=area = tk.Frame(window, bg=BG, padx=18, pady=7)
        area.pack(fill='both', expand=True,padx=2,pady=2)
        header = tk.Frame(area, bg=BG); header.pack(fill='x',pady=(0,4))
        self.avatar=self.assets.get('mascot',44)
        mascot=self.mascot=LogoIcon(header,self.assets,bg=BG)
        if self.avatar:mascot.pack(side='left',padx=(0,8))
        content=tk.Frame(header,bg=BG);content.pack(side='left',fill='x',expand=True)
        title=tk.Frame(content,bg=BG);title.pack(fill='x')
        self.target = self.label(title,'Aguardando combate',font=('Segoe UI',9,'bold'))
        self.target.configure(width=1)
        self.target.bind('<Configure>',lambda e:self.target.configure(wraplength=max(80,e.width)))
        scope=tk.Frame(content,bg=BG);scope.pack(fill='x')
        actions=self.actions=tk.Frame(title,bg=BG);actions.pack(side='right',padx=(4,0))
        self.target.pack(side='left',fill='x',expand=True)
        self.return_button=SoftButton(scope,'Bloquear HUD',app.finish_hud_adjustment,width=104,height=22)
        self.reload_button=IconButton(actions,'Reiniciar combate',app.reset_combat,self.assets,'action_reload',ACTION_SIZES['action_reload'])
        self.reload_button.pack(side='left',padx=(0,2))
        self.panel_button=IconButton(actions,'Abrir painel',self.open_panel,self.assets,'action_panel',ACTION_SIZES['action_panel'])
        self.panel_button.pack(side='left',padx=(0,2))
        self.menu_button = IconButton(actions,'?',self.menu,self.assets,'action_help',ACTION_SIZES['action_help'])
        self.menu_button.pack(side='left',padx=(0,2))
        self.hide_button=IconButton(actions,'Sair do monitor',app.close,self.assets,'action_close',ACTION_SIZES['action_close'])
        self.hide_button.pack(side='left')
        self.scope=ScopeSwitch(scope,app.hud_scope,app.change_scope,self.assets);self.scope.pack(side='left')
        self.duty_button=IconButton(scope,'Diárias e Semanais',app.toggle_duty_view,self.assets,'filter_duty',21,width=26)
        self.duty_button.pack(side='left',padx=(2,0))
        self.bind_hint(self.duty_button,'Diárias e Semanais · diário e semanal')
        self.pet_button=IconButton(scope,'Mascotes',app.toggle_pet_view,self.assets,'filter_pets',20,width=26)
        self.pet_button.pack(side='left',padx=(2,0))
        self.bind_hint(self.pet_button,'Mascotes · nível e progresso')
        self.bind_hint(self.reload_button,'Reiniciar combate · limpa o dano e o histórico de lutas.\nA captura, os recursos e os mascotes continuam ativos.')
        self.scope.bind('<Motion>',self.scope_hint,add='+')
        self.scope.bind('<FocusIn>',lambda event:self.control_hint(self.scope,'Dano em Boss' if app.hud_scope.get()=='bosses' else 'Dano em Mobs'),add='+')
        self.scope.bind('<Leave>',self.clear_tip,add='+');self.scope.bind('<FocusOut>',self.clear_tip,add='+')
        self.bind_hint(self.panel_button,lambda:self.app.shortcut('panel')+' · Abrir Painel de Combate')
        self.bind_hint(self.hide_button,'Sair do monitor · encerra a captura')
        self.source = self.label(scope, 'PRONTO', font=('Segoe UI', 8, 'bold'), color=SECONDARY)
        self.source.configure(width=1)
        self.menu_button.bind('<Enter>',self.help_hint,add='+')
        self.menu_button.bind('<FocusIn>',self.help_hint,add='+')
        self.menu_button.bind('<Leave>',self.clear_tip,add='+')
        self.menu_button.bind('<FocusOut>',self.clear_tip,add='+')
        self.source.pack(side='left',fill='x',expand=True,padx=(4,0))
        for widget in (header,self.source,self.target):
            widget.bind('<ButtonPress-1>', app.begin_drag)
            widget.bind('<B1-Motion>', app.drag_hud)
            widget.bind('<ButtonRelease-1>', app.end_drag)
        self.bind_logo(mascot)
        self.phase = self.label(scope, '', font=('Segoe UI',8), color=SECONDARY)
        self.table=MemberCanvas(area,self)
        self.pet_table=PetCanvas(area,self)
        self.activity_view=ActivityView(area,self)
        self.table.pack(fill='both',expand=True)
        self.table.bind('<Motion>',self.hover);self.table.bind('<Leave>',self.clear_tip)
        self.table.bind('<Button-1>',self.clear_tip)
        self.empty = self.label(area, 'A captura começa automaticamente quando o jogo for detectado.', color=SECONDARY)
        self.empty.pack(fill='x', pady=(6,4))
        footer = tk.Frame(area, bg=BG)
        # Reserve the persistent controls before allocating the flexible table.
        footer.pack(before=self.table,side='bottom',fill='x', pady=(4,0))
        controls=tk.Frame(footer,bg=BG);controls.pack(fill='x')
        self.capture = CaptureToggle(controls, self.toggle_capture, self.assets)
        # Capture can only be started on the welcome screen/panel, not stopped
        # by an accidental click while collecting a continuous login session.
        self.od_group=tk.Frame(controls,bg=BG)
        self.od_group.pack(side='left',padx=(0,14))
        self.od_icon=tk.Label(self.od_group,image=self.assets.get('od_energy',22),bg=BG,borderwidth=0)
        self.od_icon.pack(side='left',padx=(0,3))
        self.od_labels=[]
        for color in (TEXT,'#54C8F2',TEXT):
            label=self.label(self.od_group,'',font=('Segoe UI',9,'bold'),color=color)
            label.configure(padx=0,pady=0)
            label.pack(side='left');self.od_labels.append(label)
        for widget in (self.od_group,self.od_icon,*self.od_labels):
            self.bind_hint(widget,self.od_hint)
        self.update_od({})
        self.resource_strip=ResourceStrip(controls,self)
        self.resource_strip.pack(side='left')
        status=tk.Frame(footer,bg=BG);status.pack(fill='x',pady=(2,0))
        status.columnconfigure(0,weight=1)
        self.opacity_slider=OpacitySlider(status,app.opacity,lambda:app.apply_opacity(save=False),app.apply_opacity)
        self.opacity_slider.configure(width=106)
        self.opacity_slider.grid(row=0,column=1,sticky='e',padx=(5,0))
        self.note = self.label(status, app.hud_status_text(), font=('Segoe UI',7), color=SECONDARY)
        self.note.configure(width=1)
        self.note.grid(row=0,column=0,sticky='ew')
        self.member_count=self.label(scope,'',font=('Segoe UI',8),color=SECONDARY)
        self.member_count.pack(side='right')
        self.bind_hint(self.member_count,lambda:self.app.group_summary.get())
        self.pager = tk.Frame(scope, bg=BG)
        self.prev = SoftButton(self.pager, '‹', lambda:self.turn(-1),width=20,height=20); self.prev.pack(side='left')
        self.page_label = self.label(self.pager, '1/1', font=('Segoe UI',8)); self.page_label.pack(side='left', padx=4)
        self.next = SoftButton(self.pager, '›', lambda:self.turn(1),width=20,height=20); self.next.pack(side='left')
        self.menu_open = False;self.popup=None;self.menu_timer=None;self.feedback_until=0;self.feedback_text=''
        self.resize_edges=[]
        self.build_edges()
        self.welcome_dismissed=False;self.welcome_shown=False;self.welcome_size=None;self.covered_focus={}
        self.welcome_original=Image.open(Path(__file__).resolve().parent/'assets/kit/mascote-pensativa.png').convert('RGBA')
        self.welcome=tk.Canvas(window,bg=BG,highlightthickness=0)
        self.welcome.bind('<Configure>',self.draw_welcome)
        self.welcome_dps=ConfigureLaterButton(self.welcome,app.show_combat_hud,self.assets)
        self.bind_hint(self.welcome_dps,'Configurar depois · abre o DPS e mantém a captura automática')
        self.welcome_panel=IconButton(self.welcome,'Abrir painel',self.open_panel,self.assets,'action_panel',ACTION_SIZES['action_panel'])
        self.welcome_help=IconButton(self.welcome,'?',self.menu,self.assets,'action_help',ACTION_SIZES['action_help'])
        self.welcome_close=IconButton(self.welcome,'Sair do monitor',app.close,self.assets,'action_close',ACTION_SIZES['action_close'])
        for button in (self.welcome_panel,self.welcome_help,self.welcome_close):
            button.background_provider=lambda width,height,button=button:self.welcome_background.crop(
                (button.winfo_x(),button.winfo_y(),button.winfo_x()+width,button.winfo_y()+height)) if hasattr(self,'welcome_background') else None
        self.welcome_lock=SoftButton(self.welcome,'Bloquear HUD',app.finish_hud_adjustment,width=104,height=22)
        self.welcome_logo=LogoIcon(self.welcome,self.assets,bg=BG)
        self.bind_logo(self.welcome_logo)
        self.folded=tk.Frame(window,bg=BG)
        self.folded_logo=LogoIcon(self.folded,self.assets,bg=BG)
        self.folded_logo.pack(padx=8,pady=8)
        self.bind_logo(self.folded_logo)
        for widget,text in ((self.welcome_panel,lambda:self.app.shortcut('panel')+' · Abrir Painel de Combate'),(self.welcome_help,'Ajuda e opções'),(self.welcome_close,'Sair do monitor · encerra a captura')):
            self.bind_hint(widget,text)
        self.sync_welcome({})
        from logo_hint import LogoHint
        self.logo_hint=LogoHint(self)

    def bind_logo(self,widget):
        widget.bind('<ButtonPress-1>',self.app.logo_press)
        widget.bind('<B1-Motion>',self.app.logo_motion)
        widget.bind('<ButtonRelease-1>',self.app.logo_release)

    def set_collapsed(self,collapsed):
        self.logo_hint.dismiss()
        if collapsed:
            self.area.pack_forget()
            for edge in self.resize_edges:edge.place_forget()
            self.folded.place(x=2,y=2,width=60,height=60)
            self.folded.tk.call('raise',str(self.folded))
        else:
            self.folded.place_forget()
            self.area.pack(fill='both',expand=True,padx=2,pady=2)
            for edge in self.resize_edges:edge.place(**edge.hud_place)
        self.sync_welcome(self.status_snapshot)

    def sync_welcome(self,snapshot):
        login_ready=self.app.update_login_access(snapshot)
        self.welcome_dismissed=self.app.onboarding_complete or (self.app.onboarding_skipped and self.app.hud_page.get()=='combat')
        show=not self.welcome_dismissed and not self.app.hud_collapsed
        if show!=self.welcome_shown:
            self.welcome_shown=show
            if show:
                self.welcome_size=None
                pending=list(self.area.winfo_children())
                while pending:
                    widget=pending.pop();pending.extend(widget.winfo_children())
                    try:
                        self.covered_focus[widget]=widget.cget('takefocus');widget.configure(takefocus=False)
                    except tk.TclError:pass
                self.welcome.place(x=18,y=12,relwidth=1,width=-36,relheight=1,height=-24)
                self.welcome.tk.call('raise',str(self.welcome))
            else:
                self.welcome.place_forget()
                for widget,focus in self.covered_focus.items():
                    if widget.winfo_exists():widget.configure(takefocus=focus)
                self.covered_focus.clear()
        running=bool(snapshot.get('running'))
        state=('error' if snapshot.get('error') else 'preparing' if preparing(snapshot) else 'waiting' if running else 'idle')
        progress=(onboarding_progress(snapshot,login_ready),tuple(initial_readings(snapshot).values()))
        if getattr(self,'welcome_state',None)!=state or getattr(self,'welcome_progress',None)!=progress:
            self.welcome_state=state;self.welcome_progress=progress;self.welcome_size=None
        if show and self.welcome_size is None:self.draw_welcome()

    def draw_welcome(self,event=None):
        w,h=max(1,self.welcome.winfo_width()),max(1,self.welcome.winfo_height())
        if (w,h)==self.welcome_size:return
        self.welcome_size=(w,h);self.welcome.delete('all')
        tiny=h<220;compact=h<280
        background=Image.new('RGBA',(w,h),BG)
        if not compact:
            artwork=self.welcome_original.copy();artwork.thumbnail((65,78),Image.Resampling.LANCZOS)
            background.alpha_composite(artwork,(max(0,w-artwork.width-1),59))
        self.welcome_background=background;self.welcome_photo=ImageTk.PhotoImage(background,master=self.window)
        self.welcome.create_image(0,0,anchor='nw',image=self.welcome_photo)
        self.welcome_logo.place(x=0,y=0)
        if self.app.hud_adjustment.get() and not tiny:self.welcome_lock.place(x=max(0,w-104),y=29)
        else:self.welcome_lock.place_forget()
        for i,button in enumerate((self.welcome_panel,self.welcome_help,self.welcome_close)):
            button.place(x=w-74+24*i,y=3);button.draw()
        state=getattr(self,'welcome_state','idle');running=state in ('waiting','preparing');ready=self.app.login_ready
        done_steps,active_step=onboarding_progress(self.status_snapshot,ready)
        readings=initial_readings(self.status_snapshot)
        title='Confira a captura' if state=='error' else 'Recebendo seus dados' if ready else 'Prepare a captura'
        self.welcome.create_text(62 if tiny else 1,27 if tiny else 54,text=title,anchor='nw',
            fill='#F4D39A',font=('Segoe UI',9 if tiny else 11,'bold'),tags='welcome_title')
        if not compact:
            message=('Abra o diagnóstico pela lupa\ne tente iniciar novamente.' if state=='error' else
                'Relog confirmado. Aguarde a\nleitura completa dos seus dados.' if ready else
                'Entre na seleção de personagem.\nA captura começa automaticamente.')
            self.welcome.create_text(1,80,text=message,anchor='nw',fill='#E1CBAE',
                font=('Segoe UI',8),width=max(100,w-71),tags='welcome_intro')
        top=46 if tiny else 83 if compact else 117
        gap=4 if compact else 6;action_h=25 if tiny else 30 if compact else 54
        footer=25 if tiny else 42
        row_h=min(39,max(13,int((h-footer-action_h-8-top-2*gap)/3)))
        self.welcome_card_boxes=[];self.welcome_step_keys=[];self.welcome_step_images=[];self.welcome_card_photos=[]
        rows=(('Seleção de personagem','Seleção de personagem reconhecida.' if done_steps[0] else 'Aguardando a seleção de personagem.',done_steps[0]),
              ('Captura automática','Pronta para receber os dados.' if done_steps[1] else 'Iniciando automaticamente…' if done_steps[0] else 'Início automático na seleção.',done_steps[1]),
              ('Relogue o personagem','Entrada no personagem confirmada.' if ready else 'Entre no personagem com a captura pronta.' if done_steps[1] else 'Aguarde a captura ficar pronta.',ready))
        for i,(label,note,done) in enumerate(rows):
            y=top+i*(row_h+gap);self.welcome_card_boxes.append((1,y,w-1,y+row_h))
            key='capture_step_check' if done else 'capture_step_'+str(i+1)
            self.welcome_step_keys.append(key)
            fill='#24432F' if done else '#493421' if i==active_step else '#30251D'
            line='#5F8E57' if done else '#B38A4B' if i==active_step else '#62472E'
            self.welcome_card_photos.append(preparation_card(self.welcome,1,y,w-2,row_h,fill,line,'welcome_card_'+str(i+1)))
            badge=self.assets.get(key,min(28,max(12,row_h-4)));self.welcome_step_images.append(badge)
            self.welcome.create_image(23,y+row_h/2,image=badge,tags='welcome_badge')
            self.welcome.create_text(44,y+row_h/2 if row_h<36 else y+12,text=label,anchor='w',
                fill='#D5EDD0' if done else '#FFE0A8',font=('Segoe UI',8,'bold'),tags='welcome_step_title')
            if row_h>=36:self.welcome.create_text(44,y+27,text=note,anchor='w',fill=SECONDARY,font=('Segoe UI',7),tags='welcome_step_note')
        end=top+3*row_h+2*gap
        self.welcome_dps.place(x=0,y=end+5,width=w,height=action_h)
        self.welcome_action_bottom=end+5+action_h
        self.welcome.create_text(1,h-8,text=self.app.hud_status_text(),anchor='w',fill=SECONDARY,font=('Segoe UI',7 if tiny else 8),tags='welcome_footer')
        if not tiny:
            count=sum(readings.values())
            status=('Falha na captura · abra a lupa' if state=='error' else
                f'Recebendo dados · {count} de 4 leituras confirmadas' if ready else
                'Captura pronta · entre no personagem' if done_steps[1] else
                'Seleção reconhecida · iniciando captura' if done_steps[0] else
                'Aguardando a seleção de personagem')
            self.welcome.create_text(1,h-27,text=status,anchor='w',fill=SECONDARY,font=('Segoe UI',8),tags='welcome_status')


    def draw_border(self,event):
        self.ensure_border(event.width,event.height)

    def ensure_border(self,width,height):
        size=(max(1,int(width)),max(1,int(height)))
        if self.border_size==size:return
        self.border_size=size;self.border_raster=border_raster(*size)
        self.border_photos['frame']=ImageTk.PhotoImage(self.border_raster,master=self.window)
        self.border.delete('all');self.border.create_image(0,0,anchor='nw',image=self.border_photos['frame'])
        for canvas in self.resize_edges:self.paint_edge(canvas)

    def paint_edge(self,canvas):
        if self.border_raster is None:return
        x,y=canvas.winfo_x(),canvas.winfo_y()
        width,height=canvas.winfo_width(),canvas.winfo_height()
        raster=self.border_raster.crop((x,y,x+width,y+height))
        photo=self.border_photos[str(canvas)]=ImageTk.PhotoImage(raster,master=self.window)
        canvas.delete('all');canvas.create_image(0,0,anchor='nw',image=photo)

    def build_edges(self):
        placements={
          'n':dict(relx=0,rely=0,relwidth=1,height=5),
          's':dict(relx=0,rely=1,relwidth=1,height=5,anchor='sw'),
          'w':dict(relx=0,rely=0,width=5,relheight=1),
          'e':dict(relx=1,rely=0,width=5,relheight=1,anchor='ne'),
          'nw':dict(relx=0,rely=0,width=18,height=18),
          'ne':dict(relx=1,rely=0,width=18,height=18,anchor='ne'),
          'sw':dict(relx=0,rely=1,width=18,height=18,anchor='sw'),
          'se':dict(relx=1,rely=1,width=18,height=18,anchor='se')}
        for edge,place in placements.items():
            widget=tk.Canvas(self.window,bg=BG,highlightthickness=0,cursor='size_'+('we' if edge in ('w','e') else 'ns' if edge in ('n','s') else 'nw_se' if edge in ('nw','se') else 'ne_sw'))
            widget.place(**place)
            widget.hud_place=place
            widget.bind('<Configure>',lambda event,edge=edge:self.draw_edge(event,edge))
            widget.bind('<ButtonPress-1>',lambda event,edge=edge:self.app.begin_resize(event,edge))
            widget.bind('<B1-Motion>',self.app.resize_hud)
            widget.bind('<ButtonRelease-1>',self.app.end_resize)
            self.resize_edges.append(widget)

    def draw_edge(self,event,edge):
        self.ensure_border(self.window.winfo_width(),self.window.winfo_height())
        self.paint_edge(event.widget)

    def label(self, parent, text, font=('Segoe UI',9), color=TEXT):
        return tk.Label(parent, text=text, font=font, bg=BG, fg=color, anchor='w', borderwidth=0)

    def button(self, parent, text, command, primary=False):
        return SoftButton(parent,text,command,primary=primary)

    def toggle_capture(self):
        if self.app.controller.running:return
        self.app.start()
        # Do not optimistically display ON if startup failed.
        self.app.snapshot=self.app.controller.snapshot()
        self.status_snapshot=self.app.snapshot
        self.capture.configure(active=source_ready(self.app.snapshot));self.refresh_status()
        self.sync_welcome(self.app.snapshot)

    def menu(self):
        if self.menu_open:self.close_menu();return
        self.clear_tip();self.menu_open=True
        self.menu_button.configure(selected=True)
        popup=self.popup=tk.Toplevel(self.window);popup.withdraw();popup.overrideredirect(True)
        popup.transient(self.window);popup.configure(bg=SURFACE);popup.attributes('-topmost',True)
        surface=tk.Frame(popup,bg=SURFACE,padx=9,pady=9);surface.pack(fill='both',expand=True)
        tk.Label(surface,text='Ajuda e opções',bg=SURFACE,fg=TEXT,font=('Segoe UI',10,'bold'),anchor='w').pack(fill='x',pady=(0,5))
        from shortcuts import ACTIONS
        tk.Label(surface,text='\n'.join(self.app.shortcut(action)+' · '+label for action,label in ACTIONS.items()),
                 justify='left',bg=SURFACE,fg=SECONDARY,font=('Segoe UI',8),anchor='w').pack(fill='x',pady=(0,7))
        def run(command):
            self.close_menu();command()
        def item(text,command,enabled=True):
            button=SoftButton(surface,text,lambda:run(command),width=280)
            button.pack(fill='x',pady=2)
            if not enabled:button.configure(state='disabled')
            return button
        item('Atalhos',self.app.show_shortcuts)
        item('Salvar sessão',self.app.export,self.app.export_button['state']!='disabled')
        item('Encerrar luta',self.app.end,self.app.end_button['state']!='disabled')
        item('Reiniciar combate',self.app.reset_combat)
        item('Recarregar dados',self.app.reload_login_cache)
        tk.Frame(surface,bg='#6A4F36',height=1).pack(fill='x',pady=4)
        item('Restaurar tamanho',self.app.reset_hud_size)
        item('Reposicionar HUD',self.app.reset_hud_position)
        item('Sair do monitor',self.app.close)
        popup.bind('<Escape>',lambda event:self.close_menu())
        popup.bind('<FocusOut>',self.defer_menu_dismiss)
        popup.update_idletasks();w=popup.winfo_reqwidth();h=popup.winfo_reqheight()
        x=self.window.winfo_rootx()+self.window.winfo_width()-w-10
        anchor=self.welcome_help if self.welcome_shown else self.menu_button
        y=anchor.winfo_rooty()+anchor.winfo_height()+5
        v=self.app.viewport
        if v and not v.minimized:
            x=max(v.x,min(x,v.x+v.width-w));y=max(v.y,min(y,v.y+v.height-h))
        else:
            x=max(0,min(x,self.window.winfo_screenwidth()-w));y=max(0,min(y,self.window.winfo_screenheight()-h))
        popup.geometry(f'{w}x{h}+{x}+{y}');popup.deiconify();popup.lift();popup.focus_force()
        if hasattr(self.app.game,'rounded_overlay'):self.app.game.rounded_overlay(popup.winfo_id(),w,h,12)
        self.app.follow_game(schedule=False)

    def dismiss_unfocused_menu(self):
        self.menu_timer=None
        if self.popup and not self.app.game.own_foreground():self.close_menu()

    def defer_menu_dismiss(self,event=None):
        if not self.menu_open:return
        if self.menu_timer:self.window.after_cancel(self.menu_timer)
        self.menu_timer=self.window.after(120,self.dismiss_unfocused_menu)

    def close_menu(self):
        self.menu_open=False
        if self.menu_timer:self.window.after_cancel(self.menu_timer);self.menu_timer=None
        if self.popup:self.popup.destroy();self.popup=None
        self.menu_button.configure(selected=False)

    def turn(self, delta):
        self.table.scroll_rows(delta*self.capacity)

    def feedback(self,text):
        import time
        self.feedback_until=time.monotonic()+5;self.feedback_text=text

    def fit_height(self, height):
        # Keep the established combat capacity when switching activity pages.
        # Floor before adding a member, including intermediate drag heights.
        capacity=max(1,min(5,int((height-156)/self.table.row_height)))
        if capacity!=self.capacity:
            first=self.page*self.capacity
            self.capacity=capacity;self.page=first//capacity;self.render()

    def render(self):
        pets=self.app.hud_page.get()=='pets'
        duty=self.app.hud_page.get()=='duty'
        self.sync_welcome(self.status_snapshot)
        self.pet_button.configure(selected=pets)
        self.duty_button.configure(selected=duty)
        self.scope.active=not (pets or duty);self.scope.draw()
        if duty:
            self.table.pack_forget();self.pet_table.pack_forget();self.empty.pack_forget();self.pager.pack_forget()
            if not self.activity_view.winfo_manager():self.activity_view.pack(fill='both',expand=True)
            self.activity_view.set_state(self.status_snapshot)
            self.target.configure(text='Diárias e Semanais')
            self.member_count.configure(text='7 atividades')
            if self.app.hud_adjustment.get():self.member_count.pack_forget()
            else:self.member_count.pack(side='right')
            return
        self.activity_view.pack_forget()
        if pets:
            self.table.pack_forget();self.empty.pack_forget();self.pager.pack_forget()
            if not self.pet_table.winfo_manager():self.pet_table.pack(fill='both',expand=True)
            self.pet_table.set_state(self.status_snapshot.get('pet_collection',{}))
            cached_identity=self.status_snapshot.get('character_cache',{}).get('selected') or {}
            self.target.configure(text='Mascotes · '+cached_identity['name'] if self.status_snapshot.get('pet_collection',{}).get('cache_display') and cached_identity.get('name') else 'Mascotes')
            if self.app.hud_adjustment.get():self.member_count.pack_forget()
            else:self.member_count.pack(side='right')
            self.refresh_pet_summary()
            return
        self.pet_table.pack_forget()
        if not self.table.winfo_manager():self.table.pack(fill='both',expand=True)
        self.target.configure(text=self.app.hud_target.get() if self.status_fight else 'Aguardando boss identificado' if self.app.hud_scope.get()=='bosses' else 'Aguardando combate')
        self.table.set_rows(self.rows)
        pages=1
        if self.rows:
            self.empty.pack_forget()
        elif not self.empty.winfo_manager():
            self.empty.pack(after=self.table, fill='x', pady=(6,4))
        if pages>1:
            self.member_count.pack_forget();self.pager.pack(side='right')
        else:
            self.pager.pack_forget()
            if not self.app.hud_adjustment.get() and not self.member_count.winfo_manager():self.member_count.pack(side='right')
        self.member_count.configure(text=f'{len(self.rows)} membros' if self.rows else '')
        self.prev.configure(state='normal' if self.page else 'disabled')
        self.next.configure(state='normal' if self.page<pages-1 else 'disabled')
        self.page_label.configure(text=f'{self.page+1}/{pages}')
        self.window.update_idletasks()
        self.sync_welcome(self.status_snapshot)
        if self.app.hud_manual_size is None and not self.app.hud_collapsed:
            self.app.hud_size=(390,min(370,max(220,self.window.winfo_reqheight())))

    def refresh_pet_summary(self):
        state=self.pet_table.state;entries=self.pet_table.visible_entries()
        pending=not entries or any(not pet_progress(entry,state)[2] for entry in entries)
        footer=('Leitura incompleta · relogue' if state.get('relog_required') and self.status_snapshot.get('running') else
                'Histórico local · não confirmado' if state.get('cache_display') else
                'Saldo inicial ainda não recebido' if state.get('synchronization_state')=='metadata_partial' else
                'Nenhuma coleta ainda' if not entries else
                f"Mascotes: {len(entries)} de {len(state.get('entries',[]))}" if not pending else
                'Leitura parcial' if state.get('status')=='partial' or state.get('session_count_complete') is False else 'Saldo pendente')
        self.member_count.configure(text=footer)

    def update_od(self,snapshot):
        parts=energy_parts(snapshot.get('od_energy'))
        for label,text in zip(self.od_labels,parts):label.configure(text=text)

    def od_hint(self):
        from resource_gains import display_time
        value=self.status_snapshot.get('od_energy',{})
        text='Energia Od · leitura experimental · limite 840 de referência.'
        if value.get('cached'):
            text+='\nÚltimo saldo salvo em cache, ainda não confirmado nesta sessão.'
            if value.get('confirmed_at') or value.get('saved_at'):
                text+='\nÚltima leitura: '+display_time(value.get('confirmed_at') or value['saved_at'])
        if value.get('reading_error'):text+='\nLeitura incompleta; último saldo preservado.'
        if value.get('cached') or not value.get('available'):
            text+='\nSe estiver dessincronizado, relogue com a captura ativa.'
        return text

    def update(self, snapshot, fight, rows):
        key = fight['key'] if fight else None
        new_fight=key != self.fight_key
        if new_fight:
            self.page = 0; self.fight_key = key;self.table.offset=0
        changed = self.rows != rows
        self.rows = [dict(r) for r in rows]
        self.status_snapshot=snapshot;self.status_fight=fight
        self.update_od(snapshot)
        self.resource_strip.set_state(snapshot.get('resource_gains'))
        self.target.configure(text=self.app.hud_target.get() if fight else 'Aguardando boss identificado' if self.app.hud_scope.get()=='bosses' else 'Aguardando combate')
        self.phase.configure(text='Encerrado' if fight and fight['end_us'] is not None else
                             'Em combate' if fight else '')
        self.capture.configure(active=source_ready(snapshot))
        self.sync_welcome(snapshot)
        self.interaction_hint(self.app.hud_interactive)
        # The combat HUD stays available without a relog. Its empty state must
        # still show the readiness check when the preparation cover closes.
        startup_prompt=bool(snapshot.get('mode')=='live' and snapshot.get('running') and not snapshot.get('error')
            and 'capture_source_ready' in snapshot.get('metadata',{})
            and not snapshot.get('identified') and not snapshot.get('pending_records') and not fight)
        self.empty.readiness_photo=self.assets.get('capture_step_check' if source_ready(snapshot) else 'capture_step_2',16) if startup_prompt else None
        self.empty.configure(text=('Capturando combate\nAguardando sua identificação atual.' if source_ready(snapshot) else
            'Preparando captura\nAguarde a conexão do jogo.') if startup_prompt else combat_waiting_text(snapshot,self.app.hud_scope.get()),
            image=self.empty.readiness_photo or '',compound='top')
        if changed or new_fight or not self.table.get_children() or self.app.hud_page.get() in ('pets','duty'):self.render()

    def clear_tip(self, event=None):
        if self.tip_timer:self.window.after_cancel(self.tip_timer); self.tip_timer=None
        if self.tooltip:self.tooltip.destroy(); self.tooltip=None
        self.tip_row=None

    def open_panel(self):
        self.close_menu();self.clear_tip();self.app.show_panel()

    def bind_hint(self,widget,text):
        for event in ('<Enter>','<FocusIn>'):widget.bind(event,lambda e:self.control_hint(widget,text() if callable(text) else text),add='+')
        for event in ('<Leave>','<FocusOut>'):widget.bind(event,self.clear_tip,add='+')

    def scope_hint(self,event):
        mode=self.scope.mode_at(event.x)
        if self.tip_row=='filter_'+mode:return
        self.control_hint(self.scope,'Dano em Mobs' if mode=='all' else 'Dano em Boss')
        self.tip_row='filter_'+mode

    def control_hint(self,widget,text):
        self.clear_tip()
        if self.interactive and not self.menu_open:self.tip_timer=self.window.after(450,lambda:self.show_control_hint(widget,text))

    def show_control_hint(self,widget,text):
        self.tip_timer=None
        if not self.interactive or self.menu_open:return
        self.show_inline_hint(text,widget.winfo_rootx(),widget.winfo_rooty()+widget.winfo_height()+4)

    def show_inline_hint(self,text,x,y):
        """A child overlay stays above the HUD without creating another window.

        Keep the card away from the pointer, so rendering a tooltip cannot itself
        trigger Leave/Enter events and repeatedly destroy/recreate the same hint.
        """
        if self.tooltip:self.tooltip.destroy()
        tip=self.tooltip=tk.Canvas(self.window,bg=BG,highlightthickness=0,takefocus=False)
        label=tk.Label(tip,text=text,justify='left',bg=SURFACE,fg=TEXT,font=('Segoe UI',8),borderwidth=0,
                       wraplength=max(80,self.window.winfo_width()-58))
        label.update_idletasks();w=label.winfo_reqwidth()+16;h=label.winfo_reqheight()+10
        tip.configure(width=w,height=h)
        tip.photo=ImageTk.PhotoImage(surface_raster(w,h,background_rgb(tip),SURFACE,None,7),master=self.window)
        tip.create_image(0,0,anchor='nw',image=tip.photo);label.place(x=8,y=5)
        left,top=self.window.winfo_rootx(),self.window.winfo_rooty()
        maxw,maxh=self.window.winfo_width(),self.window.winfo_height()
        x=max(6,min(x-left,maxw-w-6));y=max(6,min(y-top,maxh-h-6))
        px,py=self.window.winfo_pointerxy();px-=left;py-=top
        if x-3<=px<=x+w+3 and y-3<=py<=y+h+3:
            above=py-h-8;below=py+8
            if above>=6:y=above
            elif below+h<=maxh-6:y=below
            else:x=maxw-w-6 if px<maxw/2 else 6
        tip.place(x=int(x),y=int(y));tip.tk.call('raise',str(tip))

    def help_hint(self,event=None):
        self.clear_tip()
        if self.interactive and not self.menu_open:
            self.tip_timer=self.window.after(450,self.show_help_hint)

    def show_help_hint(self):
        self.tip_timer=None
        if not self.interactive or self.menu_open:return
        self.show_control_hint(self.menu_button,'Ajuda e opções')

    def refresh_shortcuts(self):
        self.clear_tip();self.welcome_size=None
        self.refresh_status()
        if self.welcome_shown:self.draw_welcome()
        if self.logo_hint.popup:self.logo_hint.refresh_text()

    def interaction_hint(self,interactive):
        self.interactive=interactive
        self.refresh_status()

    def refresh_status(self):
        """One status renderer shared by combat updates and native interaction changes."""
        import time
        s=self.status_snapshot;mode=s.get('mode');issue=s.get('error') or s.get('warnings') or s.get('stale')
        adjusting=self.app.hud_adjustment.get()
        welcome_state=(adjusting,self.app.shortcut('hud_adjustment'))
        if getattr(self,'welcome_lock_state',None)!=welcome_state:
            self.welcome_lock_state=welcome_state;self.welcome_size=None
            if self.welcome_shown:self.draw_welcome()
        source=('FALHA NA LEITURA' if s.get('error') else 'LEITURA PARCIAL' if issue else
                'MODO AJUSTE' if adjusting else 'SIMULAÇÃO' if mode=='simulation' else
                'AMOSTRA' if mode=='sample' else 'PREPARANDO CAPTURA' if preparing(s) else 'AO VIVO · EXPERIMENTAL' if s.get('running') else
                'PAUSADO' if self.status_fight else 'PRONTO')
        if not issue and not adjusting and time.monotonic()<self.feedback_until:source=self.feedback_text
        note=self.app.hud_status_text()
        for widget,text in ((self.source,source),(self.note,note)):
            if widget.cget('text')!=text:widget.configure(text=text)
        if adjusting and not self.return_button.winfo_manager():
            self.source.pack_forget()
            self.member_count.pack_forget()
            self.return_button.pack(side='right',padx=(4,0))
        elif not adjusting and self.return_button.winfo_manager():
            self.return_button.pack_forget();self.source.pack(side='left',fill='x',expand=True,padx=(4,0))
            if not self.pager.winfo_manager():self.member_count.pack(side='right')

    def hover(self, event):
        row=self.table.identify_row(event.y)
        if row==self.tip_row:return
        self.clear_tip(); self.tip_row=row
        if row:self.tip_timer=self.window.after(650,lambda:self.show_tip(row,event.x_root,event.y_root))

    def show_tip(self, row, x, y):
        self.tip_timer=None
        if not self.interactive or self.menu_open or not self.table.exists(row):return
        values=self.table.item(row,'values')
        text=f'{values[0]}\nDano {values[1]} · DPS médio {values[2]}\nTempo {values[3]} · Participação no dano {values[4]}'
        member=next((r for r in self.table.rows if str(r.get('dbid') or r['name'])==row),{})
        text+='\n'+CLASSES.get(member.get('class_key'),CLASSES['unknown'])[0]
        if member.get('hp_reading_complete',member.get('hp_percent_status')=='observed') is not True:
            text+='\nHP incompleto · veja o diagnóstico para a leitura de vida.'
        if member.get('damage_share_percent') is not None:
            text+=f"\n{member['damage_share_percent']:.1f}% do dano registrado do grupo neste alvo."
        self.show_inline_hint(text,x,y+14)


class MemberCanvas(tk.Canvas):
    def __init__(self,parent,hud):
        super().__init__(parent,bg=BG,highlightthickness=0,height=28,takefocus=True)
        self.hud=hud;self.rows=[];self.values={};self.row_height=32;self.heading_height=22
        self.offset=0;self.scroll_drag=None
        self.scrollbar=tk.Canvas(self,width=12,bg=BG,highlightthickness=0,cursor='hand2')
        self.scrollbar.bind('<ButtonPress-1>',self.begin_scroll)
        self.scrollbar.bind('<B1-Motion>',self.drag_scroll)
        self.scrollbar.bind('<ButtonRelease-1>',lambda event:setattr(self,'scroll_drag',None))
        self.loading_items=[];self.loading_frames={};self.loading_frame=0
        self.loading_timer=self.after(180,self.animate_loading)
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<MouseWheel>',lambda event:self.scroll_rows(-1 if event.delta>0 else 1))
        self.scrollbar.bind('<MouseWheel>',lambda event:self.scroll_rows(-1 if event.delta>0 else 1))
        self.bind('<Next>',lambda event:hud.turn(1));self.bind('<Prior>',lambda event:hud.turn(-1))
        self.bind('<Home>',lambda event:self.scroll_rows(-len(self.rows)))
        self.bind('<End>',lambda event:self.scroll_rows(len(self.rows)))
        self.name_font=tkfont.Font(root=parent,family='Segoe UI',size=9)

    def set_rows(self,rows):
        self.rows=rows;self.offset=min(self.offset,max(0,len(rows)-self.visible_capacity()))
        self.configure(height=self.heading_height+min(len(rows),self.visible_capacity())*self.row_height)
        self.draw()

    def visible_capacity(self):return max(1,min(5,self.hud.capacity))

    def scroll_rows(self,delta):
        self.hud.clear_tip();self.offset=max(0,min(self.offset+delta,max(0,len(self.rows)-self.visible_capacity())))
        self.draw();return 'break'

    def begin_scroll(self,event):
        self.focus_set()
        if self.thumb_top<=event.y<=self.thumb_bottom:self.scroll_drag=event.y-self.thumb_top
        else:self.scroll_drag=0;self.drag_scroll(event)

    def drag_scroll(self,event):
        if self.scroll_drag is None:return
        height=max(1,self.scrollbar.winfo_height());travel=max(1,height-(self.thumb_bottom-self.thumb_top))
        position=max(0,min(1,(event.y-self.scroll_drag)/travel))
        self.offset=round(position*max(0,len(self.rows)-self.visible_capacity()));self.draw()

    def clipped(self,text,width):
        if self.name_font.measure(text)<=width:return text
        while text and self.name_font.measure(text+'…')>width:text=text[:-1]
        return text+'…'

    def draw(self):
        from app import fmt
        self.delete('all');self.values={};self.loading_items=[]
        width=max(1,self.winfo_width()) if self.winfo_ismapped() else 330
        overflow=len(self.rows)>self.visible_capacity()
        w=max(1,width-(16 if overflow else 0));xs=(8,w*.54,w*.72,w*.86,w-5)
        if overflow:
            height=self.visible_capacity()*self.row_height
            self.scrollbar.place(x=width-12,y=self.heading_height,width=12,height=height)
            self.scrollbar.delete('all')
            self.scrollbar.create_line(6,2,6,height-2,fill='#584430',width=3)
            thumb=max(18,height*self.visible_capacity()/len(self.rows));travel=height-thumb
            self.thumb_top=travel*self.offset/(len(self.rows)-self.visible_capacity());self.thumb_bottom=self.thumb_top+thumb
            rounded_rect(self.scrollbar,3,self.thumb_top,6,thumb,3,fill=GOLD,outline='')
        else:self.scrollbar.place_forget()
        self.heading_photo=ImageTk.PhotoImage(surface_raster(w,self.heading_height,background_rgb(self),SURFACE,None,7),master=self)
        self.create_image(0,0,anchor='nw',image=self.heading_photo,tags='heading_band')
        for x,label in zip(xs,['Personagem','Total','DPS','Tempo','%']):
            self.create_text(x,11,text=label,anchor='w' if label=='Personagem' else 'e',fill=SECONDARY,font=('Segoe UI',8))
        peak=max((r.get('damage') or 0 for r in self.rows),default=0)
        for i,row in enumerate(self.rows[self.offset:self.offset+self.visible_capacity()]):
            y=self.heading_height+i*self.row_height;own=row['name']==self.hud.app.character.get().strip()
            key=str(row.get('dbid') or row['name'])
            share=row.get('damage_share_percent')
            percent=compact_percent(share)
            complete=share is not None
            values=(row['name'],fmt(row['damage']),fmt(row['dps_average'],1),fmt(row['duration_us']/1e6,1)+' s',percent)
            self.values[key]=values
            cls=row.get('class_key','unknown');colour=CLASSES.get(cls,CLASSES['unknown'])[1]
            if own:rounded_rect(self,0,y,w,self.row_height-1,7,fill='#30251D',outline='')
            emblem=self.hud.assets.get(cls,22)
            if emblem:self.create_image(6,y+2,image=emblem,anchor='nw')
            else:draw_icon(self,'unknown',9,y+5,19,colour)
            name=row['name']+(' · você' if own and w>=410 else '')
            self.create_text(38,y+14,text=self.clipped(name,max(38,w*.34-40)),anchor='w',fill=TEXT,font=('Segoe UI',9,'bold' if own else 'normal'))
            display=(compact_number(row['damage']),compact_number(row['dps_average']),compact_duration(row['duration_us']),compact_percent(share))
            for column,(x,value) in enumerate(zip(xs[1:],display)):
                if x==xs[-1] and not complete:
                    self.loading_items.append(self.create_image(x-6,y+14,image=self.loading_image(),tags='hp_loading'))
                    continue
                self.create_text(x,y+14,text=value,anchor='e',fill=TEXT if x!=xs[-1] else colour,font=('Segoe UI',9,'bold' if x==xs[2] else 'normal'),tags=('member_value',f'member_column_{column}'))
            by=y+self.row_height-4
            self.create_line(8,by,w-7,by,fill='#3A2D23',width=3,capstyle='round')
            if peak and row.get('damage'):
                self.create_line(8,by,8+(w-15)*row['damage']/peak,by,fill=colour,width=3,capstyle='round')

    def loading_image(self):
        frame=self.loading_frame
        if frame not in self.loading_frames:
            raster=Image.new('RGBA',(56,56));draw=ImageDraw.Draw(raster)
            draw.ellipse((8,8,48,48),outline='#6A5036',width=5)
            draw.arc((8,8,48,48),start=frame*30,end=frame*30+110,fill=GOLD,width=6)
            self.loading_frames[frame]=ImageTk.PhotoImage(raster.resize((14,14),Image.Resampling.LANCZOS),master=self)
        return self.loading_frames[frame]

    def animate_loading(self):
        if self.loading_items and self.winfo_ismapped():
            self.loading_frame=(self.loading_frame+1)%12
            for item in self.loading_items:self.itemconfigure(item,image=self.loading_image())
        self.loading_timer=self.after(180,self.animate_loading)

    def destroy(self):
        if self.loading_timer:self.after_cancel(self.loading_timer)
        super().destroy()

    def get_children(self):return tuple(self.values)
    def exists(self,key):return key in self.values
    def item(self,key,option):return self.values[key]
    def identify_row(self,y):
        index=int((y-self.heading_height)//self.row_height)
        return tuple(self.values)[index] if y>=self.heading_height and 0<=index<len(self.values) else ''


def pet_progress(entry,state=None):
    """Display confirmed account progress, never substitute a capture delta."""
    state=state or {}
    level=entry.get('level');souls=entry.get('souls');required=entry.get('next_required')
    stale=bool(entry.get('sync_pending') or entry.get('capture_pending'))
    if stale:
        return ('Confirmando evolução' if entry.get('awaiting_growth') else 'Confirmando progresso',
                'Relogue com a captura ativa' if state.get('relog_required') else 'Aguardando confirmação do jogo',False)
    if level==3 and entry.get('max_level_confirmed') and not entry.get('cached'):
        return 'Nv. 3 · Máximo','Nível completo',True
    if entry.get('cached'):
        if level==3 and (souls is None or entry.get('cached_fields')!=['level']):
            return 'Nv. 3 · Máximo',('Último nível salvo · relogue' if state.get('relog_required') else 'Último nível salvo'),True
        # A saved level is not a saved soul balance; never manufacture an
        # apparent --/25 total or mark incomplete metadata as complete.
        if souls is None:
            return ('Nv. '+str(level) if level is not None else 'Nível não recebido','Saldo inicial não recebido',False)
        if level is None or entry.get('cached_fields')==['level']:
            return f'{souls} alma'+('s' if souls!=1 else ''),'Nível salvo · aguardando confirmação' if level is not None else 'Nível não recebido',False
        if (type(level) is not int or level not in (1,2,3) or type(souls) is not int or souls<0
                or level<3 and souls>{1:25,2:75}[level]):
            return 'Saldo pendente','Nível e saldo aguardando confirmação',False
        level_text='Nv. '+str(level) if level is not None else 'Nv. —'
        balance=str(souls) if souls is not None else '—'
        caption=f'{level_text} · {balance}'+(f'/{required}' if required else '')
        return caption,('Último saldo salvo · relogue' if state.get('relog_required') else 'Último saldo salvo'),True
    if entry.get('metadata_missing') and not state.get('relog_required'):
        if entry.get('soul_balance_known'):
            return f'{souls} alma' if souls==1 else f'{souls} almas','Nível não recebido',False
        return ('Nv. '+str(level) if level is not None else 'Nível não recebido'),('Saldo inicial não recebido' if level is not None else 'Nível e saldo não recebidos'),False
    valid=(type(level) is int and level in (1,2,3) and type(souls) is int and
           souls>=0 and type(required) is int and required=={1:25,2:75,3:0}[level] and
           (level==3 or souls<=required))
    # A newly verified absolute balance is independent of an incomplete count
    # of this session's receipts. Legacy adapters still use completeness flags.
    stale=(entry.get('sync_pending') or entry.get('capture_pending') or
           (entry.get('account_reading_complete') is not True and
            (entry.get('session_count_complete') is False or state.get('session_count_complete') is False)))
    if not valid or entry.get('account_count_known') is False or stale:
        return ('Confirmando progresso' if stale else 'Saldo pendente','Aguardando dados da coleção',False)
    if level==3:return 'Nv. 3 · Máximo','Nível completo',True
    remaining=required-souls
    note='Falta 1 alma' if remaining==1 else f'Faltam {remaining} almas'
    return f'Nv. {level} · {souls}/{required}',note,True


class PetCanvas(tk.Canvas):
    """Recent mascotes fill the available width and height without stretching."""
    MIN_CARD_WIDTH=320
    GAP=8
    def __init__(self,parent,hud):
        super().__init__(parent,bg=BG,highlightthickness=0,height=70)
        self.hud=hud;self.state={};self.photos=[]
        self.loading_items=[];self.loading_frames={};self.loading_frame=0
        self.loading_timer=None
        self.card_size=None;self.card_photo=None
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<Map>',lambda event:self.start_loading())
        self.bind('<Unmap>',lambda event:self.stop_loading())

    def set_state(self,state):
        self.state=state;self.draw()

    def columns(self):
        return max(1,(self.winfo_width()+self.GAP)//(self.MIN_CARD_WIDTH+self.GAP))

    def visible_entries(self):
        return self.state.get('entries',[])[:self.columns()*self.row_capacity()]

    def row_capacity(self):
        return max(2,(self.winfo_height()+4)//46)

    def draw(self):
        self.delete('all');self.photos=[];self.loading_items=[]
        w,h=self.winfo_width(),self.winfo_height()
        if w<5 or h<5:self.stop_loading();return
        entries=self.visible_entries()
        if self.hud.app.hud_page.get()=='pets':self.hud.refresh_pet_summary()
        if self.state.get('relog_required') and self.hud.status_snapshot.get('running') and not entries:
            photo=ImageTk.PhotoImage(surface_raster(w,h,background_rgb(self),SURFACE,GOLD,10),master=self)
            self.photos.append(photo);self.create_image(0,0,anchor='nw',image=photo)
            self.create_text(w/2,h/2-25,text='Coleção fora de sincronia',fill=GOLD,font=('Segoe UI',9,'bold'))
            self.create_text(w/2,h/2+2,text='Relogue o personagem\npara atualizar os dados.',fill=TEXT,font=('Segoe UI',10),justify='center')
            self.stop_loading();return
        if not entries:
            icon_size=max(16,min(32,h-40))
            icon=self.hud.assets.get('filter_pets',icon_size)
            if icon:self.photos.append(icon);self.create_image(w/2,max(icon_size/2,h/2-30),image=icon)
            self.create_text(w/2,h/2,text='Aguardando a primeira alma',fill=TEXT,font=('Segoe UI',9,'bold'))
            self.create_text(w/2,h/2+22,text='Amplie a janela para ver mais mascotes.',fill=SECONDARY,font=('Segoe UI',8))
            self.stop_loading()
            return
        rows=self.row_capacity()
        rh=max(23,min(42,(h-4*(rows-1))//rows))
        columns=self.columns();cw=(w-self.GAP*(columns-1))//columns
        if self.card_size!=(cw,rh):
            self.card_size=(cw,rh)
            self.card_photo=ImageTk.PhotoImage(surface_raster(cw,rh,background_rgb(self),SURFACE,None,7),master=self)
        font=tkfont.Font(root=self,family='Segoe UI',size=8,weight='bold')
        note_font=tkfont.Font(root=self,family='Segoe UI',size=7)
        for i,entry in enumerate(entries):
            x=(i//rows)*(cw+self.GAP);y=(i%rows)*(rh+4)
            self.create_image(x,y,anchor='nw',image=self.card_photo,tags=('pet_card',))
            icon=self.hud.assets.get(entry.get('icon_key'),min(32,rh-4))
            if icon:self.photos.append(icon);self.create_image(x+20,y+rh/2,image=icon)
            else:
                self.create_oval(x+8,y+rh/2-10,x+28,y+rh/2+10,outline=SECONDARY)
                self.create_text(x+18,y+rh/2,text='?',fill=SECONDARY,font=('Segoe UI',9))
            caption,note,complete=pet_progress(entry,self.state)
            if (self.state.get('relog_required') and not entry.get('account_count_known')
                    and not entry.get('cached') and not entry.get('max_level_confirmed')):
                caption='Total pendente';note='Relogue para confirmar'
            caption_width=font.measure(caption)
            right_width=max(caption_width+(16 if not complete and not entry.get('metadata_missing') else 0),note_font.measure(note))
            name=entry['name'];name_width=max(40,cw-56-right_width)
            while len(name)>1 and font.measure(name+'…')>name_width:name=name[:-1]
            if name!=entry['name']:name+='…'
            self.create_text(x+40,y+rh*.28,text=name,anchor='w',fill=TEXT,font=font,tags=('pet_name',))
            subtitle=(f"+{entry['session_souls']} alma"+('s' if entry['session_souls']!=1 else '')+' nesta captura') if entry.get('session_souls',0)>0 else 'Histórico local' if entry.get('cached') else 'Coleção do personagem' if entry.get('account_count_known') else 'Saldo pendente'
            # Keep the receipt caption separate from the right-hand recovery note.
            available=cw-56-note_font.measure(note)
            original=subtitle
            while len(subtitle)>1 and note_font.measure(subtitle+'…')>available:subtitle=subtitle[:-1]
            if subtitle!=original:subtitle+='…'
            self.create_text(x+40,y+rh*.73,text=subtitle,anchor='w',fill=SECONDARY,font=note_font,tags=('pet_receipt',))
            if not complete and not entry.get('metadata_missing'):
                self.loading_items.append(self.create_image(x+cw-18-caption_width,y+rh*.28,image=self.loading_image(),tags='pet_loading'))
            self.create_text(x+cw-8,y+rh*.28,text=caption,anchor='e',fill=TEXT,font=('Segoe UI',8,'bold'),tags=('pet_progress',))
            self.create_text(x+cw-8,y+rh*.73,text=note,anchor='e',fill=GOLD,font=('Segoe UI',7),tags=('pet_note',))
        if self.loading_items:self.start_loading()
        else:self.stop_loading()

    def loading_image(self):
        frame=self.loading_frame
        if frame not in self.loading_frames:
            raster=Image.new('RGBA',(48,48));draw=ImageDraw.Draw(raster)
            draw.ellipse((7,7,41,41),outline='#6A5036',width=4)
            draw.arc((7,7,41,41),start=frame*30,end=frame*30+110,fill=GOLD,width=5)
            self.loading_frames[frame]=ImageTk.PhotoImage(raster.resize((12,12),Image.Resampling.LANCZOS),master=self)
        return self.loading_frames[frame]

    def animate_loading(self):
        self.loading_timer=None
        if self.loading_items and self.winfo_ismapped():
            self.loading_frame=(self.loading_frame+1)%12
            for item in self.loading_items:self.itemconfigure(item,image=self.loading_image())
            self.start_loading()

    def start_loading(self):
        if self.loading_items and self.winfo_ismapped() and self.loading_timer is None:
            self.loading_timer=self.after(180,self.animate_loading)

    def stop_loading(self):
        if self.loading_timer:self.after_cancel(self.loading_timer);self.loading_timer=None

    def destroy(self):
        self.stop_loading()
        super().destroy()


def combat_waiting_text(snapshot,scope):
    if snapshot.get('error'):return 'Falha na captura. Abra o painel pela lupa.'
    if not snapshot.get('running'):return 'A captura começa automaticamente quando o jogo for detectado.'
    if not snapshot.get('identified') and not snapshot.get('group_encounters'):
        return 'Aguardando identificar seu personagem.\nDanos de terceiros ficam fora do DPS.'
    if scope=='bosses' and (snapshot.get('encounters') or snapshot.get('group_encounters')):
        return 'Sem boss identificado. Consulte a aba Monstros.'
    return 'DPS disponível · aguardando novos danos.'


def compact_duration(microseconds):
    """Whole elapsed units; formatting does not change encounter arithmetic."""
    seconds=max(0,int(microseconds or 0)//1_000_000)
    if seconds>=3600:return f'{seconds//3600}h'
    if seconds>=60:return f'{seconds//60}m'
    return f'{seconds}s'


def compact_percent(value):
    from app import fmt
    if value is None:return '—'
    return ('999' if value>=999 else fmt(value,1))+'%'


def compact_number(value):
    from app import fmt
    if value is None:return '—'
    for divisor,suffix in ((1e9,' B'),(1e6,' M'),(1e3,' k')):
        if abs(value)>=divisor:return fmt(value/divisor,1)+suffix
    return fmt(value,1 if isinstance(value,float) else 0)
