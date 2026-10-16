# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Compact native gain readout using the user's supplied transparent artwork."""
import tkinter as tk
from resource_gains import PERIODS,gain_presentation


class ResourceStrip(tk.Frame):
    def __init__(self,parent,hud):
        super().__init__(parent,bg='#241B15')
        self.hud=hud;self.labels={};self.hints={};self.icons={}
        for key in PERIODS:
            group=tk.Frame(self,bg='#241B15');group.pack(side='left',padx=(0,12))
            icon=tk.Label(group,image=hud.assets.get(key,20),bg='#241B15',
                borderwidth=0,highlightthickness=0,padx=0,pady=0,takefocus=0)
            icon.pack(side='left',padx=(0,4));self.icons[key]=icon
            label=tk.Label(group,text='',font=('Segoe UI',9,'bold'),bg='#241B15',fg='#FFF5E8',
                padx=0,pady=0,anchor='w',borderwidth=0,takefocus=1)
            label.pack(side='left');self.labels[key]=label
            for widget in (group,icon,label):
                for event in ('<Enter>','<FocusIn>'):
                    widget.bind(event,lambda e,k=key,w=widget:hud.control_hint(w,self.hints[k]),add='+')
                for event in ('<Leave>','<FocusOut>'):widget.bind(event,hud.clear_tip,add='+')
        self.set_state({})

    def set_state(self,state):
        state=state if isinstance(state,dict) else {}
        for key,label in self.labels.items():
            view=gain_presentation(key,state.get(key))
            label.configure(text=view['text'],fg='#D5A65F' if view['at_limit'] else '#FFF5E8' if view['available'] else '#D6BEA2')
            self.hints[key]=view['hint']
