# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""First Windows monitor. Main panel + a movable, topmost compact window."""
from startup_identity import enforce_expected_user_sid
enforce_expected_user_sid()

from installation_guard import enforce_elevated_installation
enforce_elevated_installation()

import argparse
import csv
import json
import threading
import time
import subprocess
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from datetime import datetime
import uuid
import os
import tempfile
import sys

from collector import Controller, list_interfaces
from game_window import GameWindow, PanelHotkey, fit_hud, hud_visible, enable_dpi_awareness
from hud_view import CompactHUD
from presentation import Catalog, TargetSelector, alpha_for, resize_box, combat_history
from hud_assets import RasterAssets
from game_startup import GameLifetime, game_processes
from app_paths import export_directory

ROOT=Path(__file__).resolve().parent
VERSION='0.8.80'
C=dict(bg='#241B15',surface='#241B15',ink='#FFF5E8',muted='#D6BEA2',brown='#D5A65F',
       gold='#D5A65F',line='#513C29',green='#A6D7A8',soft='#493225',dark='#201812',panel='#30251D',light='#FFF5E8')


def fmt(value, digits=0):
    if value is None: return '—'
    return f'{value:,.{digits}f}'.replace(',','_').replace('.',',').replace('_','.')


def csv_safe_event(event):
    """Keep external text as text in spreadsheets; leave JSON/numbers intact."""
    result=dict(event,dps_screen_validated=False)
    for field in ('actor_name','target','skill_name'):
        value=result.get(field)
        if not isinstance(value,str) or not value:continue
        leading=value.lstrip(' \t\r\n')
        if value[0] in '\t\r\n' or (leading and leading[0] in '=+-@'):
            result[field]="'"+value
    return result


def save_export(controller, destination=None):
    folder=Path(destination) if destination is not None else export_directory();folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
    report=controller.export()
    # The provisional nearby-actor view from v61 was not the user's DPS scope.
    # Never export that presentation as a personal/party encounter, even if an
    # older in-memory producer supplies it. Raw diagnostic samples stay separate.
    for state in [report]+report.get('previous_connections',[]):
        for key in ('observed_encounters','observed_events','observed_active_key','observed_capture_scope'):
            state.pop(key,None)
        for key in ('events','group_events','encounters','group_encounters'):
            if key in state:state[key]=[entry for entry in state[key] if not entry.get('observation_only')]
    path=folder/('sessao-'+stamp+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    csv_path=path.with_suffix('.csv')
    fields=['encounter_key','flow','clock_us','actor','actor_name','target','raw_skill','skill_name','candidate_amount','screen_confirmed','dps_screen_validated']
    events=list(report['events'])
    events.extend(report.get('group_events',[]))
    for previous in report['previous_connections']:
        events.extend(previous['events']);events.extend(previous.get('group_events',[]))
    events=list({e['id']:e for e in events}.values())
    with csv_path.open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore',delimiter=';');writer.writeheader()
        for event in events: writer.writerow(csv_safe_event(event))
    return path,csv_path


class App:
    def __init__(self, root, controller=None, preview=False, game_window=None, native_capture=False):
        self.root=root;self.controller=controller or Controller()
        self.root.withdraw()
        self.root.title('Império AION · Painel de combate v'+VERSION)
        self.root.geometry(f'{min(1320,self.root.winfo_screenwidth()-60)}x{min(700,self.root.winfo_screenheight()-80)}');self.root.minsize(1000,670);self.root.configure(bg=C['bg'])
        from native_identity import apply_window_icon
        apply_window_icon(self.root)
        self.root.protocol('WM_DELETE_WINDOW',self.hide_panel)
        self.root.bind('<Escape>',lambda event:self.escape_panel())
        self.overlay=None;self.last_table=None;self.mapping={};self.interfaces={};self.selected_key=None
        self.snapshot={};self.closing=False;self.instance_control=None
        self.login_ready=False;self.login_generation=None;self.login_character=None;self.current_page='capture'
        self.onboarding_complete=False;self.onboarding_skipped=False
        self.auto_capture_requested=False
        self.preview=preview;self.game=game_window or GameWindow()
        self.game_lifetime=GameLifetime();self.game_poll_at=0
        self.hotkey=None
        self.catalog=Catalog();self.selector=TargetSelector(self.catalog)
        self.viewport=None;self.drag_origin=None;self.resize_origin=None
        self.hud_manual_size=(390,370);self.hud_size=(390,370);self.hud_width=390
        self.hud_offset=None;self.hud_geometry=None;self.hud_view=None
        self.hud_hidden=False;self.hud_interactive=True;self.overlay_style=None;self.overlay_style_failed=False;self.overlay_topmost=None
        self.hud_adjustment=tk.BooleanVar(master=root,value=False)
        self.hud_adjustment_held=False
        self.hud_collapsed=False;self.logo_gesture=None
        self.hud_page=tk.StringVar(master=root,value='combat')
        self.hotkey_notice=False
        from character_cache import cache_directory
        self.preferences_path=cache_directory().parent/'preferencias-hud.json'
        preferences={}
        if not preview:
            for path in (self.preferences_path,ROOT/'preferencias-hud.json'):
                try:
                    value=json.loads(path.read_text(encoding='utf-8'))
                    if isinstance(value,dict):preferences=value;break
                except (OSError,ValueError):pass
        from shortcuts import load_preferences
        self.shortcuts=load_preferences(preferences.get('shortcuts'))
        self.hotkey=PanelHotkey(self.shortcuts) if not preview else None
        offset=preferences.get('offset') if isinstance(preferences,dict) else None
        if isinstance(offset,list) and len(offset)==2 and all(type(v) is int for v in offset):self.hud_offset=tuple(offset)
        size=preferences.get('size') if isinstance(preferences,dict) else None
        if isinstance(size,list) and len(size)==2 and all(type(v) is int for v in size) and 390<=size[0]<=1000 and 180<=size[1]<=900:
            self.hud_manual_size=(390,min(370,max(220,size[1])));self.hud_size=self.hud_manual_size;self.hud_width=390
        alpha=preferences.get('opacity',100) if isinstance(preferences,dict) else 100
        self.opacity=tk.IntVar(value=alpha if type(alpha) is int and 30<=alpha<=100 else 100)
        self.hud_scope=tk.StringVar(value='bosses')
        saved_name=preferences.get('character','Zepidem' if preview else '') if isinstance(preferences,dict) else ''
        if not isinstance(saved_name,str) or len(saved_name)>36 or any(ord(c)<32 for c in saved_name):saved_name=''
        self.capture_device='windivert'
        saved_interface='Automática · conexão do jogo'
        if native_capture:
            self.capture_device='native';saved_interface='Nativa · Windows · em validação'
        self.character=tk.StringVar(value=saved_name);self.interface=tk.StringVar(value=saved_interface)
        if not self.controller.running and self.controller.mode=='idle':
            self.controller.reader.character=saved_name
            self.controller.od_energy.character=saved_name
            self.controller.resource_gains.character=saved_name
            self.controller.pets.character=saved_name
        self.saved_interface_label=saved_interface
        self.auto_target=tk.BooleanVar(value=True);self.target=tk.StringVar(value='Aguardando primeiro dano')
        self.hud_target=tk.StringVar(value='Aguardando primeiro dano')
        self.group_note=tk.StringVar(value='Grupo e vida máxima: identificação automática pendente')
        self.group_summary=tk.StringVar(value='Aguardando combate')
        self.hud_table=None;self.hud_empty=None
        self.source=tk.StringVar(value='Pronto para iniciar · DPS experimental')
        self.identity=tk.StringVar(value='Identificação pendente')
        self.phase=tk.StringVar(value='Aguardando combate')
        self.damage=tk.StringVar(value='—');self.dps=tk.StringVar(value='—');self.duration=tk.StringVar(value='—')
        self.detail=tk.StringVar(value='Inicie a captura dos dados. Relogue o personagem.')
        self.result=tk.StringVar(value='')
        self.resource_status=tk.StringVar(value='')
        self.last_resource_check_status='idle'
        self.collection_status=tk.StringVar(value='Diagnóstico de arquivos ainda não iniciado.')
        self._collection_was_active=False
        from streaming_server import OverlayServer
        stream_preferences=preferences.get('streaming',{}) if isinstance(preferences.get('streaming'),dict) else {}
        self.streaming=OverlayServer(port=stream_preferences.get('port',38472),tokens=stream_preferences.get('tokens') if isinstance(stream_preferences.get('tokens'),dict) else None)
        self.streaming_options=dict(names=stream_preferences.get('names',True) is True,resources=stream_preferences.get('resources',False) is True)
        self.streaming_updated_at=0
        self._styles();self._build()
        threading.Thread(target=self._interfaces,daemon=True).start()
        self.tick_after=self.root.after(100,self.tick)
        self.overlay_after=self.root.after_idle(self.open_overlay)
        self.follow_after=self.root.after(350,self.follow_game)
        if not saved_name and not preview:self.root.after_idle(self.show_panel)

    def _styles(self):
        s=ttk.Style(self.root);s.theme_use('clam')
        s.configure('.',font=('Segoe UI',10),foreground=C['ink'],background=C['surface'])
        s.configure('TButton',padding=(11,8),borderwidth=1,relief='flat',background=C['surface'])
        s.map('TButton',background=[('active',C['bg']),('disabled',C['bg'])])
        s.configure('Primary.TButton',background='#614326',foreground='#FFE4B3',font=('Segoe UI',9,'bold'))
        s.map('Primary.TButton',background=[('active','#79582D'),('disabled','#423326')],foreground=[('disabled','#A68B70')])
        s.configure('TCombobox',fieldbackground=C['surface'],padding=5)
        s.map('TCombobox',fieldbackground=[('readonly',C['surface'])],foreground=[('readonly',C['ink'])])
        s.configure('TEntry',fieldbackground='#30251D',foreground=C['ink'],insertcolor=C['gold'],bordercolor='#61472E',padding=6)
        s.configure('TCombobox',fieldbackground='#30251D',background='#30251D',foreground=C['ink'],arrowcolor=C['gold'],bordercolor='#61472E',lightcolor='#61472E',darkcolor='#61472E')
        s.map('TCombobox',fieldbackground=[('readonly','#30251D')],selectbackground=[('readonly','#493225')],selectforeground=[('readonly',C['ink'])])
        s.configure('TScrollbar',background='#765B39',troughcolor=C['bg'],arrowcolor=C['muted'],borderwidth=0)
        s.configure('TCheckbutton',background=C['surface'])
        s.configure('Treeview',background='#2B2019',fieldbackground='#2B2019',foreground=C['ink'],font=('Segoe UI',9),rowheight=36,borderwidth=0,bordercolor=C['line'],lightcolor=C['line'],darkcolor=C['line'])
        s.configure('Treeview.Heading',background='#38291E',foreground='#E2C8A2',font=('Segoe UI',9,'bold'),padding=7,bordercolor=C['line'],lightcolor=C['line'],darkcolor=C['line'])
        s.map('Treeview',background=[('selected',C['soft'])],foreground=[('selected',C['ink'])])
        s.configure('HUD.Treeview',font=('Segoe UI',9),background=C['dark'],fieldbackground=C['dark'],foreground=C['light'],rowheight=24,borderwidth=0)
        s.configure('HUD.Treeview.Heading',font=('Segoe UI',8),background=C['panel'],foreground='#D6BEA2',padding=(4,4),borderwidth=0)
        s.map('HUD.Treeview',background=[('selected','#604629')],foreground=[('selected',C['light'])])
        s.layout('HUD.Treeview',[('Treeview.treearea',{'sticky':'nswe'})])

    def label(self,parent,text=None,var=None,size=10,bold=False,fg=None,bg=None):
        return tk.Label(parent,text=text,textvariable=var,font=('Segoe UI',size,'bold' if bold else 'normal'),
                        fg=fg or C['ink'],bg=bg or C['surface'],anchor='w')

    def _build(self):
        from combat_panel import CombatPanel
        self.panel=CombatPanel(self)

    def shortcut(self,action):
        return self.shortcuts[action]

    def hud_status_text(self):
        return self.shortcut('hud_adjustment')+' · '+('HUD Desbloqueada' if self.hud_adjustment.get() else 'HUD Bloqueada')

    def show_shortcuts(self):
        self.show_page('shortcuts');self.show_panel()

    def apply_shortcuts(self,bindings):
        from shortcuts import validate,ACTIONS
        try:bindings=validate(bindings)
        except ValueError as error:return False,str(error)
        if self.hotkey is not None:
            ok,errors=self.hotkey.replace_bindings(bindings)
            if not ok:
                self.hotkey_notice=False
                names=', '.join(bindings.get(key,key) for key in errors)
                return False,'O Windows não liberou '+names+'. Os atalhos anteriores foram mantidos. Escolha outra combinação.'
        self.shortcuts=dict(bindings);self.hotkey_notice=False
        saved=self.save_preferences()
        if self.hud_view:
            self.hud_view.close_menu();self.hud_view.clear_tip();self.hud_view.refresh_shortcuts()
        self.panel.refresh_header(self.snapshot)
        return True,('Atalhos aplicados e salvos neste computador.' if saved is not False else 'Atalhos aplicados nesta sessão, mas não foi possível salvar. Verifique a pasta de preferências.')

    def show_page(self,key):
        if key=='skills':key='summary'
        self.current_page=key
        if key!='shortcuts' and hasattr(self,'shortcut_editor'):self.shortcut_editor.cancel()
        self.panel.show_page(key)

    def update_login_access(self,snapshot):
        generation=snapshot.get('capture_generation',0)
        if self.login_generation!=generation or self.login_character!=snapshot.get('character'):
            self.login_ready=False;self.login_generation=generation
            self.login_character=snapshot.get('character')
            self.onboarding_complete=False
        if snapshot.get('mode') in ('sample','simulation'):
            self.login_ready=True
            self.onboarding_complete=True
        elif snapshot.get('mode')=='live' and snapshot.get('character_selection',{}).get('confirmed'):
            # An explicit return to selection is a new preparation boundary;
            # quiet traffic and map transitions alone never clear this latch.
            self.login_ready=False
            self.onboarding_complete=False
        elif (snapshot.get('mode')=='live' and snapshot.get('running') and not snapshot.get('error')
                and (snapshot.get('login_session',{}).get('entry_confirmed')
                     or snapshot.get('login_session',{}).get('confirmed'))):
            self.login_ready=True
        from capture_readiness import initial_readings
        if self.login_ready and all(initial_readings(snapshot).values()):
            self.onboarding_complete=True
        # Once a login is confirmed, quiet traffic or a map transition must not
        # force another startup relog. This flag is never saved across launches.
        # Combat events do not require the character's initial resource balances.
        # Keep history accessible without treating a damage packet as a login.
        for key in ('summary','skills'):
            self.nav_buttons[key].configure(state='normal')
        return self.login_ready

    def combat_ready(self,snapshot):
        return bool(self.login_ready or snapshot.get('running') and not snapshot.get('error'))

    def sort_skills(self):
        self.skill_reverse=not self.skill_reverse;self.render_skills()

    def render_skills(self):
        if not hasattr(self,'skills'):return
        from combat_charts import skill_colors
        selected=self.skills.selection()
        self.skills.delete(*self.skills.get_children());self.skill_lookup={}
        query=self.skill_query.get().casefold().strip();unknown=0
        colors=skill_colors(self.skill_rows)
        for item in sorted(self.skill_rows,key=lambda x:x['damage'],reverse=self.skill_reverse):
            name=self.panel.skill_catalog.name(item.get('code'),item.get('name',''))
            if name=='Habilidade não identificada':unknown+=1;name+=' '+str(unknown)
            if query and query not in name.casefold():continue
            iid=str(item['code']);self.skill_lookup[iid]=(item,name)
            self.skills.tag_configure(iid,foreground=colors[iid])
            options=dict(iid=iid,text=name,tags=(iid,),values=(item['records'],fmt(item['damage']),fmt(item['damage']/self.skill_total*100,1)+'%' if self.skill_total else '—'))
            icon=self.panel.skill_catalog.photo(self.root,item['code'])
            if icon is not None:options['image']=icon
            self.skills.insert('','end',**options)
        if selected and self.skills.exists(selected[0]):self.skills.selection_set(selected[0])
        self.skill_status.set(f'{len(self.skill_lookup)} habilidades · vermelho: maior dano · verde: menor dano · total '+fmt(self.skill_total))
        if hasattr(self,'technical_skills'):
            self.technical_skills.set('Códigos das habilidades do alvo selecionado:\n'+'\n'.join(f"{i.get('code','—')} · {self.panel.skill_catalog.name(i.get('code'),i.get('name',''))}" for i in self.skill_rows))
            self.codes.configure(state='normal');self.codes.delete('1.0','end');self.codes.insert('1.0',self.technical_skills.get());self.codes.configure(state='disabled')

    def select_skill(self,event=None):
        selected=self.skills.selection()
        if not selected or selected[0] not in self.skill_lookup:return
        item,name=self.skill_lookup[selected[0]]
        self.skill_detail.set(f"{name} · {item['records']} registros de dano · {fmt(item['damage'])} de dano. Registros não equivalem a ativações de habilidade.")

    def _group_table(self,parent,height=5,hud=False):
        frame=tk.Frame(parent,bg=C['dark'] if hud else C['surface']);frame.pack(fill='both',expand=True)
        columns=('name','damage','average','time','life')
        table=ttk.Treeview(frame,columns=columns,show='headings' if hud else 'tree headings',height=height,selectmode='browse',style='HUD.Treeview' if hud else 'Treeview')
        for key,label,width in [('name','Personagem',150),('damage','Dano total',110),('average','DPS médio',95),('time','Tempo',75),('life','Dano (%)',80)]:
            table.heading(key,text=label,anchor='w' if key=='name' else 'e')
            table.column(key,width=width,minwidth=width,stretch=key=='name',anchor='w' if key=='name' else 'e')
        if not hud:table.column('#0',width=46,minwidth=46,stretch=False);table.heading('#0',text='')
        table.tag_configure('own',background='#4E3825',foreground=C['gold'])
        scroll=ttk.Scrollbar(frame,orient='vertical',command=table.yview);table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');table.pack(side='left',fill='both',expand=True)
        return table

    def _update_group_table(self,table,rows):
        if table is None or not table.winfo_exists():return
        current=[]
        for row in sorted(rows,key=lambda r:-(r.get('damage') or 0)):
            from combat_charts import row_key
            key=row_key(row)
            current.append(key)
            share=row.get('damage_share_percent')
            percent=fmt(share,1)+'%' if share is not None else '—'
            values=(row['name'],fmt(row['damage']),fmt(row['dps_average'],1),fmt(row['duration_us']/1e6,1)+' s',percent)
            tags=('own',) if row['name']==self.character.get().strip() else ()
            from presentation import CLASSES
            cls=row.get('class_key','unknown');table.tag_configure(cls,foreground=CLASSES.get(cls,CLASSES['unknown'])[1]);tags=(cls,)
            icon=self.panel_assets.get(cls,24)
            options=dict(values=values,tags=tags,image=icon if icon is not None else '')
            if table.exists(key):table.item(key,**options);table.move(key,'',len(current)-1)
            else:table.insert('',len(current)-1,iid=key,**options)
        for key in table.get_children():
            if key not in current:table.delete(key)

    def _interfaces(self):
        native={'Nativa · Windows · em validação':'native'}
        if self.capture_device=='native':
            self.interfaces=native;return
        self.interfaces={'Automática · conexão do jogo':'windivert'}

    def start(self,automatic=False):
        if self.controller.running:return
        self.auto_capture_requested=True
        name=self.character.get().strip()
        if len(name)>36 or any(ord(c)<32 or ord(c)==127 for c in name):
            self.result.set('Informe um nome de personagem válido.');return
        from capture_elevation import needed,prepare,launch
        if needed():
            try:
                helper=prepare()
                self.save_preferences()
                launch(helper)
            except (OSError,RuntimeError) as exc:
                self.result.set(str(exc));return
            self.close();return
        device=self.capture_device if automatic else self.interfaces.get(self.interface.get(),
            self.capture_device if self.interface.get()==self.saved_interface_label else 'auto')
        self.last_table=None;self.selected_key=None;self.result.set('')
        self.controller.start(name,device)
        self.capture_device=device;self.saved_interface_label=self.interface.get();self.save_preferences()
        self.hud_adjustment.set(False);self.apply_hud_adjustment()

    def activate_auto_capture(self):
        if self.closing:return
        # A second startup request must not erase an active combat/collection.
        if self.controller.running and self.controller.mode=='live':return
        self.start(automatic=True)

    def stop(self):
        # Kept for older callers; only application shutdown ends live capture.
        self.result.set('A captura continua até fechar o monitor.')

    def demo(self):
        if self.controller.running:return
        self.last_table=None;self.selected_key=None;self.result.set('Amostra gravada · reprodução em 4×; não é uma captura ao vivo.')
        self.controller.demo()

    def choose_target(self,event=None):
        self.auto_target.set(False);self.selected_key=self.mapping.get(self.target.get());self.last_table=None

    def show_combat_hud(self):
        self.onboarding_skipped=True
        self.hud_page.set('combat')
        if self.hud_view:self.hud_view.clear_tip();self.hud_view.render()

    def change_scope(self):
        self.hud_page.set('combat')
        self.auto_target.set(True);self.selected_key=None;self.last_table=None
        if self.hud_view:self.hud_view.render()
        self.save_preferences()

    def toggle_pet_view(self):
        self.hud_page.set('combat' if self.hud_page.get()=='pets' else 'pets')
        self.hud_view.clear_tip();self.hud_view.render()

    def toggle_duty_view(self):
        self.hud_page.set('combat' if self.hud_page.get()=='duty' else 'duty')
        self.hud_view.clear_tip();self.hud_view.render()

    def reset_combat(self):
        self.controller.reset_combat()
        self.selected_key=None;self.last_table=None;self.mapping={};self.auto_target.set(True)
        self.snapshot=self.controller.snapshot()
        self.panel.group_chart.set_series([],None);self.panel.skill_chart.set_series([],None)
        self.skill_rows=[];self.skill_total=0;self.render_skills()
        self.hud_target.set('Aguardando novo combate')
        self.damage.set('—');self.dps.set('—');self.duration.set('—')
        for table in (self.group_table,self.skills):table.delete(*table.get_children())
        if self.hud_view:
            self.hud_view.feedback('Combate reiniciado')
            self.hud_view.update(self.snapshot,None,[])
        self.result.set('Combate e histórico reiniciados. A captura e a coleção de mascotes foram preservadas.')

    def reload_login_cache(self):
        self.snapshot=self.controller.reload_login_cache();self.last_table=None
        cache=self.snapshot.get('character_cache',{})
        text=('Não foi possível ler o cache: '+cache['error'] if cache.get('error') else
              'Dados salvos recarregados · captura preservada' if cache.get('selected') else
              'Sem dados salvos para este personagem')
        check=self.snapshot.get('resource_refresh',{})
        self.result.set(text+'. '+check.get('detail','Valores com ~ aguardam confirmação do jogo.'))
        self.update_resource_status(self.snapshot)
        if self.hud_view:
            self.hud_view.feedback('Verificando recursos · veja a lupa' if check.get('active') else text)
            fight=self.hud_view.status_fight;rows=self.hud_view.rows
            self.hud_view.update(self.snapshot,fight,rows)

    def update_resource_status(self,snapshot):
        from resource_refresh import NAMES,readings
        states=[]
        for key,value in readings(snapshot).items():
            status=('salvo (~)' if value.get('cached') else 'confirmado' if value.get('available') else 'aguardando')
            states.append(NAMES[key]+': '+status)
        check=snapshot.get('resource_refresh',{})
        self.resource_status.set(' · '.join(states)+'\n'+check.get('detail',''))
        status=check.get('status','idle')
        if status!=self.last_resource_check_status and self.last_resource_check_status=='watching':
            if self.hud_view:
                self.hud_view.feedback('Novas leituras recebidas' if status=='confirmed' else 'Verificação concluída · veja a lupa')
        self.last_resource_check_status=status

    def record_pets(self):
        try:self.controller.start_collection_diagnostic()
        except ValueError as error:self.result.set(str(error));return
        self._collection_was_active=True
        self.result.set('Diagnóstico de 10 minutos iniciado. Continue jogando; Salvar diagnóstico encerra o registro. A contagem de almas continua enquanto ON estiver ativo.')

    def update_collection_recording(self,snapshot):
        active=snapshot.get('collection_diagnostic_active',False)
        observed=snapshot.get('collection_diagnostic_transport_observed',0)
        pets=snapshot.get('pet_collection',{})
        if not active and not self._collection_was_active:
            self.collection_status.set(pets.get('detail','ON acompanha o combate e as almas coletadas.'))
        if active or observed or self._collection_was_active:
            self.collection_status.set(f"{observed} pacotes registrados · "+(snapshot.get('error') or snapshot.get('status','')))
        if active:
            remaining=snapshot.get('collection_diagnostic_remaining_seconds',0)
            self.record_pets_button.configure(text=f'Registrando · {remaining//60:02d}:{remaining%60:02d}',state='disabled')
        else:
            self.record_pets_button.configure(text='Diagnóstico de Arquivos • 10min',state='normal')
            if self._collection_was_active:
                self.result.set('Diagnóstico de arquivos concluído. Clique em Salvar diagnóstico de arquivos para guardar este teste.')
        self._collection_was_active=active

    def export_pets(self):
        self._collection_was_active=False
        report=self.controller.export_collection_diagnostic()
        rejected=report.get('capture_state',{}).get('pet_rejections',[])
        if not report.get('started_utc') and not report['messages'] and not report.get('transport_samples') and not rejected:
            self.result.set('Sem mensagens de coleção registradas. Inicie a captura e use Diagnóstico de Arquivos • 10min.');return
        folder=export_directory();folder.mkdir(parents=True,exist_ok=True)
        path=folder/('mascotes-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.json')
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        empty=not report['messages'] and not report.get('transport_samples') and not rejected
        self.result.set(('Diagnóstico sem pacotes salvo para investigar a captura: ' if empty else 'Diagnóstico local salvo: ')+str(path))

    def logo_press(self,event):
        self.logo_gesture=(event.x_root,event.y_root,False)
        self.begin_drag(event)
        event.widget.grab_set()

    def logo_motion(self,event):
        if not self.logo_gesture:return
        x,y,moved=self.logo_gesture
        moved=moved or abs(event.x_root-x)+abs(event.y_root-y)>=5
        self.logo_gesture=(x,y,moved)
        if moved:self.drag_hud(event)

    def logo_release(self,event):
        gesture=self.logo_gesture;self.logo_gesture=None
        try:event.widget.grab_release()
        except tk.TclError:pass
        self.end_drag(event)
        if gesture and not gesture[2]:self.toggle_hud_collapse()

    def toggle_hud_collapse(self):
        if not self.hud_view:return
        self.hud_view.opacity_slider.finish();self.hud_view.close_menu();self.hud_view.clear_tip()
        self.hud_collapsed=not self.hud_collapsed
        self.hud_size=(64,64) if self.hud_collapsed else self.hud_manual_size
        self.hud_view.set_collapsed(self.hud_collapsed)
        self.follow_game(schedule=False,drain_hotkeys=False)

    def toggle_hud_visibility(self):
        self.hud_hidden=not self.hud_hidden
        if self.hud_view:self.hud_view.opacity_slider.finish();self.hud_view.close_menu();self.hud_view.clear_tip()
        if self.hud_hidden and self.hotkey and self.hotkey.ready.is_set() and not self.hotkey.registrations.get('hud_visibility'):
            self.show_panel()
        self.follow_game(schedule=False)

    def toggle_hud_adjustment(self):
        self.hud_adjustment.set(not self.hud_adjustment.get())
        self.apply_hud_adjustment()

    def finish_hud_adjustment(self):
        self.hud_adjustment.set(False)
        self.apply_hud_adjustment()

    def apply_hud_adjustment(self,refresh=True):
        if not self.hud_adjustment.get():
            if self.drag_origin or self.resize_origin:self.save_preferences()
            self.drag_origin=None;self.resize_origin=None
            if self.hud_view:self.hud_view.opacity_slider.finish();self.hud_view.close_menu();self.hud_view.clear_tip()
        if refresh:self.follow_game(schedule=False)

    def end(self):
        if self.selected_key:self.controller.close_encounter(self.selected_key)

    def export(self):
        try:
            path,_=save_export(self.controller);self.result.set('Sessão salva: '+str(path))
            if self.hud_view:self.hud_view.feedback('Sessão salva · veja o painel')
        except Exception as exc:self.result.set('Não foi possível salvar: '+str(exc))

    def show_panel(self):
        self.root.deiconify();self.root.lift();self.root.focus_force()

    def ensure_taskbar(self):
        # A closed panel must not create a phantom minimized Alt+Tab entry.
        return

    def hide_panel(self):
        # Keep Tk/capture alive, but remove the panel window from the switcher.
        # The titlebar minimize action remains Tk's normal iconify operation.
        self.root.withdraw()
        if not self.overlay:self.open_overlay()
        self.follow_game(schedule=False)

    def dismiss_logo_hint(self):
        if self.hud_view and self.hud_view.logo_hint.pending:
            self.hud_view.logo_hint.dismiss();return True
        return False

    def escape_panel(self):
        if self.shortcut_editor.editing is not None:self.shortcut_editor.cancel()
        elif not self.dismiss_logo_hint():self.hide_panel()
        return 'break'

    def escape_overlay(self):
        if not self.dismiss_logo_hint():
            if self.hud_view.menu_open:self.hud_view.close_menu()
        return 'break'

    def open_overlay(self):
        if self.overlay and self.overlay.winfo_exists():
            self.follow_game(schedule=False);return
        self.overlay=tk.Toplevel(self.root);self.overlay.withdraw()
        self.overlay.title('Império AION · HUD v'+VERSION)
        from native_identity import apply_window_icon
        apply_window_icon(self.overlay)
        self.overlay.overrideredirect(True);self.overlay.configure(bg=C['dark'])
        if sys.platform=='win32':self.overlay.attributes('-toolwindow',True)
        self.overlay.protocol('WM_DELETE_WINDOW',self.close)
        self.overlay.bind('<Escape>',lambda event:self.escape_overlay())
        self.hud_view=CompactHUD(self,self.overlay)
        self.hud_table=self.hud_view.table
        self.apply_opacity(save=False)
        self.hud_view.render()
        self.follow_game(schedule=False)

    def close_overlay(self):
        self.close()

    def apply_opacity(self,save=True):
        if self.overlay:self.overlay.attributes('-alpha',alpha_for(self.opacity.get()))
        if save:self.save_preferences()

    def save_preferences(self):
        if self.preview:return
        try:
            self.preferences_path.parent.mkdir(parents=True,exist_ok=True)
            data=json.dumps(dict(offset=self.hud_offset,opacity=self.opacity.get(),size=self.hud_manual_size,scope=self.hud_scope.get(),character=self.character.get().strip(),capture_device=self.capture_device,capture_label=self.saved_interface_label,shortcuts=self.shortcuts,streaming=dict(self.streaming.settings(),**self.streaming_options)))
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.preferences_path.parent,suffix='.tmp',delete=False) as handle:
                    temporary=Path(handle.name);handle.write(data);handle.flush();os.fsync(handle.fileno())
                os.replace(temporary,self.preferences_path)
            finally:
                if temporary and temporary.exists():temporary.unlink()
            return True
        except OSError:
            self.result.set('Não foi possível guardar os ajustes da HUD nesta pasta.');return False

    def reset_hud_position(self):
        self.hud_offset=None;self.hud_geometry=None
        self.follow_game(schedule=False);self.save_preferences()

    def reset_hud_size(self):
        self.hud_manual_size=(390,370);self.hud_size=(64,64) if self.hud_collapsed else (390,370);self.hud_width=390
        self.hud_view.fit_height(370)
        self.hud_view.render();self.follow_game(schedule=False);self.save_preferences()


    def begin_resize(self,event,edge):
        self.resize_origin=(edge,event.x_root,event.y_root,(self.overlay.winfo_x(),self.overlay.winfo_y(),self.overlay.winfo_width(),self.overlay.winfo_height()))
        self.drag_origin=None
        self.hud_view.clear_tip()

    def resize_hud(self,event):
        if not self.resize_origin:return
        edge,ex,ey,box=self.resize_origin
        v=self.viewport
        bounds=(v.x,v.y,v.width,v.height) if v and not v.minimized else (0,0,self.root.winfo_screenwidth(),self.root.winfo_screenheight())
        x,y,w,h=resize_box(box,edge,(event.x_root-ex,event.y_root-ey),bounds,minimum=(390,200))
        if 'w' in edge:x+=w-390
        if 'n' in edge and h>370:y+=h-370
        w=390;h=min(370,max(220,h))
        self.hud_manual_size=(w,h);self.hud_size=(w,h);self.hud_width=w
        self.hud_view.fit_height(h)
        if v and not v.minimized:self.hud_offset=(x-v.x,y-v.y)
        self.place_hud(x,y,w,h)

    def end_resize(self,event):
        self.resize_origin=None;self.save_preferences()

    def begin_drag(self,event):
        self.drag_origin=(event.x_root,event.y_root,self.overlay.winfo_x(),self.overlay.winfo_y())
        if self.hud_view:self.hud_view.clear_tip()

    def drag_hud(self,event):
        if not self.drag_origin:return
        ex,ey,wx,wy=self.drag_origin
        x=wx+event.x_root-ex;y=wy+event.y_root-ey
        if self.viewport and not self.viewport.minimized:
            self.hud_offset=(x-self.viewport.x,y-self.viewport.y)
            x,y,w,h=fit_hud(self.viewport,self.hud_size,self.hud_offset)
            self.hud_offset=(x-self.viewport.x,y-self.viewport.y)
        else:
            w,h=self.hud_size
            x=max(0,min(x,self.root.winfo_screenwidth()-w))
            y=max(0,min(y,self.root.winfo_screenheight()-h))
        self.place_hud(x,y,w,h)

    def end_drag(self,event):
        self.drag_origin=None;self.save_preferences()

    def place_hud(self,x,y,w,h):
        geometry=(int(x),int(y),int(w),int(h))
        if geometry!=self.hud_geometry:
            self.overlay.geometry(f'{w}x{h}+{x}+{y}')
            self.hud_geometry=geometry
            # Apply pending Tk geometry before Windows changes topmost styles.
            self.overlay.update_idletasks()
            if not self.preview and hasattr(self.game,'rounded_overlay'):
                self.game.rounded_overlay(self.overlay.winfo_id(),w,h,16)

    def follow_game(self,schedule=True,drain_hotkeys=True):
        if self.closing:return
        if self.hotkey and drain_hotkeys:
            for action in self.hotkey.drain():
                if self.shortcut_editor.editing is not None:
                    self.shortcut_editor.accept(self.shortcuts.get(action,''));continue
                if time.monotonic()<self.shortcut_editor.suppress_until:continue
                if action=='reset_combat':self.reset_combat()
                elif action=='panel':self.show_panel()
                elif action=='hud_visibility':self.hud_hidden=not self.hud_hidden
                elif action=='hud_collapse':self.toggle_hud_collapse()
                elif action=='hud_adjustment':
                    self.hud_adjustment.set(not self.hud_adjustment.get())
                    self.apply_hud_adjustment(refresh=False)
        self.viewport=None if self.preview else self.game.viewport()
        self.visibility_button.configure(text='Mostrar HUD' if self.hud_hidden else 'Ocultar HUD')
        if self.hotkey and self.hotkey.ready.is_set() and not self.hotkey_notice:
            self.hotkey_notice=True
            failed=[name for name,ok in self.hotkey.registrations.items() if not ok]
            if failed:
                shortcuts=self.shortcuts
                self.result.set('Atalho global indisponível: '+', '.join(shortcuts.get(name,name) for name in failed)+'. Use os botões do painel.')
                if 'panel' in failed and self.viewport is None:self.show_panel()
        if self.overlay:
            self.hud_view.sync_welcome(self.snapshot)
            available=self.viewport.height if self.viewport and not self.viewport.minimized else 900
            if not self.hud_collapsed:self.hud_view.fit_height(min(available,self.hud_manual_size[1]) if self.hud_manual_size else available)
            own=self.game.own_foreground()
            visible=not self.hud_hidden and (self.preview or self.viewport is not None and hud_visible(self.viewport,own))
            if visible:
                if not self.drag_origin and not self.resize_origin:
                    if self.viewport and not self.viewport.minimized:
                        if self.hud_offset is None:self.hud_offset=(max(0,self.viewport.width-self.hud_size[0]-20),20)
                        x,y,w,h=fit_hud(self.viewport,self.hud_size,self.hud_offset)
                    else:
                        w,h=self.hud_size;x=max(0,self.root.winfo_screenwidth()-w-30);y=60
                    self.place_hud(x,y,w,h)
                topmost=self.preview or self.viewport is not None
                if self.overlay_topmost!=topmost:
                    self.overlay.attributes('-topmost',topmost);self.overlay_topmost=topmost
                self.hud_adjustment_held=False
                interactive=bool(self.hud_view.welcome_shown or self.hud_collapsed or self.hud_adjustment.get() or self.hud_view.menu_open or self.hud_view.opacity_slider.dragging or self.drag_origin or self.resize_origin)
                if self.overlay_style!=interactive and hasattr(self.game,'overlay_interaction'):
                    if self.game.overlay_interaction(self.overlay.winfo_id(),interactive):
                        self.overlay_style=interactive;self.hud_interactive=interactive;self.overlay_style_failed=False
                    else:
                        self.result.set('HUD ocultada: Windows não permitiu ajustar seus cliques. Feche e abra o monitor no Windows normal.')
                        if not self.overlay_style_failed:self.show_panel()
                        self.overlay_style=None;self.overlay_style_failed=True;self.hud_interactive=False
                elif not hasattr(self.game,'overlay_interaction'):self.hud_interactive=interactive
                if self.overlay_style_failed:
                    self.hud_view.close_menu();self.hud_view.clear_tip();self.overlay.withdraw()
                elif self.overlay.state()=='withdrawn':self.overlay.deiconify()
                if hasattr(self.game,'edit_overlay_focus') and not self.preview and not self.overlay_style_failed:
                    self.game.edit_overlay_focus(self.overlay.winfo_id(),bool(interactive))
                if not interactive:self.hud_view.clear_tip()
                if self.hud_view.popup:self.hud_view.popup.lift()
            else:
                self.hud_adjustment_held=False
                if hasattr(self.game,'edit_overlay_focus') and not self.preview:
                    self.game.edit_overlay_focus(self.overlay.winfo_id(),False)
                self.hud_view.opacity_slider.finish();self.hud_view.close_menu();self.hud_view.clear_tip()
                if self.overlay.state()!='withdrawn':self.overlay.withdraw()
            self.hud_view.interaction_hint(self.hud_interactive)
            self.hud_view.logo_hint.sync(visible and not self.overlay_style_failed)
        self.panel.refresh_header(self.snapshot)
        if schedule:self.follow_after=self.root.after(100,self.follow_game)

    def poll_game_exit(self):
        if self.preview:return False
        now=time.monotonic()
        if now<self.game_poll_at:return False
        self.game_poll_at=now+.5
        try:processes=game_processes()
        except OSError:processes=None
        if self.game_lifetime.observe(processes,now):
            self.close()
            return True
        # Begin the passive, owned-game connection observer before the first
        # selection response; recognizing that response itself requires it.
        # Request elevation at most once, and never retry a denied UAC in a loop.
        if processes and not self.auto_capture_requested and not self.controller.running:
            self.auto_capture_requested=True
            self.start(automatic=True)
            if self.closing:return True
        return False

    def tick(self):
        if self.closing:return
        if getattr(self,'tick_after',None):
            try:self.root.after_cancel(self.tick_after)
            except tk.TclError:pass
        if self.poll_game_exit():return
        if self.instance_control:
            for action in self.instance_control.poll():
                if action=='auto-capture':self.activate_auto_capture()
                elif action=='show-panel':self.show_panel()
        s=self.controller.snapshot();self.snapshot=s
        detected=s.get('metadata',{}).get('automatic_character',{})
        if (s.get('mode')=='live' and s.get('running') and detected.get('name')
                and detected['name']==s.get('character') and detected['name']!=self.character.get()):
            self.character.set(detected['name'])
            self.save_preferences()
        self.update_login_access(s)
        self.update_resource_status(s)
        self.update_collection_recording(s)
        source='AMOSTRA GRAVADA · '+s['status'] if s['mode']=='sample' else s['status']
        self.source.set(source)
        self.identity.set((s['character']+' · identificado nesta conexão') if s['identified'] else
            (s['character']+' · aguardando confirmação do jogo' if s['character'] else 'Aguardando identificação automática do personagem'))
        if s['identified'] and not s.get('named_identity_confirmed',s['identified']):
            self.identity.set('Seu personagem identificado para o DPS · nome pendente')
        running=s['running'];self.start_button.configure(state='disabled' if running else 'normal')
        self.demo_button.configure(state='disabled' if running else 'normal')
        if running:self.start_button.pack_forget()
        elif not self.start_button.winfo_manager():self.start_button.pack(side='left',padx=(0,8))
        self.entry.configure(state='disabled' if running else 'normal');self.nic.configure(state='disabled' if running else 'readonly')
        self.nic['values']=list(self.interfaces) or ['Automática · conexão do jogo']
        fights=combat_history(s);self.mapping={}
        available=[e for e in fights if self.selector.includes(s,e,self.hud_scope.get())]
        for index,e in enumerate(available):
            name=self.catalog.target(s,e)
            self.mapping[name+f" · tentativa {e['attempt']} · luta {index+1}"]=e['key']
        self.targets['values']=list(self.mapping)
        self.selected_key=self.selector.choose(s,fights,self.selected_key,self.hud_scope.get(),self.auto_target.get())
        fight=next((e for e in fights if e['key']==self.selected_key),None)
        self.export_button.configure(state='normal' if fights or s['error'] or s['packets'] else 'disabled')
        if fight:
            self.target.set(next(label for label,key in self.mapping.items() if key==fight['key']))
            name=self.catalog.target(s,fight)
            self.hud_target.set(name)
            if 'members' in fight:
                rows=fight['members']
                own=next((r for r in rows if r.get('self_identity') or r['name']==self.character.get().strip()),None)
                shown=own or dict(damage=None,dps_average=None,dps_current=None,skills=[])
            else:
                shown=fight
                rows=[dict(name=(fight.get('character_name') or 'Você · nome pendente') if 'character_name' in fight else s['character'],actor=fight.get('actor'),identity_pending='character_name' in fight and not fight['character_name'],damage=fight['damage'],dps_average=fight['dps_average'],dps_current=fight['dps_current'],duration_us=fight['duration_us'],hp_percent=fight.get('hp_percent'),hp_percent_status=fight.get('hp_percent_status'),damage_share_percent=100.0 if fight['damage']>0 else 0.0,skills=fight.get('skills',[]))]
            self.damage.set(fmt(fight['damage']));self.dps.set(fmt(fight['dps_average'],2));self.duration.set(fmt(fight['duration_us']/1e6,2)+' s')
            self.phase.set(('Dados parciais · luta encerrada' if fight['end_us'] is not None else 'Dados parciais · luta em andamento') if fight['partial'] else 'Luta encerrada · '+str(fight['end_reason']) if fight['end_us'] is not None else 'Luta em andamento · pausas incluídas')
            self.end_button.configure(state='normal' if fight['end_us'] is None else 'disabled')
        else:
            shown=None;rows=[]
            if s['group_rosters']:
                for m in s['group_rosters'][-1]['members']:
                    rows.append(dict(**m,damage=None,dps_average=None,dps_current=None,duration_us=0,hp_percent=None))
            waiting='Aguardando boss identificado' if self.hud_scope.get()=='bosses' else 'Aguardando primeiro dano atribuído'
            self.damage.set('—');self.dps.set('—');self.duration.set('—');self.target.set(waiting)
            self.hud_target.set(waiting)
            self.phase.set('Identificação pendente' if not s['identified'] else 'Aguardando combate');self.end_button.configure(state='disabled')
        rows=[dict(r,class_key=self.catalog.player_class(s,fight,r)) for r in rows]
        rows=[dict(r,hp_reading_complete=r.get('hp_percent_status')=='observed' and r.get('hp_percent') is not None
            and not bool(s.get('error') or s.get('warnings') or s.get('stale') or fight and fight.get('partial'))) for r in rows]
        self._update_group_table(self.group_table,rows)
        if self.hud_view:self.hud_view.update(s,fight,rows)
        roster_known=bool(s['group_rosters'] or fight and fight.get('members'))
        hp=fight.get('max_hp') if fight else None
        note=('Grupo automático · '+str(len(rows))+' membros' if roster_known else 'Grupo automático pendente · somente seu personagem')
        note+=' · Vida máxima: '+(fmt(hp) if hp else 'pendente')
        hp_conflict=bool(fight and fight.get('hp_status')=='conflict')
        if hp_conflict:note+=' · HP em conflito; % compara o dano do grupo'
        if s['group_roster_errors']:note+=' · Lista de grupo fora do perfil; salve o diagnóstico'
        self.group_note.set(note)
        summary='AMOSTRA GRAVADA · ' if s['mode']=='sample' else ''
        summary+='SIMULAÇÃO · ' if s['mode']=='simulation' else ''
        summary+=(fmt(fight['damage'])+' dano · '+fmt(fight['duration_us']/1e6,1)+' s · ' if fight else '')
        summary+=('grupo reconhecido' if roster_known else 'grupo pendente')+' · '+('% vida pendente' if not hp else 'vida '+fmt(hp))
        if hp_conflict:summary+=' · HP em conflito'
        if s['error'] or s['warnings'] or s['stale']:summary='Leitura parcial · consulte o painel'
        elif fight and fight['end_us'] is not None:summary+=' · encerrado'
        self.group_summary.set(summary)
        members=[r['name'] for r in rows]
        self.panel.member_choice['values']=members
        if self.skill_member.get() not in members:
            own=next((r['name'] for r in rows if r.get('self_identity')),None)
            self.skill_member.set(own or (self.character.get().strip() if self.character.get().strip() in members else members[0] if members else ''))
        if fight and 'members' in fight:shown=next((r for r in rows if r['name']==self.skill_member.get()),None)
        key=(self.selected_key,fight['damage'] if fight else None,fight['records'] if fight else None,self.skill_member.get())
        if key!=self.last_table:
            self.last_table=key
            self.skill_rows=list(shown['skills']) if shown else []
            self.skill_total=shown['damage'] if shown else 0
            self.render_skills()
        if s['error']:text='Falha na coleta: '+s['error']+' · Salve a sessão para obter o diagnóstico.'
        elif s['warnings']:text=' · '.join(s['warnings'])
        elif s['stale']:text='A leitura ficou sem novos pacotes. O tempo foi congelado; o resultado é parcial até a leitura voltar.'
        elif s['mode']=='sample':text='Amostra real: cinco valores principais conferidos na gravação. O total de dano e o DPS continuam experimentais.'
        elif s['mode']=='live' and s['running'] and not s.get('capture_ready',True):
            text='Preparando captura. Aguarde a indicação Captura pronta antes de entrar no personagem.'
        elif s['mode']=='live' and s['running'] and not s['identified'] and not s['group_encounters']:
            text='Captura ativa · aguardando o vínculo do seu personagem recebido do jogo. Danos sem identificação ficam fora do DPS. Mascotes e saldos iniciais aguardam o login, separadamente.'
        elif hp_conflict:text='O jogo enviou valores de vida máxima diferentes para este alvo. A coluna % mostra a participação no dano registrado do grupo. Salve a sessão para conferir o conflito de HP.'
        elif any(r.get('hp_percent_status')=='partial' for r in rows):
            text='≥ indica a parcela mínima de vida atribuída. Há quedas de HP com vários atacantes ou dano não interpretado; essas quedas não são divididas por estimativa. Dano e vida recebidos usam escalas diferentes.'
        elif fight and hp and any(r.get('hp_percent') is None for r in rows):
            text='A porcentagem aguarda quedas de HP vinculadas a uma única origem de dano. Dano exibido e vida recebida usam escalas diferentes; a porcentagem não divide esses valores diretamente.'
        else:text=f"DPS experimental · {s['packets']} pacotes observados · {s['rejected']} registros não interpretados. A luta começa no primeiro dano observado e termina no abate ou no encerramento manual."
        self.detail.set(text)
        self.panel.update_charts(s,fight,rows)
        now=time.monotonic()
        if any(self.streaming.enabled.values()) and now-self.streaming_updated_at>=.5:
            self.streaming_updated_at=now
            self.panel.streaming_view.refresh(s,fight,rows)
        self.tick_after=self.root.after(150,self.tick)

    def close(self):
        if self.closing:return
        self.closing=True;self.streaming.stop();self.controller.stop()
        for timer in (self.tick_after,self.overlay_after,self.follow_after):
            if timer:
                try:self.root.after_cancel(timer)
                except tk.TclError:pass
        if self.hotkey:self.hotkey.close()
        if self.hud_view:self.hud_view.close_menu();self.hud_view.clear_tip();self.hud_view.logo_hint.dismiss()
        if self.controller.reader.meter.encounters or self.controller.reader.group.timeline:
            try:save_export(self.controller)
            except Exception:pass
        self.root.destroy()


def main():
    parser=argparse.ArgumentParser(description='Império AION — monitor local experimental')
    parser.add_argument('--expected-user-sid',help=argparse.SUPPRESS)
    parser.add_argument('--demo',action='store_true');parser.add_argument('--speed',type=float,default=4)
    parser.add_argument('--panel',action='store_true')
    parser.add_argument('--auto-capture',action='store_true',help='Iniciar DPS e mascotes ao abrir com o jogo')
    parser.add_argument('--capture-native',action='store_true',help='Avaliar captura nativa do Windows sem TShark/Npcap; requer administrador')
    parser.add_argument('--recuperar-latesran',metavar='JSON',help='Recuperar entregas de um diagnóstico no histórico local')
    args=parser.parse_args()
    if args.demo and args.capture_native:parser.error('A avaliação da captura nativa não pode usar demonstração.')
    if args.demo and args.recuperar_latesran:parser.error('Recuperação de histórico não pode ser usada com --demo.')
    from game_startup import MonitorInstance
    instance=None if args.demo else MonitorInstance()
    try:
        if instance is not None and not instance.primary:
            if args.capture_native:parser.error('Feche a versão aberta antes de avaliar a captura nativa.')
            if args.recuperar_latesran:parser.error('Feche a versão aberta antes de recuperar o diagnóstico.')
            instance.request(auto_capture=args.auto_capture);return
        if args.recuperar_latesran:
            from diagnostic_recovery import recover_latesran
            try:recovered=recover_latesran(args.recuperar_latesran)
            except (ValueError,OSError) as exc:parser.error(str(exc))
            print(f"Histórico de {recovered['identity']['name']}: Latesran {recovered['current']}/5. O login ainda precisa ser confirmado na captura atual.")
        from native_identity import set_application_identity
        set_application_identity()
        enable_dpi_awareness()
        root=tk.Tk();app=App(root,preview=args.demo,native_capture=args.capture_native);app.instance_control=instance
        if args.panel:root.after_idle(app.show_panel)
        if args.auto_capture and not args.demo:root.after_idle(app.activate_auto_capture)
        if args.demo:root.after(300,lambda:app.controller.demo(max(.5,min(100,args.speed))))
        root.mainloop()
    finally:
        if instance:instance.close()


if __name__=='__main__':main()
