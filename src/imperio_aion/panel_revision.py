# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Compact native combat history, diagnostics and local-service information."""
from pathlib import Path
import json,sys,tkinter as tk,webbrowser
from tkinter import ttk
from panel_components import PanelButton,PanelArt,ShortcutEditor
from panel_widgets import RoundedTable,RoundedField,GoldScrollbar
from hud_assets import RasterAssets
from combat_charts import DamageChart,row_key,chart_series,skill_colors
from skill_catalog import SkillCatalog
from boss_picker import BossPicker
from streaming_view import StreamingView
from character_information import character_information
from presentation import class_key,CLASSES
from display_identity import LOCAL_PLAYER_LABEL,player_label
ROOT=Path(__file__).resolve().parent
BG='#241B15';SURFACE='#30251D';RAIL='#201812';TEXT='#FFF5E8';MUTED='#D6BEA2';GOLD='#D5A65F';LINE='#513C29'

def number(value,digits=0):
    if value is None:return '—'
    return f'{value:,.{digits}f}'.replace(',','_').replace('.',',').replace('_','.')

class Page(tk.Frame):
    def __init__(self,parent,scrollable=True):
        super().__init__(parent,bg=BG);self.scrollable=scrollable
        if not scrollable:
            self.body=tk.Frame(self,bg=BG,padx=22,pady=14);self.body.pack(fill='both',expand=True);return
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0)
        self.scroll=GoldScrollbar(self,command=self.canvas.yview,autohide=True)
        self.canvas.configure(yscrollcommand=self.scroll.set);self.canvas.pack(side='left',fill='both',expand=True)
        self.body=tk.Frame(self.canvas,bg=BG,padx=24,pady=18);self.window=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))
    def show(self):self.tkraise()
    def wheel(self,event):
        if self.scrollable and self.body.winfo_reqheight()>self.canvas.winfo_height():self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')

class CombatPanel:
    def __init__(self,app):
        self.app=app;app.panel=self;self.root=app.root;self.art=PanelArt(self.root);self.skill_catalog=SkillCatalog();self.page_views={};self.last_scope=None;self.last_data=None
        self.build()
    def label(self,*args,**kwargs):return self.app.label(*args,**kwargs)
    def button(self,parent,text,command,**kwargs):return PanelButton(parent,text,command,**kwargs)
    def table(self,parent,columns,ratios,height=250,show='headings',rowheight=24):
        frame=tk.Frame(parent,bg=BG);frame.pack(fill='x')
        table=RoundedTable(frame,columns=columns,ratios=ratios,height=height,show=show,rowheight=rowheight)
        table.pack(side='left',fill='both',expand=True)
        table.scrollbar=GoldScrollbar(frame,command=table.yview,autohide=True)
        table.configure(yscrollcommand=table.scrollbar.set);return table
    def build(self):
        a=self.app;a.panel_assets=RasterAssets(self.root)
        header=tk.Frame(self.root,bg=BG,padx=20,pady=5);header.pack(fill='x')
        a.panel_avatar=self.art.get('logo',(110,72));tk.Label(header,image=a.panel_avatar,bg=BG,borderwidth=0).pack(side='left',padx=(0,22))
        self.character=tk.StringVar(value=LOCAL_PLAYER_LABEL);self.label(header,var=self.character,bold=True,size=10).pack(side='left')
        self.class_icon=tk.Label(header,bg=BG,borderwidth=0);self.class_icon.pack(side='left',padx=9)
        self.button(header,'Voltar à HUD',a.hide_panel,width=145,primary=True,image=self.art.get('return',23)).pack(side='right')
        a.visibility_button=self.button(header,'Ocultar HUD',a.toggle_hud_visibility,width=140,image=self.art.get('eye-open',24));a.visibility_button.pack(side='right',padx=8)
        tk.Frame(self.root,bg=LINE,height=1).pack(fill='x')
        shell=tk.Frame(self.root,bg=BG);shell.pack(fill='both',expand=True)
        rail=tk.Frame(shell,bg=RAIL,width=202,padx=12,pady=15);rail.pack(side='left',fill='y');rail.pack_propagate(False)
        host=tk.Frame(shell,bg=BG);host.pack(side='left',fill='both',expand=True);host.rowconfigure(0,weight=1);host.columnconfigure(0,weight=1)
        a.pages={};a.nav_buttons={}
        for key,title in (('summary','Histórico de combate'),('capture','Captura'),('diagnostic','Diagnóstico'),('streaming','Streaming'),('privacy','Privacidade'),('terms','Termos de Serviço'),('shortcuts','Atalhos')):
            page=Page(host,scrollable=key!='summary');page.grid(row=0,column=0,sticky='nsew');self.page_views[key]=page;a.pages[key]=page.body
            if key=='shortcuts':continue
            art_key='summary' if key=='summary' else key
            b=self.button(rail,title,lambda k=key:a.show_page(k),width=178,height=42,kind='nav',image=self.art.get(art_key,27));b.pack(fill='x',pady=(0,4));a.nav_buttons[key]=b
        bottom=tk.Frame(rail,bg=RAIL);bottom.pack(side='bottom',fill='x')
        self.capture_state=self.button(bottom,'Captura inativa',a.start,width=178,height=34,kind='capture');self.capture_state.pack(fill='x',pady=(0,8))
        self.adjust=self.button(bottom,'Modo de ajuste',a.toggle_hud_adjustment,width=178,height=34,kind='adjust');self.adjust.pack(fill='x',pady=(0,8))
        b=self.button(bottom,'Atalhos',a.show_shortcuts,width=178,height=34);b.pack(fill='x');a.nav_buttons['shortcuts']=b
        self.label(bottom,text='Império AION · v0.8.70',size=8,fg=MUTED,bg=RAIL).pack(anchor='w',pady=(14,0))
        # Existing consumers may address the former skills route; it is one page now.
        a.pages['skills']=a.pages['summary'];a.nav_buttons['skills']=a.nav_buttons['summary'];self.page_views['skills']=self.page_views['summary']
        self.summary();self.capture();self.diagnostic();self.streaming_view=StreamingView(a,a.pages['streaming']);self.legal('privacy');self.legal('terms')
        self.root.bind('<Configure>',self.resize_charts,add='+')
        a.shortcut_editor=ShortcutEditor(a,a.pages['shortcuts'])
        self.root.bind_all('<MouseWheel>',self.wheel,add='+')
        a.show_page('capture');self.refresh_header({})
    def resize_charts(self,event=None):
        if event is not None and event.widget is not self.root:return
        height=max(72,min(134,self.root.winfo_height()-566))
        for chart in (self.group_chart,self.skill_chart):
            if int(chart.canvas.cget('height'))!=height:chart.canvas.configure(height=height)
    def wheel(self,event):
        if self.root.state()!='normal' or isinstance(event.widget,(RoundedTable,tk.Text,ttk.Combobox)):return
        widget=event.widget
        while widget is not None:
            if widget is self.page_views[self.app.current_page].body:self.page_views[self.app.current_page].wheel(event);return
            widget=getattr(widget,'master',None)
    def show_page(self,key):
        key='summary' if key=='skills' else key;self.app.targets.dismiss();self.page_views[key].show()
        for name,b in self.app.nav_buttons.items():b.configure(selected=('summary' if name=='skills' else name)==key)
    def refresh_header(self,snapshot):
        from capture_readiness import source_ready,preparing
        a=self.app;running=source_ready(snapshot)
        self.capture_state.configure(text='Preparando captura' if preparing(snapshot) else 'Captura ativa' if running else 'Captura inativa',selected=running)
        self.adjust.configure(selected=a.hud_adjustment.get())
        guild=snapshot.get('login_session',{}).get('guild')
        self.character.set((f'<{guild}> ' if isinstance(guild,str) and guild else '')+LOCAL_PLAYER_LABEL)
        cls=class_key(snapshot.get('character_cache',{}).get('class_id'));self.class_photo=a.panel_assets.get(cls,25)
        self.class_icon.configure(image=self.class_photo or '')
        a.visibility_button.configure(text='Mostrar HUD' if a.hud_hidden else 'Ocultar HUD',image=self.art.get('eye-closed' if a.hud_hidden else 'eye-open',24))
        if hasattr(a,'shortcut_editor'):a.shortcut_editor.refresh_status()
    def summary(self):
        a=self.app;body=a.pages['summary']
        heading=tk.Frame(body,bg=BG);heading.pack(fill='x')
        self.label(heading,text='Histórico de combate',size=18,bold=True).pack(side='left')
        self.follow=self.button(heading,'Seguir último alvo',lambda:(a.auto_target.set(not a.auto_target.get()),self.follow.configure(selected=a.auto_target.get())),width=145,height=30,kind='follow',primary=True);self.follow.pack(side='right')
        a.targets=BossPicker(heading,a.target,width=250,height=32);a.targets.pack(side='right',padx=10);a.targets.bind('<<ComboboxSelected>>',a.choose_target)
        overview=tk.Frame(body,bg=BG);overview.pack(fill='x',pady=(5,9))
        self.scope_buttons={}
        for key,label,asset in (('bosses','Bosses','filter_bosses'),('all','Todos os monstros','filter_all')):
            b=self.button(overview,label,lambda k=key:(a.hud_scope.set(k),a.change_scope(),self.refresh_scopes()),width=100 if key=='bosses' else 165,height=30,kind='scope',image=a.panel_assets.get(asset,24));b.pack(side='left',padx=(0,10));self.scope_buttons[key]=b
        for var,label in ((a.duration,'tempo'),(a.dps,'DPS médio'),(a.damage,'dano total')):
            self.label(overview,text=label,fg=MUTED,size=8).pack(side='right',padx=(3,11));self.label(overview,var=var,bold=True,size=9).pack(side='right')
        self.refresh_scopes()
        columns=tk.Frame(body,bg=BG);columns.pack(fill='x');columns.columnconfigure(0,weight=1,uniform='analysis');columns.columnconfigure(1,weight=1,uniform='analysis')
        left=tk.Frame(columns,bg=BG);left.grid(row=0,column=0,sticky='nsew',padx=(0,12));right=tk.Frame(columns,bg=BG);right.grid(row=0,column=1,sticky='nsew',padx=(12,0))
        left_title=tk.Frame(left,bg=BG,height=30);left_title.pack(fill='x',pady=(0,5));left_title.pack_propagate(False)
        self.rank_title=self.label(left_title,text='Rank de dano',size=11,bold=True);self.rank_title.pack(side='left')
        self.member_count=tk.StringVar();self.label(left_title,var=self.member_count,size=8,fg=MUTED).pack(side='right')
        a.group_table=self.table(left,('name','damage','average','time','life'),(9,25,22,15,13,16),height=252,show='tree headings',rowheight=22)
        a.group_table.heading('#0',text='Classe',anchor='w')
        for key,label in (('name','Personagem'),('damage','Dano total'),('average','DPS Médio'),('time','Tempo'),('life','Dano (%)')):a.group_table.heading(key,text=label,anchor='w' if key=='name' else 'e');a.group_table.column(key,anchor='w' if key=='name' else 'e')
        a.group_table.bind('<<TreeviewSelect>>',self.select_player);a.group_table.bind('<Return>',self.select_player)
        self.group_description=tk.StringVar(value='Participação no dano registrado no alvo.')
        self.label(left,var=self.group_description,size=8,fg=MUTED).pack(anchor='w',pady=(5,8))
        self.group_chart=DamageChart(left,'Dano do grupo',on_select=self.player_curve_selected,columns=5);self.group_chart.pack(fill='x')
        title=tk.Frame(right,bg=BG,height=30);title.pack(fill='x',pady=(0,5));title.pack_propagate(False)
        self.label(title,text='Habilidades',size=11,bold=True).pack(side='left')
        self.member_icon=tk.Label(title,bg=BG);self.member_icon.pack(side='left',padx=(8,2))
        a.skill_member=tk.StringVar(value=a.character.get());self.skill_member_label=tk.StringVar(value=LOCAL_PLAYER_LABEL)
        self.label(title,var=self.skill_member_label,size=8,fg=GOLD).pack(side='left')
        self.member_choice={};a.skill_query=tk.StringVar();a.skill_rows=[];a.skill_total=0;a.skill_reverse=True
        RoundedField(title,a.skill_query,width=140,height=28).pack(side='right');a.skill_query.trace_add('write',lambda *_:a.render_skills())
        a.skill_status=tk.StringVar();a.skill_detail=tk.StringVar();self.skills_footer=tk.StringVar(value='Nenhuma habilidade recebida')
        a.skills=self.table(right,('records','damage','share'),(49,14,22,15),height=252,show='tree headings',rowheight=30)
        a.skills.heading('#0',text='Habilidade',anchor='w')
        for key,label in (('records','Registros'),('damage','Dano total'),('share','Dano (%)')):a.skills.heading(key,text=label,anchor='e');a.skills.column(key,anchor='e')
        a.skills.heading('damage',command=a.sort_skills)
        a.skills.bind('<<TreeviewSelect>>',lambda e:(a.select_skill(e),self.select_skill_curve()));a.skills.bind('<Return>',self.select_skill_curve)
        self.label(right,text='Registros de dano não equivalem a ativações.',size=8,fg=MUTED).pack(anchor='w',pady=(5,8))
        self.skill_chart=DamageChart(right,'Dano das habilidades');self.skill_chart.pack(fill='x')
    def refresh_scopes(self):
        for key,b in self.scope_buttons.items():b.configure(selected=self.app.hud_scope.get()==key)
    def select_player(self,event=None):
        a=self.app;key=a.group_table.identify_row(event.y) if event and getattr(event,'num',None)==1 else next(iter(a.group_table.selection()),None)
        if key and a.group_table.exists(key):
            focus=None if self.group_chart.selected==key else key
            member=getattr(a,'group_member_rows',{}).get(key)
            if member is None:return
            a.group_table.selection_set(key);a.skill_member.set(member['name']);a.last_table=None
            if a.snapshot:a.tick()
            self.group_chart.selected=focus;self.group_chart.draw()
    def player_curve_selected(self,key):
        a=self.app
        if key and a.group_table.exists(key):
            member=getattr(a,'group_member_rows',{}).get(key)
            if member is None:return
            a.group_table.selection_set(key);a.skill_member.set(member['name']);a.last_table=None
            if a.snapshot:a.tick()
    def select_skill_curve(self,event=None):
        a=self.app;key=a.skills.identify_row(event.y) if event and getattr(event,'num',None)==1 else next(iter(a.skills.selection()),None)
        if key and key in a.skill_lookup:self.skill_chart.select(str(a.skill_lookup[key][0]['code']))
    def capture(self):
        a=self.app;body=a.pages['capture'];self.label(body,text='Captura',size=20,bold=True).pack(anchor='w',pady=(0,18))
        config=tk.Frame(body,bg=BG);config.pack(fill='x');config.columnconfigure(1,weight=1)
        for col,label in ((0,'Personagem · automático'),(1,'Interface de captura')):self.label(config,text=label,size=9,fg=MUTED).grid(row=0,column=col,sticky='w',pady=(0,8))
        self.character_label=tk.StringVar(value=LOCAL_PLAYER_LABEL)
        a.entry=RoundedField(config,self.character_label,width=230,height=36);a.entry.grid(row=1,column=0,padx=(0,18));a.entry.configure(state='readonly')
        a.nic=RoundedField(config,a.interface,choice=True,height=36);a.nic.grid(row=1,column=1,sticky='ew')
        controls=tk.Frame(body,bg=BG);controls.pack(fill='x',pady=16)
        a.start_button=self.button(controls,'Iniciar captura',a.start,primary=True);a.start_button.pack(side='left')
        a.demo_button=self.button(controls,'Testar amostra',a.demo)
        if not getattr(sys,'frozen',False):a.demo_button.pack(side='left',padx=8)
        a.export_button=self.button(controls,'Salvar sessão',a.export,primary=True);a.export_button.pack(side='right')
        for text,command in (('Recarregar dados',a.reload_login_cache),('Reiniciar combate',a.reset_combat)):
            self.button(controls,text,command,width=142).pack(side='left',padx=6)
        a.end_button=self.button(controls,'Encerrar luta',a.end,width=120);a.end_button.pack(side='left',padx=6)
        for var in (a.source,a.result):self.label(body,var=var,size=9,fg=MUTED).pack(anchor='w',pady=(0,7))
        self.label(body,text='Captura incluída no aplicativo · conexão direta ou acelerador opcional · requer administrador.',size=9,fg=MUTED).pack(anchor='w',pady=(4,8))
        self.label(body,text='Seu personagem aparece como Você. A identificação é automática; não é necessário preencher o nome.',size=9,fg=MUTED).pack(anchor='w',pady=(0,8))
        self.label(body,text='Dados do personagem',size=12,bold=True).pack(anchor='w',pady=(22,9))
        self.character_table=self.table(body,('field','value','state'),(30,47,23),height=400,rowheight=27)
        for key,label in (('field','Informação'),('value','Valor'),('state','Status')):self.character_table.heading(key,text=label,anchor='w')
        self.character_table.tag_configure('confirmed',foreground='#B5DFB2');self.character_table.tag_configure('pending',foreground='#EDC281')
        self.label(body,text='Confirmado: recebido nesta conexão. Última leitura: salvo no computador. Pendente: ainda não confirmado.',size=8,fg=MUTED).pack(anchor='w',pady=8)
    def diagnostic(self):
        a=self.app;body=a.pages['diagnostic'];self.label(body,text='Diagnóstico',size=20,bold=True).pack(anchor='w')
        self.label(body,text='Estado da leitura e arquivos para análise.',fg=MUTED).pack(anchor='w',pady=(9,18))
        for var in (a.identity,a.source,a.phase,a.group_note,a.collection_status,a.result):
            label=self.label(body,var=var,fg=MUTED,size=9);label.configure(wraplength=950,justify='left');label.pack(fill='x',pady=5)
        controls=tk.Frame(body,bg=BG);controls.pack(fill='x',pady=(15,18))
        a.record_pets_button=self.button(controls,'Diagnóstico de Arquivos • 10min',a.record_pets,width=265);a.record_pets_button.pack(side='left')
        self.button(controls,'Salvar diagnóstico de arquivos',a.export_pets,width=260,primary=True).pack(side='left',padx=10)
        self.label(body,text='Alvos e IDs',size=12,bold=True).pack(anchor='w',pady=(7,8))
        self.npc_query=tk.StringVar();RoundedField(body,self.npc_query,width=400).pack(fill='x',pady=(0,9))
        self.npc_table=self.table(body,('name','id','kind'),(57,22,21),height=230,rowheight=27)
        for key,label in (('name','Alvo'),('id','ID da espécie'),('kind','Classificação')):self.npc_table.heading(key,text=label,anchor='w')
        self.npc_count=tk.StringVar();self.label(body,var=self.npc_count,fg=MUTED,size=8).pack(anchor='w',pady=7)
        self.label(body,text='Um alvo sem ID de espécie confirmado permanece pendente; nomes não são atribuídos por suposição.',fg=MUTED,size=8).pack(anchor='w')
        self.npc_query.trace_add('write',lambda *_:self.search_npcs());self.search_npcs()
    def search_npcs(self):
        term=self.npc_query.get().strip().casefold();rows=[(code,row) for code,row in self.app.catalog.npcs.items() if term in (code+' '+row.get('name','')).casefold()]
        self.npc_table.delete(*self.npc_table.get_children())
        for code,row in rows[:100]:self.npc_table.insert('','end',iid=code,values=(row['name'],code,'Treinamento' if row.get('isDummy') else 'Boss' if row.get('isBoss') else 'Monstro'))
        self.npc_count.set(f'{len(rows):,} resultados · {len(self.app.catalog.npcs):,} registros no catálogo'.replace(',','.')+(' · exibindo os primeiros 100; refine a busca' if len(rows)>100 else ''))
    def legal(self,key):
        data=json.loads((ROOT/'dados/legal-pt.json').read_text(encoding='utf-8'));content=data[key];body=self.app.pages[key]
        self.label(body,text=content['title'],size=20,bold=True).pack(anchor='w')
        self.label(body,text='Versão '+data['version']+' · '+data['date'],size=8,fg=MUTED).pack(anchor='w',pady=(8,19))
        def paragraph(text,size=10,color=MUTED):
            label=self.label(body,text=text,size=size,fg=color);label.configure(wraplength=920,justify='left');label.pack(fill='x',pady=(0,9))
            def resize(e,l=label):
                width=max(240,min(920,e.width-60))
                if int(l.cget('wraplength'))!=width:l.configure(wraplength=width)
            self.page_views[key].canvas.bind('<Configure>',resize,add='+')
        paragraph(content['intro'],10,TEXT)
        for title,text in content['sections']:
            tk.Frame(body,bg=LINE,height=1).pack(fill='x',pady=(17,14));self.label(body,text=title,size=12,bold=True,fg=GOLD).pack(anchor='w',pady=(0,8));paragraph(text)
        self.button(body,'support@imperiobdo.com',lambda:webbrowser.open('mailto:support@imperiobdo.com'),width=250).pack(anchor='w',pady=(15,8))
    def update_charts(self,snapshot,fight,rows):
        a=self.app;self.refresh_header(snapshot)
        if a.current_page=='capture':
            values=character_information(snapshot,a.character.get())
            if values!=self.last_data:
                self.last_data=values;self.character_table.delete(*self.character_table.get_children())
                for i,(name,value,status) in enumerate(values):self.character_table.insert('','end',iid=str(i),values=(name,value,status),tags=('confirmed' if status=='Confirmado' else 'pending' if status in ('Pendente','Parcial') else 'normal',))
        if a.current_page not in ('summary','skills') or self.root.state()!='normal':return
        self.rank_title.configure(text='Rank de dano')
        self.group_chart.heading='Dano do grupo'
        self.group_chart.title.configure(text=self.group_chart.heading)
        self.group_description.set('Participação no dano registrado no alvo.')
        self.member_count.set(f'{len(rows)} personagens');self.refresh_scopes();self.follow.configure(selected=a.auto_target.get())
        member=next((r for r in rows if r['name']==a.skill_member.get()),None)
        self.skill_member_label.set(player_label(member) if member else LOCAL_PLAYER_LABEL)
        if member:
            self.member_photo=a.panel_assets.get(member.get('class_key','unknown'),21);self.member_icon.configure(image=self.member_photo or '')
            key=row_key(member);a.group_table.selection_set(key)
        players,skills,partial=chart_series(snapshot,fight,rows,a.character.get().strip(),self.skill_catalog)
        scope=(snapshot.get('capture_generation'),fight['key'] if fight else None)
        self.group_chart.set_series(players,scope)
        values=[dict(s,key=s['code']) for s in skills if member and s['member']==row_key(member)]
        palette=skill_colors(a.skill_rows)
        for s in values:s['color']=palette.get(s['code'],MUTED)
        self.skill_chart.set_series(values,(scope,a.skill_member.get()))
