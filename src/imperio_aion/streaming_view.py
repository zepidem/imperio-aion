# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Native Streaming settings. The browser sources are independent and read-only."""
import tkinter as tk
import webbrowser
from panel_components import PanelButton
from panel_widgets import RoundedField
from streaming_state import projection
from combat_charts import compact

BG='#241B15';SURFACE='#30251D';TEXT='#FFF5E8';MUTED='#D6BEA2';GOLD='#D5A65F';LINE='#513C29'


class StreamingView:
    def __init__(self, app, body):
        self.app=app;self.server=app.streaming;self.preview='hud';self.views={};self.last_input=None
        self.buttons={};self.urls={};self.copy_buttons={};self.renew_buttons={}
        self.names=tk.BooleanVar(master=body,value=app.streaming_options['names'])
        self.resources=tk.BooleanVar(master=body,value=app.streaming_options['resources'])
        app.label(body,text='Streaming',size=20,bold=True).pack(anchor='w')
        app.label(body,text='Sua HUD e seu painel de combate no OBS Studio ou Streamlabs Desktop.',size=9,fg=MUTED).pack(anchor='w',pady=(8,19))
        columns=tk.Frame(body,bg=BG);columns.pack(fill='x');columns.columnconfigure(0,weight=1,uniform='stream');columns.columnconfigure(1,weight=1,uniform='stream')
        left=tk.Frame(columns,bg=BG);left.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        right=tk.Frame(columns,bg=BG);right.grid(row=0,column=1,sticky='nsew',padx=(12,0))
        toggles=tk.Frame(left,bg=BG);toggles.pack(fill='x',pady=(0,18));toggles.columnconfigure(0,weight=1);toggles.columnconfigure(1,weight=1)
        for i,(kind,label) in enumerate((('hud','Sobreposição HUD'),('painel','Sobreposição Painel'))):
            b=PanelButton(toggles,label,lambda k=kind:self.toggle(k),kind='capture',width=155,height=36)
            b.face.configure(size=8);b.grid(row=0,column=i,sticky='ew',padx=(0,4) if i==0 else (4,0));self.buttons[kind]=b
        for kind,label,size in (('hud','HUD','390 × 370 px'),('painel','Painel','1100 × 620 px')):
            title=tk.Frame(left,bg=BG);title.pack(fill='x',pady=(0,7))
            app.label(title,text='URL da Sobreposição '+label,size=9,fg=MUTED).pack(side='left')
            app.label(title,text=size,size=8,fg=MUTED).pack(side='right')
            var=tk.StringVar(master=body);self.urls[kind]=var
            RoundedField(left,var,readonly=True,height=36).pack(fill='x')
            actions=tk.Frame(left,bg=BG);actions.pack(fill='x',pady=(9,18))
            b=PanelButton(actions,'Copiar URL',lambda k=kind:self.copy(k),width=115,height=32,primary=True);b.pack(side='left');self.copy_buttons[kind]=b
            b=PanelButton(actions,'Gerar nova URL',lambda k=kind:self.renew(k),width=140,height=32);b.pack(side='left',padx=8);self.renew_buttons[kind]=b
        self.status=tk.StringVar(master=body,value='Ative uma sobreposição para disponibilizar sua URL local.')
        note=app.label(left,var=self.status,size=8,fg=MUTED);note.configure(wraplength=370,justify='left');note.pack(fill='x',pady=(0,12))
        app.label(left,text='A Sobreposição HUD acompanha sempre a aba ativa da HUD.',size=8,fg=MUTED).pack(anchor='w',pady=(0,12))
        self.name_toggle=PanelButton(left,'Exibir nomes dos jogadores',self.toggle_names,kind='adjust',height=32,width=320);self.name_toggle.pack(fill='x',pady=(0,7))
        self.resource_toggle=PanelButton(left,'Exibir recursos do personagem na HUD',self.toggle_resources,kind='adjust',height=32,width=320);self.resource_toggle.pack(fill='x')
        tk.Frame(left,bg=LINE,height=1).pack(fill='x',pady=(18,12))
        app.label(left,text='Adicionar à sua cena',bold=True,size=10).pack(anchor='w')
        instructions='1. Crie uma fonte Navegador / Browser Source.\n2. Cole a URL da HUD (390 × 370) ou do Painel (1100 × 620).\n3. Mantenha o Império AION aberto durante a transmissão.\n\nAs URLs funcionam apenas neste computador. Use uma fonte para cada sobreposição. Ao gerar outra URL, atualize-a no OBS/Streamlabs.'
        label=app.label(left,text=instructions,size=9,fg=MUTED);label.configure(wraplength=400,justify='left');label.pack(fill='x',pady=(9,0))
        left.bind('<Configure>',lambda e,l=label,n=note:(l.configure(wraplength=max(200,e.width)),n.configure(wraplength=max(200,e.width))))
        title=tk.Frame(right,bg=BG);title.pack(fill='x')
        app.label(title,text='Prévia da transmissão',size=10,bold=True).pack(side='left')
        self.size=tk.StringVar(master=body);app.label(title,var=self.size,size=8,fg=MUTED).pack(side='right')
        tabs=tk.Frame(right,bg=BG);tabs.pack(fill='x',pady=(11,8));self.preview_buttons={}
        for kind,label in (('hud','HUD'),('painel','Painel')):
            b=PanelButton(tabs,label,lambda k=kind:self.choose(k),width=86,height=29,kind='scope');b.pack(side='left',padx=(0,8));self.preview_buttons[kind]=b
        self.canvas=tk.Canvas(right,bg='#15100D',highlightthickness=1,highlightbackground=LINE,height=370,width=320)
        self.canvas.pack(fill='x');self.canvas.bind('<Configure>',lambda e:self.paint())
        self.open_button=PanelButton(right,'Visualizar no navegador',self.open,width=250,height=32,primary=True);self.open_button.pack(fill='x',pady=(12,7))
        app.label(right,text='Fundo transparente · somente dados de apresentação.\nNão inclui atalhos, configurações ou diagnósticos.',fg=MUTED,size=8).pack(anchor='w')
        self.render_controls()

    def toggle(self,kind):
        enabled=not self.server.enabled[kind]
        try:self.server.activate(kind,enabled)
        except OSError:
            self.status.set('Não foi possível abrir a sobreposição local. Verifique se o Windows permite conexões locais.');return
        self.preview=kind;self.last_input=None;self.refresh();self.app.save_preferences()
        self.status.set(('Sobreposição '+('HUD' if kind=='hud' else 'Painel')+' ativada. Copie a URL para sua fonte de navegador.') if enabled else 'Sobreposição desativada. A outra fonte mantém seu próprio estado.')
        self.render_controls()

    def copy(self,kind):
        url=self.server.url(kind)
        if not url:return
        self.app.root.clipboard_clear();self.app.root.clipboard_append(url)
        self.status.set('URL da '+('HUD' if kind=='hud' else 'Painel')+' copiada. Cole na fonte de navegador do OBS/Streamlabs.')

    def renew(self,kind):
        self.server.rotate(kind);self.app.save_preferences();self.render_controls()
        self.status.set('Nova URL gerada. A anterior deixou de funcionar; atualize a fonte no OBS/Streamlabs.')

    def toggle_names(self):self.names.set(not self.names.get());self.options_changed()
    def toggle_resources(self):self.resources.set(not self.resources.get());self.options_changed()
    def options_changed(self):
        self.app.streaming_options.update(names=self.names.get(),resources=self.resources.get())
        self.last_input=None;self.refresh();self.app.save_preferences();self.render_controls()

    def choose(self,kind):self.preview=kind;self.render_controls();self.paint()
    def open(self):
        if self.server.url(self.preview):webbrowser.open(self.server.url(self.preview))

    def render_controls(self):
        for kind,label in (('hud','HUD'),('painel','Painel')):
            enabled=self.server.enabled[kind]
            self.buttons[kind].configure(selected=enabled)
            self.urls[kind].set(self.server.url(kind) or 'Ative a Sobreposição '+label+' para gerar a URL')
            for buttons in (self.copy_buttons,self.renew_buttons):buttons[kind].configure(state='normal' if enabled else 'disabled')
            self.preview_buttons[kind].configure(selected=self.preview==kind)
        self.name_toggle.configure(selected=self.names.get());self.resource_toggle.configure(selected=self.resources.get())
        self.open_button.configure(state='normal' if self.server.enabled[self.preview] else 'disabled')
        self.size.set('390 × 370 px' if self.preview=='hud' else '1100 × 620 px')
        self.paint()

    def refresh(self,snapshot=None,fight=None,rows=None):
        if snapshot is None:
            snapshot=self.app.snapshot
            fight=self.app.hud_view.status_fight if self.app.hud_view else None
            rows=self.app.hud_view.rows if self.app.hud_view else []
        views,assets=projection(self.app,snapshot,fight,rows or [],self.names.get(),self.resources.get())
        self.views=views;self.server.publish(views,assets);self.paint()

    def paint(self):
        if not hasattr(self,'canvas') or not self.canvas.winfo_exists():return
        c=self.canvas;c.delete('all');w=max(300,c.winfo_width());h=370
        def text(x,y,value,color=TEXT,size=9,anchor='w'):c.create_text(x,y,text=value,fill=color,font=('Segoe UI',size),anchor=anchor)
        if not self.server.enabled[self.preview]:
            text(w/2,170,'Sobreposição '+('HUD' if self.preview=='hud' else 'Painel')+' desativada',GOLD,11,'center')
            text(w/2,198,'Ative a opção para visualizar.',MUTED,9,'center');return
        d=self.views.get(self.preview,{})
        if not d.get('ready'):
            text(w/2,170,d.get('waitingTitle','Inicie a captura'),GOLD,12,'center');text(w/2,198,d.get('waitingNote','O DPS pode ser iniciado durante a dungeon.'),MUTED,8,'center');return
        if self.preview=='painel':
            text(12,20,'Histórico de combate',GOLD,11);text(12,43,d.get('target',''),MUTED,8)
            text(12,72,'Rank de dano',TEXT,9);text(w/2+9,72,'Habilidades',TEXT,9)
            for i,row in enumerate(d.get('rows',[])[:10]):
                text(12,97+i*17,row['name'][:15],row['color'],8);text(w/2-10,97+i*17,compact(row['damage'] or 0),TEXT,8,'e')
            for i,row in enumerate(d.get('skills',[])[:10]):text(w/2+9,97+i*17,row['name'][:28],row['color'],7)
            for offset,key,mode,focus in ((0,'groupCurves',d.get('groupMode','total'),d.get('groupFocus')),(w/2,'skillCurves',d.get('skillMode','total'),d.get('selectedSkill'))):
                series=d.get(key,[]);maximum=max([p[1] for s in series for p in s[mode]] or [1]);duration=max(1,d.get('seconds',0))
                text(offset+12,291,'Dano/s' if mode=='second' else 'Dano',MUTED,7)
                c.create_line(offset+12,345,offset+w/2-10,345,fill=LINE)
                for s in series:
                    pts=[v for x,y in s[mode] for v in (offset+12+x/duration*(w/2-22),345-y/max(1,maximum)*45)]
                    if len(pts)>=4:c.create_line(*pts,fill=s['color'] if not focus or s['key']==focus else '#68534A',width=1)
            return
        labels={'combat':'Combate','pets':'Mascotes','duty':'Diárias e Semanais'}
        text(15,25,labels.get(d.get('view'),'Combate')+(' · '+d['character'] if d.get('character') else ''),GOLD,10)
        text(15,49,d.get('target','')[:48],MUTED,8)
        if d.get('view')=='duty':
            for i,row in enumerate(d.get('activities',[])):
                y=78+i*34;text(14,y,row['name'],TEXT,8);text(w-14,y,row['count'],GOLD,9,'e');text(14,y+13,row['note'],MUTED,7)
        elif d.get('view')=='pets':
            for i,row in enumerate(d.get('pets',[])):
                y=83+i*49;text(14,y,row['name'][:29],TEXT,9);text(w-14,y+16,row['caption'],GOLD,8,'e');text(14,y+16,row['note'],MUTED,7)
        else:
            peak=max([r.get('damage') or 0 for r in d.get('rows',[])] or [1])
            for i,row in enumerate(d.get('rows',[])):
                y=90+i*35;text(15,y,row['name'],row['color'],9);text(w-16,y,compact(row.get('damage') or 0),TEXT,9,'e')
                c.create_line(15,y+13,15+(w-30)*(row.get('damage') or 0)/max(1,peak),y+13,fill=row['color'],width=2)
            text(15,294,'Dano do grupo  '+compact(d.get('total') or 0),GOLD,9);text(15,316,'DPS médio  '+compact(d.get('dps') or 0),MUTED,9)
        if d.get('resources'):text(15,352,'    '.join(r['text'] for r in d['resources']),GOLD,8)
