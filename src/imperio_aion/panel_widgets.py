# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Rounded native panel controls. Data interpretation stays outside widgets."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from PIL import ImageTk
from hud_controls import surface_raster,background_rgb,rounded_rect
BG='#241B15';SURFACE='#30251D';TEXT='#FFF5E8';MUTED='#D6BEA2';GOLD='#D5A65F';LINE='#67492F'

class GoldScrollbar(tk.Canvas):
    def __init__(self,parent,command=None,autohide=False):
        super().__init__(parent,width=9,bg=BG,highlightthickness=0,takefocus=False)
        self.command=command;self.first=0.;self.last=1.;self.drag=None;self.autohide=autohide
        self.bind('<Configure>',lambda _:self.draw());self.bind('<Button-1>',self.press);self.bind('<B1-Motion>',self.motion)
    def set(self,first,last):
        self.first,self.last=float(first),float(last)
        if self.autohide:
            if self.last-self.first>=.999:
                if self.winfo_manager():self.pack_forget()
            elif not self.winfo_manager():self.pack(side='right',fill='y')
        self.draw()
    def draw(self):
        self.delete('all');h=max(1,self.winfo_height());top=self.first*h;end=max(top+20,self.last*h)
        rounded_rect(self,1,1,7,max(1,h-2),3,fill='#2B2019',outline='')
        if self.last-self.first<.999:rounded_rect(self,2,top,5,min(h-top,end-top),2,fill='#A17B45',outline='')
    def press(self,e):
        self.drag=(e.y,self.first)
        if self.command and not self.first*self.winfo_height()<=e.y<=self.last*self.winfo_height():self.command('moveto',max(0,min(1,e.y/max(1,self.winfo_height()))))
    def motion(self,e):
        if self.command and self.drag:self.command('moveto',max(0,min(1,self.drag[1]+(e.y-self.drag[0])/max(1,self.winfo_height()))))

class RoundedField(tk.Frame):
    def __init__(self,parent,variable,choice=False,width=180,height=34,values=(),readonly=False):
        super().__init__(parent,bg=parent.cget('bg'),width=width,height=height)
        self.pack_propagate(False);self.grid_propagate(False)
        self.back=tk.Canvas(self,bg=self.cget('bg'),highlightthickness=0);self.back.place(relwidth=1,relheight=1)
        self.photo=None
        self.inner=(ttk.Combobox(self,textvariable=variable,values=values,state='readonly',style='Rounded.TCombobox',font=('Segoe UI',9)) if choice else tk.Entry(self,textvariable=variable,bg=SURFACE,fg=TEXT,insertbackground=GOLD,readonlybackground=SURFACE,disabledbackground=SURFACE,disabledforeground=MUTED,relief='flat',borderwidth=0,font=('Segoe UI',9)))
        if readonly and not choice:self.inner.configure(state='readonly')
        self.inner.place(x=8,y=5,relwidth=1,width=-16,relheight=1,height=-10)
        self.back.bind('<Configure>',self.redraw)
    def redraw(self,event=None):
        self.photo=ImageTk.PhotoImage(surface_raster(max(2,self.winfo_width()),max(2,self.winfo_height()),background_rgb(self),SURFACE,LINE,radius=8),master=self)
        self.back.delete('all');self.back.create_image(0,0,anchor='nw',image=self.photo)
    def configure(self,cnf=None,**kwargs):
        options=dict(cnf or {},**kwargs)
        forwarded={k:options.pop(k) for k in tuple(options) if k in ('state','values')}
        if forwarded and hasattr(self,'inner'):self.inner.configure(**forwarded)
        return super().configure(**options) if options else None
    config=configure
    def __setitem__(self,key,value):self.configure(**{key:value})
    def bind(self,sequence=None,func=None,add=None):
        if hasattr(self,'inner') and sequence not in ('<Configure>','<Destroy>'):return self.inner.bind(sequence,func,add)
        return super().bind(sequence,func,add)

class RoundedTable(tk.Canvas):
    """Treeview-compatible public subset, drawn without square platform borders."""
    def __init__(self,parent,columns=(),show='headings',height=250,ratios=None,rowheight=24):
        super().__init__(parent,bg=BG,highlightthickness=0,height=height,takefocus=True)
        self.columns=tuple(columns);self.show_tree='tree' in show;self.order=[];self.rows={};self.tags={};self.selected=[]
        self.headings={};self.column_options={};self.ratios=ratios;self.rowheight=rowheight;self.top=0;self.headerheight=28;self.scrollcommand=None;self.pending=None
        self.face=tkfont.Font(self,family='Segoe UI',size=8);self.bold=tkfont.Font(self,family='Segoe UI',size=8,weight='bold');self.photo=None
        self.bind('<Configure>',lambda _:self.redraw());self.bind('<Button-1>',self.press)
        self.bind('<MouseWheel>',lambda e:self.yview('scroll',-1 if e.delta>0 else 1,'units'))
        for key,delta in (('<Up>',-1),('<Down>',1)):self.bind(key,lambda e,d=delta:self.arrow(d))
    def configure(self,cnf=None,**kwargs):
        options=dict(cnf or {},**kwargs)
        if 'yscrollcommand' in options:self.scrollcommand=options.pop('yscrollcommand')
        options.pop('style',None);options.pop('selectmode',None)
        if 'height' in options and isinstance(options['height'],int) and options['height']<30:options['height']=options['height']*self.rowheight+self.headerheight
        result=super().configure(**options) if options else None
        if hasattr(self,'rows'):self.redraw()
        return result
    config=configure
    def heading(self,key,**kw):self.headings.setdefault(key,{}).update(kw);self.redraw()
    def column(self,key,**kw):self.column_options.setdefault(key,{}).update(kw);self.redraw()
    def tag_configure(self,key,**kw):self.tags.setdefault(str(key),{}).update(kw);self.redraw()
    def insert(self,parent,index,iid=None,**kw):
        key=str(iid if iid is not None else len(self.rows));self.rows[key]=dict(kw)
        self.order.insert(len(self.order) if index=='end' else int(index),key);self.redraw();return key
    def exists(self,key):return str(key) in self.rows
    def get_children(self,parent=''):return tuple(self.order)
    def item(self,key,option=None,**kw):
        row=self.rows[str(key)]
        if kw:row.update(kw);self.redraw()
        return row.get(option) if option else dict(row)
    def delete(self,*keys):
        for key in keys:
            key=str(key);self.rows.pop(key,None)
            if key in self.order:self.order.remove(key)
            if key in self.selected:self.selected.remove(key)
        self.redraw()
    def move(self,key,parent,index):
        key=str(key)
        if key in self.order:self.order.remove(key);self.order.insert(len(self.order) if index=='end' else int(index),key);self.redraw()
    def selection(self):return tuple(self.selected)
    def selection_set(self,*keys):
        if len(keys)==1 and isinstance(keys[0],(tuple,list)):keys=keys[0]
        self.selected=[str(k) for k in keys if str(k) in self.rows];self.redraw()
    def identify_row(self,y):
        i=self.top+int((y-self.headerheight)//self.rowheight)
        return self.order[i] if y>=self.headerheight and 0<=i<len(self.order) else ''
    def capacity(self):return max(1,(max(self.winfo_height(),self.headerheight+self.rowheight)-self.headerheight-2)//self.rowheight)
    def yview(self,*args):
        count=len(self.order);cap=self.capacity()
        if not args:return (self.top/max(1,count),min(1,(self.top+cap)/max(1,count)))
        if args[0]=='moveto':self.top=round(float(args[1])*count)
        elif args[0]=='scroll':self.top+=int(args[1])*(cap if args[2]=='pages' else 1)
        self.top=max(0,min(self.top,max(0,count-cap)));self.redraw()
    def see(self,key):
        if str(key) not in self.order:return
        index=self.order.index(str(key));cap=self.capacity()
        if index<self.top:self.top=index
        elif index>=self.top+cap:self.top=index-cap+1
        self.redraw()
    def press(self,e):
        self.focus_set()
        if e.y<self.headerheight:
            for key,left,width in self.layout_columns():
                if left<=e.x<left+width:
                    command=self.headings.get(key,{}).get('command')
                    if command:command()
                    break
            return
        key=self.identify_row(e.y)
        if key:self.selection_set(key);self.event_generate('<<TreeviewSelect>>')
    def arrow(self,delta):
        if not self.order:return 'break'
        old=self.order.index(self.selected[0]) if self.selected and self.selected[0] in self.order else 0
        key=self.order[max(0,min(len(self.order)-1,old+delta))];self.selection_set(key);self.see(key);self.event_generate('<<TreeviewSelect>>');return 'break'
    def layout_columns(self):
        keys=(('#0',) if self.show_tree else ())+self.columns;w=max(2,self.winfo_width())-2
        weights=self.ratios or [self.column_options.get(k,{}).get('width',100) for k in keys]
        total=sum(weights) or 1;result=[];x=1
        for key,weight in zip(keys,weights):width=w*weight/total;result.append((key,x,width));x+=width
        return result
    def redraw(self):
        if self.pending is None and self.winfo_exists():self.pending=self.after_idle(self.draw)
    def fit_text(self,text,width,font=None):
        text=str(text);font=font or self.face
        if font.measure(text)<=width:return text
        while text and font.measure(text+'…')>max(0,width):text=text[:-1]
        return text+'…' if text else ''
    def draw(self):
        self.pending=None
        if not self.winfo_exists():return
        # delete() is the table's row API; clear only Canvas items here.
        tk.Canvas.delete(self,'all');w=max(2,self.winfo_width());h=max(2,self.winfo_height());cap=self.capacity()
        self.top=min(self.top,max(0,len(self.order)-cap));layout=self.layout_columns()
        self.photo=ImageTk.PhotoImage(surface_raster(w,h,background_rgb(self),'#2B2019',LINE,radius=9),master=self);self.create_image(0,0,anchor='nw',image=self.photo)
        rounded_rect(self,1,1,w-2,27,8,fill='#38291E',outline='');self.create_rectangle(1,15,w-1,28,fill='#38291E',outline='')
        for key,x,width in layout:
            opt=self.headings.get(key,{});right=opt.get('anchor')=='e'
            self.create_text(x+width-7 if right else x+6,14,text=self.fit_text(opt.get('text',''),width-12,self.bold),font=self.bold,fill='#E2C8A2',anchor='e' if right else 'w')
        for j,key in enumerate(self.order[self.top:self.top+cap]):
            row=self.rows[key];y=self.headerheight+j*self.rowheight;tags=row.get('tags',());tags=(tags,) if isinstance(tags,str) else tags
            color=next((self.tags[t]['foreground'] for t in tags if t in self.tags and 'foreground' in self.tags[t]),TEXT)
            if key in self.selected:self.create_rectangle(1,y,w-1,y+self.rowheight,fill='#493225',outline='');self.create_line(1,y,1,y+self.rowheight,fill=GOLD)
            self.create_line(1,y,w-1,y,fill='#493525')
            values=dict(zip(self.columns,row.get('values',())))
            for column,x,width in layout:
                tree=column=='#0';text=row.get('text','') if tree else values.get(column,'');image=row.get('image') if tree else None
                right=self.column_options.get(column,{}).get('anchor')=='e';padding=7
                if image:
                    self.create_image(x+6,y+self.rowheight/2,image=image,anchor='w');padding=40 if text else 32
                self.create_text(x+width-7 if right else x+padding,y+self.rowheight/2,text=self.fit_text(text,width-padding-7),font=self.face,fill=color if tree or column=='name' else TEXT,anchor='e' if right else 'w')
        if self.scrollcommand:self.scrollcommand(*self.yview())
