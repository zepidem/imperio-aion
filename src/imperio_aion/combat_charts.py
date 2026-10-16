# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Charts of already attributed damage. Never infer damage from missing HP."""
from collections import defaultdict
import math
import tkinter as tk
from tkinter import ttk
from presentation import CLASSES

BG='#241B15';TEXT='#FFF5E8';MUTED='#D6BEA2';GOLD='#D5A65F'

def skill_colors(items):
    ordered=sorted(items,key=lambda s:(-(s.get('damage') or 0),str(s.get('code'))))
    top=(241,132,120);low=(141,204,154);count=max(1,len(ordered)-1)
    return {str(s['code']):'#'+''.join(f'{round(a+(b-a)*i/count):02x}' for a,b in zip(top,low)) for i,s in enumerate(ordered)}

def row_key(row):return str(row.get('observation_key') or row.get('dbid') or row.get('name'))

def chart_series(snapshot,fight,rows,character,catalog):
    if not fight:return [],[],False
    if fight.get('observation_only'):return [],[],False
    group=str(fight['key']).startswith('grupo/')
    source=snapshot.get('group_events',[]) if group else snapshot.get('events',[])
    # The encounter key already separates TCP flows, roster revisions and lives.
    events=[e for e in source if e.get('encounter_key')==fight['key']]
    seconds=max(0,fight.get('duration_us',0)/1e6);start=fight['start_us']
    players=[];skills=[];partial=False
    for row in rows:
        selected=[e for e in events if (e.get('actor')==row['actor'] if row.get('actor') is not None else e.get('actor_name')==row['name'] if group else row['name']==character)]
        hits=[];by_skill=defaultdict(list)
        for e in selected:
            amount=e.get('candidate_amount');clock=e.get('clock_us')
            if not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<0 or not isinstance(clock,int):continue
            # Valid trailing impacts belong to this fight and land at its endpoint.
            point=(max(0,min(seconds,(clock-start)/1e6)),amount)
            hits.append(point);by_skill[str(e.get('raw_skill'))].append(point)
        hits.sort();known=row.get('damage')
        incomplete=known is not None and abs(sum(v for _,v in hits)-known)>.001
        partial|=incomplete
        players.append(dict(key=row_key(row),name=row['name'],color=CLASSES.get(row.get('class_key'),CLASSES['unknown'])[1],hits=hits,duration=seconds,partial=incomplete))
        for item in row.get('skills',[]):
            code=str(item['code']);values=sorted(by_skill[code])
            skills.append(dict(key=row_key(row)+'/'+code,member=row_key(row),code=code,name=catalog.name(code,item.get('name','')),hits=values,duration=seconds,damage=item.get('damage',0),partial=abs(sum(v for _,v in values)-item.get('damage',0))>.001))
    return players,skills,partial

def points_for(series,mode):
    duration=series['duration'];hits=series['hits']
    if not hits:return []
    if mode=='total':
        points=[(0,0)];total=0
        for t,value in hits:points.extend(((t,total),(t,total+value)));total+=value
        points.append((duration,total));return points
    # One-second windows start at first attributed damage. The last window can
    # be shorter and its rate uses its actual observed duration, not one second.
    if duration<=0:return []
    buckets=defaultdict(float)
    count=max(1,math.ceil(duration))
    for t,value in hits:buckets[min(int(t),count-1)]+=value
    points=[]
    for index in range(count):
        left=index;right=min(duration,index+1);rate=buckets[index]/(right-left)
        points.extend(((left,rate),(right,rate)))
    return points

def blended(color,alpha):
    return '#'+''.join(f'{round(int(color[i:i+2],16)*alpha+int(BG[i:i+2],16)*(1-alpha)):02x}' for i in (1,3,5))

def compact(value):
    if value>=1_000_000:return f'{value/1e6:.1f}M'.replace('.',',')
    if value>=1000:return f'{value/1000:.1f}K'.replace('.',',')
    return str(round(value))

class DamageChart(tk.Frame):
    def __init__(self,parent,title,on_select=None,columns=3):
        super().__init__(parent,bg=BG);self.series=[];self.selected=None;self.mode='total';self.signature=None;self.on_select=on_select;self.columns=columns
        head=tk.Frame(self,bg=BG);head.pack(fill='x',pady=(0,3))
        self.title=tk.Label(head,text=title,bg=BG,fg=TEXT,font=('Segoe UI',10,'bold'),anchor='w')
        self.heading=title
        head.bind('<Configure>',lambda e:self.title.configure(text=self.heading if e.width>=430 else 'Dano'))
        modes=tk.Frame(head,bg=BG);modes.pack(side='right')
        self.title.pack(side='left',fill='x',expand=True)
        self.mode_buttons={}
        for key,label in (('total','Dano acumulado'),('second','Dano por segundo')):
            from panel_components import PanelButton
            b=PanelButton(modes,label,lambda k=key:self.set_mode(k),width=126 if key=='total' else 136,height=28);b.pack(side='left',padx=2);self.mode_buttons[key]=b
        self.caption=tk.StringVar(value='Aguardando registros do combate.')
        tk.Label(self,textvariable=self.caption,bg=BG,fg=MUTED,font=('Segoe UI',8),anchor='w',wraplength=800).pack(fill='x')
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0,height=134,cursor='crosshair');self.canvas.pack(fill='x')
        self.canvas.bind('<Configure>',lambda _:self.draw());self.canvas.bind('<ButtonRelease-1>',self.click)
        self.canvas.bind('<Motion>',self.hover);self.canvas.bind('<Leave>',lambda _:self.canvas.delete('hover'))
        self._update_modes()

    def set_mode(self,mode):self.mode=mode;self._update_modes();self.draw()
    def _update_modes(self):
        for k,b in self.mode_buttons.items():b.configure(selected=k==self.mode)
    def select(self,key):
        self.selected=None if self.selected==key else key
        self.draw()
        if self.on_select:self.on_select(self.selected)
    def set_series(self,series,scope):
        signature=(scope,tuple((s['key'],s['name'],s['color'],len(s['hits']),s['duration'],sum(v for _,v in s['hits'])) for s in series))
        if signature==self.signature:return
        if self.signature and scope!=self.signature[0]:self.selected=None
        self.signature=signature;self.series=series
        if self.selected not in {s['key'] for s in series}:self.selected=None
        self.draw()

    def draw(self):
        c=self.canvas;c.delete('all');width=max(c.winfo_width(),200);height=max(c.winfo_height(),72)
        left,right,top,bottom=52,width-12,21,height-30
        self.plot=(left,right,top,bottom)
        nonempty=[s for s in self.series if s['hits']]
        if not nonempty:
            if hasattr(self,'duration'):del self.duration
            self.caption.set('Aguardando registros de dano para este combate.')
            c.create_text(width/2,height/2,text='O gráfico aparece ao receber dano atribuído ao alvo.',fill=MUTED,font=('Segoe UI',8));return
        maximum=max(max(v for _,v in points_for(s,self.mode)) if points_for(s,self.mode) else 0 for s in nonempty)
        maximum=max(1,maximum);step=10**math.floor(math.log10(maximum/5));maximum=math.ceil(maximum/5/step)*step*5
        duration=max(s['duration'] for s in self.series);span=max(duration,.001)
        self.duration=duration;self.maximum=maximum
        divisions=3 if height<120 else 5
        for n in range(divisions+1):
            value=maximum*n/divisions;y=bottom-(bottom-top)*n/divisions
            c.create_line(left,y,right,y,fill='#513C29',dash=(2,5));c.create_text(left-10,y,text=compact(value),anchor='e',fill=MUTED,font=('Segoe UI',8))
        c.create_text(left,8,text='Dano/s' if self.mode=='second' else 'Dano',anchor='w',fill=MUTED,font=('Segoe UI',8))
        for n in range(6):
            t=duration*n/5;x=left+(right-left)*n/5
            label=f'{int(t)//60}m {int(t)%60:02d}s' if t>=60 else f'{t:.0f}s'
            c.create_text(x,bottom+17,text=label,anchor='w' if n==0 else 'e' if n==5 else 'center',fill=MUTED,font=('Segoe UI',8))
        c.create_text((left+right)/2,height-5,text='Tempo de combate',fill=MUTED,font=('Segoe UI',8))
        ordered=sorted(self.series,key=lambda s:s['key']==self.selected)
        for s in ordered:
            pts=points_for(s,self.mode)
            # Keep vertical peaks while bounding drawing cost at pixel density.
            if len(pts)>max(1000,int(right-left)*4):
                columns=defaultdict(list)
                for t,v in pts:columns[int(t/span*(right-left))].append((t,v))
                pts=[]
                for values in columns.values():
                    chosen={0,len(values)-1,min(range(len(values)),key=lambda i:values[i][1]),max(range(len(values)),key=lambda i:values[i][1])}
                    pts.extend(values[i] for i in sorted(chosen))
            coords=[coordinate for t,v in pts for coordinate in (left+t/span*(right-left),bottom-v/maximum*(bottom-top))]
            if len(coords)>=4:c.create_line(*coords,fill=blended(s['color'],1 if self.selected is None or s['key']==self.selected else .25),width=2.8 if s['key']==self.selected else 2,tags=('curve',s['key']))
        self.caption.set('Gráfico parcial · histórico de eventos incompleto.' if any(s.get('partial') for s in self.series) else '')

    def click(self,event):
        for item in reversed(self.canvas.find_overlapping(event.x-5,event.y-5,event.x+5,event.y+5)):
            tags=self.canvas.gettags(item)
            if 'curve' in tags:self.select(tags[1]);return
    def hover(self,event):
        c=self.canvas;c.delete('hover')
        if not hasattr(self,'duration'):return
        left,right,top,bottom=self.plot
        if not left<=event.x<=right or not top<=event.y<=bottom:return
        t=(event.x-left)/(right-left)*self.duration
        c.create_line(event.x,top,event.x,bottom,fill=GOLD,dash=(3,3),tags='hover')
        lines=[f'{int(t)//60}m {t%60:04.1f}s'+(' · dano/s' if self.mode=='second' else ' · acumulado')]
        ordered=sorted(self.series,key=lambda s:s['key']!=self.selected)
        for s in ordered[:10]:
            if self.mode=='total':value=sum(v for at,v in s['hits'] if at<=t)
            else:
                left_t=min(int(t),max(0,math.ceil(s['duration'])-1));right_t=min(s['duration'],left_t+1)
                value=sum(v for at,v in s['hits'] if left_t<=at<right_t or at==right_t==s['duration'])/max(.001,right_t-left_t)
            lines.append(s['name']+'  '+compact(value))
        label=c.create_text(right-9,top+8,text='\n'.join(lines),anchor='ne',fill=TEXT,font=('Segoe UI',8),tags='hover')
        x1,y1,x2,y2=c.bbox(label);box=c.create_rectangle(x1-9,y1-6,x2+9,y2+6,fill='#30251D',outline='#9c713c',tags='hover');c.tag_lower(box,label)
