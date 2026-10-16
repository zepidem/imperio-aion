# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Daily/weekly activity presentation, independent of shop purchase counters."""
import tkinter as tk
from PIL import ImageTk
from hud_controls import surface_raster, background_rgb
from daily_duty import BRASILIA, duty_presentation
from weekly_orders import next_order_reset

ORDER_TYPES = {
    'altgard': ('Ordem Altgard', 12, 'Pergaminho de Ordem: Altgard'),
    'abyss': ('Ordem Latesran', 5, 'Pergaminho de Ordem da Raiz do Leste de Latesran'),
}
ENTRY_TYPES = {
    'daily_dungeon': ('Masmorra Diária',14,'Semanal',True),
    'shugo_party': ('Shugo Festa',21,'Recarga automática',False),
    'nightmare': ('Pesadelo',14,'Recarga automática',True),
    'ascension_rite': ('Rito de Ascensão',3,'Semanal',True),
}
ACTIVITY_KEYS=('daily','altgard','abyss',*ENTRY_TYPES)
ACTIVITY_ICONS=dict(daily='scroll',altgard='scroll',abyss='scroll',
                    daily_dungeon='dungeon',shugo_party='shugo',
                    nightmare='nightmare',ascension_rite='rite')


def entry_row(key, snapshot):
    name,limit,period,extras=ENTRY_TYPES[key]
    hint=f'{name}\nEntradas disponíveis; limite normal: {limit}.\n'
    hint+=('Renovação no reinício semanal do jogo.' if period=='Semanal' else
           'As entradas acumulam até o limite e são recuperadas automaticamente. O intervalo de recarga aguarda confirmação.')
    if extras:hint+='\nBilhetes extras são mostrados separadamente: saldo(+extras)/limite.'
    entries=snapshot.get('activity_entries',{})
    value=entries.get(key,{}) if isinstance(entries,dict) else {}
    valid=(isinstance(value,dict) and value.get('available') is True
           and type(value.get('current')) is int and 0<=value['current']<=limit
           and type(value.get('bonus',0)) is int and 0<=value.get('bonus',0)<=100_000
           and (extras or not value.get('bonus',0)))
    count=f'—/{limit}';note=period+' · Contagem pendente'
    if valid:
        bonus=value.get('bonus',0);cached=bool(value.get('cached'))
        count=('~' if cached else '')+str(value['current'])+(f'(+{bonus})' if bonus else '')+f'/{limit}'
        note=period+' · '+('Última leitura salva' if cached else 'Entradas disponíveis')
        hint+=f"\nSaldo normal: {value['current']}; bilhetes extras: {bonus}."
        if cached:hint+='\nÚltima leitura salva; aguarde a confirmação atual. Se estiver dessincronizado, relogue com a captura ativa.'
    else:hint+='\nAguardando leitura do saldo de entradas.'
    return dict(key=key,name=name,count=count,note=note,hint=hint,complete=False)


def activity_rows(snapshot, now=None):
    duty = duty_presentation(snapshot.get('daily_duty', {}))
    rows = [dict(key='daily', name='Dever • Continente', count=duty['text'].removeprefix('Dever '),
                 note='Diário · '+duty['note'], hint='Dever • Continente\n'+duty['hint'], complete=duty['complete'])]
    reset = next_order_reset(now).strftime('%d/%m às %H:%M')
    # Altgard awaits a weekly login field; Latesran uses an absolute counter.
    for key, (name, limit, scroll) in ORDER_TYPES.items():
        rows.append(dict(key=key, name=name+(' • Abismo' if key=='abyss' else ' • Continente'), count=f'—/{limit}',
            note='Semanal · Contagem pendente', complete=False,
            hint=f'{scroll}\nAté {limit} Ordens por semana, por servidor. Limites separados.\n'
                 f'Reinício: quarta-feira às 04:00 de Brasília. Próximo: {reset}.\n'
                 'A contagem automática de conclusões aguarda validação.\n'
                 'Comprar ou aceitar um pergaminho não confirma a conclusão.'))
        if key=='altgard':
            value=snapshot.get('weekly_orders',{}).get('altgard',{})
            if value.get('available') and type(value.get('current')) is int and value['current']>=0:
                n=value['current'];partial=bool(value.get('partial'));cached=bool(value.get('cached'))
                note=('Dessincronizado · relogue' if value.get('reading_error') else
                    'Histórico parcial' if partial and cached else
                    'Parcial · Desde a primeira leitura' if partial else
                    'Última contagem salva' if cached else 'Base 17 · Automático')
                rows[-1].update(count=('~' if cached else '')+f'{n}'+('+' if partial else '')+'/12',
                    note='Semanal · '+note,complete=False)
            elif value.get('reset_due'):
                rows[-1]['note']='Semanal · Aguardando base da nova semana'
            elif value.get('reading_error'):
                rows[-1]['note']='Semanal · Leitura dessincronizada'
            elif value.get('observed_deliveries'):
                rows[-1]['note']=f"Semanal · {value['observed_deliveries']} entrega(s) registrada(s) · Total pendente"
            else:
                rows[-1]['note']='Semanal · Aguardando total automático'
            hint=f'{scroll}\nBase semanal adotada: 17 Ordens. Cálculo: acumulado de Ordens − 17 − Latesran da semana.\n'
            if value.get('available') and value.get('reference_base')==17:
                hint+=f"Leitura: {value.get('accumulated')} − 17 − {value.get('latesran')} = {value['current']} Altgard.\n"
            hint+='A regra é aplicada no login, sem preencher contador e sem usar a primeira leitura como zero. O histórico fica separado por personagem, servidor e semana.\n'
            hint+='Reinício configurado: quarta-feira às 04:00 de Brasília. A base 17 segue a regra solicitada; seu reinício no servidor ainda aguarda validação na virada semanal.\n'
            hint+='A referência 12 corresponde às compras semanais e não trunca entregas superiores. Valores apenas salvos aparecem com ~.\nSe houver leitura incompatível com a regra, o monitor orienta relogar com a captura ativa.'
            rows[-1]['hint']=hint
        if key=='abyss':
            value=snapshot.get('weekly_orders',{}).get('latesran',{})
            hint=f'{scroll}\nAté 5 por semana, por servidor. Reinício: quarta às 04:00 de Brasília.\n'
            hint+='Leitura experimental: entregas de 1/5 a 5/5 conferidas nos diagnósticos. A virada semanal ainda precisa de validação no jogo.'
            if value.get('available'):
                n=value['current'];cached=value.get('cached',False)
                note='Reiniciado · confirme no jogo' if value.get('reset_pending') else 'Informado pelo jogador' if value.get('manual') else 'Última leitura salva' if cached else 'Completo' if n==5 else f'Faltam {5-n}'
                rows[-1].update(count=('~' if cached else '')+f'{n}/5',note='Semanal · '+note,complete=n==5 and not cached)
                if cached:hint+='\nValor em cache; a próxima entrega atualiza a leitura. Se estiver dessincronizado, relogue com a captura ativa.'
            else:
                rows[-1]['note']='Semanal · '+('Semana reiniciada' if value.get('reset_due') else 'Aguardando entrega')
            rows[-1]['hint']=hint
    rows.extend(entry_row(key,snapshot) for key in ENTRY_TYPES)
    return rows


class ActivityView(tk.Frame):
    """Natural-height activity cards in a local scroll viewport."""
    MAX_CARD_WIDTH=360
    def __init__(self, parent, hud):
        super().__init__(parent, bg=parent['bg'])
        self.hud = hud
        self.rows = []
        self.widgets = {}
        self.scroll_drag=None;self.scroll_visible=False;self.row_step=28
        self.viewport=tk.Canvas(self,bg=self['bg'],highlightthickness=1,highlightbackground=self['bg'],
                                highlightcolor='#D5A65F',takefocus=True,yscrollincrement=1)
        self.viewport.pack(side='left',fill='both',expand=True)
        self.scrollbar=tk.Canvas(self,width=12,bg=self['bg'],highlightthickness=0,cursor='hand2',takefocus=False)
        self.scrollbar.bind('<Configure>',lambda event:self.draw_scrollbar())
        self.scrollbar.bind('<ButtonPress-1>',self.begin_scroll)
        self.scrollbar.bind('<B1-Motion>',self.drag_scroll)
        self.scrollbar.bind('<ButtonRelease-1>',lambda event:setattr(self,'scroll_drag',None))
        self.viewport.configure(yscrollcommand=self.on_scroll)
        self.content=tk.Frame(self.viewport,bg=self['bg'])
        self.content_window=self.viewport.create_window(1,1,anchor='nw',window=self.content)
        self.viewport.bind('<Configure>',self.fit_cards)
        self.content.bind('<Configure>',self.update_scroll_region)
        for index, key in enumerate(ACTIVITY_KEYS):
            self.content.rowconfigure(index, weight=0)
            row = tk.Frame(self.content, bg=self['bg'])
            row.grid(row=index, column=0, sticky='new', pady=(0, 2 if index < len(ACTIVITY_KEYS)-1 else 0))
            row.columnconfigure(1, weight=1)
            surface = tk.Canvas(row, bg=self['bg'], highlightthickness=0)
            surface.place(x=0, y=0, relwidth=1, relheight=1)
            surface.bind('<Configure>', lambda event, canvas=surface: self.draw_surface(canvas))
            photo=hud.assets.get('activity_'+ACTIVITY_ICONS[key],24)
            icon=tk.Label(row,image=photo or '',bg='#30251D',borderwidth=0,padx=0,pady=0)
            icon.image=photo
            icon.grid(row=0,column=0,rowspan=2,sticky='w',padx=(4,5))
            name = tk.Label(row, bg='#30251D', fg='#FFF5E8', anchor='w',
                            font=('Segoe UI', 8, 'bold'), borderwidth=0, pady=0)
            name.grid(row=0, column=1, sticky='ew', pady=(1,0))
            count = tk.Label(row, bg='#30251D', fg='#FFF5E8', anchor='e',
                             font=('Segoe UI', 11, 'bold'), borderwidth=0, pady=0)
            count.grid(row=0, column=2, rowspan=2, sticky='e', padx=8)
            note = tk.Label(row, bg='#30251D', fg='#D6BEA2', anchor='w',
                            font=('Segoe UI', 7), borderwidth=0, pady=0)
            note.grid(row=1, column=1, sticky='ew', pady=(0,1))
            self.widgets[key] = dict(row=row, name=name, count=count, note=note,icon=icon)
            for widget in (row, surface, icon, name, count, note):
                hud.bind_hint(widget, lambda key=key: self.hint(key))
                widget.bind('<MouseWheel>',self.mousewheel)
                widget.bind('<Button-4>',lambda event:self.scroll_units(-1))
                widget.bind('<Button-5>',lambda event:self.scroll_units(1))
        for widget in (self,self.viewport,self.scrollbar):widget.bind('<MouseWheel>',self.mousewheel)
        for key,command in (('Home',lambda:self.scroll_to(0)),('End',lambda:self.scroll_to(1)),
                            ('Up',lambda:self.scroll_units(-1)),('Down',lambda:self.scroll_units(1)),
                            ('Prior',lambda:self.scroll_units(-1,'pages')),('Next',lambda:self.scroll_units(1,'pages'))):
            self.viewport.bind('<'+key+'>',lambda event,command=command:command())
        self.set_state({})

    def fit_cards(self,event=None):
        width=min(self.MAX_CARD_WIDTH,max(1,self.viewport.winfo_width()-2))
        # An explicit width avoids the grid's requested width expanding the
        # cards; height remains the natural two text lines at the current DPI.
        for widgets in self.widgets.values():
            widgets['row'].configure(width=width)
            widgets['row'].grid_propagate(False)
            widgets['row'].configure(height=max(26,widgets['name'].winfo_reqheight()+widgets['note'].winfo_reqheight()+2))
            self.row_step=max(1,widgets['row']['height']+2)
        self.viewport.itemconfigure(self.content_window,width=width)
        self.update_scroll_region()

    def update_scroll_region(self,event=None):
        total=self.content.winfo_reqheight()+2
        inset=2*(int(self.viewport['highlightthickness'])+int(self.viewport['borderwidth']))
        available=max(1,self.viewport.winfo_height()-inset)
        overflow=total>available
        # If it fits, use the visible height and reset any prior scroll offset.
        self.viewport.configure(scrollregion=(0,0,self.content.winfo_width()+2,
                                              total if overflow else available))
        if overflow!=self.scroll_visible:
            self.scroll_visible=overflow
            if overflow:self.scrollbar.pack(before=self.viewport,side='right',fill='y',padx=(6,0))
            else:self.scrollbar.pack_forget()
        if not overflow:self.viewport.yview_moveto(0)
        self.draw_scrollbar()

    def on_scroll(self,first,last):self.draw_scrollbar(float(first),float(last))

    def draw_scrollbar(self,first=None,last=None):
        self.scrollbar.delete('all')
        if first is None:first,last=self.viewport.yview()
        h=max(1,self.scrollbar.winfo_height());w=max(1,self.scrollbar.winfo_width())
        if h<4 or w<8:return
        self.scrollbar.create_line(w/2,3,w/2,h-3,fill='#584430',width=3)
        self.thumb_top=max(2,first*h);self.thumb_bottom=min(h-2,max(self.thumb_top+18,last*h))
        from hud_controls import rounded_rect
        rounded_rect(self.scrollbar,3,self.thumb_top,w-6,max(2,self.thumb_bottom-self.thumb_top),3,fill='#D5A65F',outline='')

    def scroll_to(self,fraction):
        if not self.scroll_visible:return 'break'
        self.hud.clear_tip();self.viewport.yview_moveto(max(0,min(1,fraction)));return 'break'

    def scroll_units(self,units,kind='units'):
        if not self.scroll_visible:return 'break'
        self.hud.clear_tip();self.viewport.yview_scroll(units*self.row_step if kind=='units' else units,kind);return 'break'

    def mousewheel(self,event):
        if not event.delta:return 'break'
        return self.scroll_units((-1 if event.delta>0 else 1)*max(1,abs(event.delta)//120))

    def begin_scroll(self,event):
        self.viewport.focus_set();self.hud.clear_tip()
        if self.thumb_top<=event.y<=self.thumb_bottom:self.scroll_drag=event.y-self.thumb_top
        else:self.scroll_to(event.y/max(1,self.scrollbar.winfo_height()));self.scroll_drag=0

    def drag_scroll(self,event):
        if self.scroll_drag is not None:self.scroll_to((event.y-self.scroll_drag)/max(1,self.scrollbar.winfo_height()))

    def draw_surface(self, canvas):
        canvas.delete('all')
        w,h=canvas.winfo_width(),canvas.winfo_height()
        canvas.surface_photo=ImageTk.PhotoImage(surface_raster(w,h,background_rgb(canvas),'#30251D',None,7),master=canvas)
        canvas.create_image(0,0,anchor='nw',image=canvas.surface_photo)

    def hint(self, key):
        return next(row['hint'] for row in self.rows if row['key'] == key)

    def set_state(self, snapshot):
        rows = activity_rows(snapshot)
        if rows == self.rows:
            return
        self.rows = rows
        for item in rows:
            widgets = self.widgets[item['key']]
            widgets['name'].configure(text=item['name'])
            widgets['count'].configure(text=item['count'], fg='#D5A65F' if item['complete'] else '#FFF5E8')
            widgets['note'].configure(text=item['note'], fg='#D5A65F' if item['complete'] else '#D6BEA2')
