# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Validated shortcut preferences, independent of Tk and Windows registration."""
from collections import OrderedDict

ACTIONS=OrderedDict([
    ('hud_adjustment','Desbloquear o HUD'),
    ('hud_collapse','Minimizar/Maximizar HUD'),
    ('hud_visibility','Ocultar/Desocultar HUD'),
    ('panel','Abrir Painel de Combate'),
    ('reset_combat','Reiniciar combate')])
DEFAULTS=dict(zip(ACTIONS,('Ctrl+Alt+M',"Ctrl+Alt+'",'Ctrl+Alt+H','Ctrl+Alt+I','Ctrl+Alt+R')))
NAMED={'Space':'Espaço','Return':'Enter','Prior':'PageUp','Next':'PageDown','BackSpace':'Backspace',
       'apostrophe':"'",'quotedbl':"'",'grave':'`','asciitilde':'`','semicolon':';','colon':';',
       'minus':'-','underscore':'-','equal':'=','plus':'Plus','comma':',','less':',','period':'.','greater':'.',
       'slash':'/','question':'/','backslash':'\\','bar':'\\','bracketleft':'[','braceleft':'[','bracketright':']','braceright':']'}
KEYS={'Enter':0x0D,'Espaço':0x20,'Tab':0x09,'Backspace':0x08,'Insert':0x2D,'Delete':0x2E,
      'Home':0x24,'End':0x23,'PageUp':0x21,'PageDown':0x22,'Left':0x25,'Up':0x26,'Right':0x27,'Down':0x28}
MODS={'Ctrl':2,'Alt':1,'Shift':4}

def normalize(value):
    if not isinstance(value,str) or len(value)>50:raise ValueError('Combinação inválida.')
    parts=value.split('+');mods=parts[:-1];key=parts[-1]
    if not key or any(m not in MODS for m in mods) or len(set(mods))!=len(mods):raise ValueError('Use Ctrl, Alt ou Shift e uma tecla.')
    key=key.upper() if len(key)==1 and key.isascii() and key.isalnum() else key
    function=key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=24
    valid=key in KEYS or function or key=='Plus' or len(key)==1 and key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'`;-=,./\\[]"
    if not valid or not mods and not function:raise ValueError('Escolha uma combinação com Ctrl, Alt ou Shift, ou F1–F24. ESC não é um atalho.')
    return '+'.join([m for m in MODS if m in mods]+[key])

def validate(bindings):
    if not isinstance(bindings,dict) or set(bindings)!=set(ACTIONS):raise ValueError('A lista precisa conter os cinco atalhos.')
    result={action:normalize(bindings[action]) for action in ACTIONS}
    if len(set(result.values()))!=len(result):raise ValueError('Há atalhos repetidos. Escolha uma combinação para cada ação.')
    return result

def load_preferences(value):
    try:return validate(value)
    except ValueError:return dict(DEFAULTS)

def from_tk(event):
    key=getattr(event,'keysym','')
    if key in ('Escape','Control_L','Control_R','Alt_L','Alt_R','Shift_L','Shift_R','Meta_L','Meta_R'):return None
    key=NAMED.get(key,key)
    state=getattr(event,'state',0)
    mods=[name for name,flag in (('Ctrl',0x4),('Alt',0x20008),('Shift',0x1)) if state&flag]
    return normalize('+'.join(mods+[key]))

def resolve(value,scan):
    """Return RegisterHotKey modifiers/VK in the active keyboard layout."""
    parts=normalize(value).split('+');key=parts[-1];mod=sum(MODS[m] for m in parts[:-1])
    if key in KEYS:vk=KEYS[key]
    elif key.startswith('F') and key[1:].isdigit():vk=0x70+int(key[1:])-1
    elif len(key)==1 and key.isalnum():vk=ord(key)
    else:
        found=scan('+' if key=='Plus' else key)
        if found==-1:raise ValueError('Tecla indisponível no layout atual do Windows: '+key)
        vk=found&255;required=(found>>8)&7
        mod|=(4 if required&1 else 0)|(2 if required&2 else 0)|(1 if required&4 else 0)
    return mod|0x4000,vk
