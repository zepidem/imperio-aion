# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Current-user integration after authenticated installation; no elevation.

Startup is opt-in. The installed launcher always authenticates before running
the HUD. Windows startup opens the HUD without UAC; capture asks separately.
"""
from pathlib import Path
import json
import os
import subprocess
import ctypes as ct
import tempfile

DEFAULTS=dict(start_windows=False,start_menu=True)
RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE='ImperioAION'

def load(data):
    try:
        p=Path(data)/'install-preferences.json'
        from verify_core import _plain_ancestors
        _plain_ancestors(p)
        if p.stat().st_size>4096:return dict(DEFAULTS)
        result=json.loads(p.read_text(encoding='utf8'))
        if set(result)!=set(DEFAULTS) or any(type(v) is not bool for v in result.values()):return dict(DEFAULTS)
        return result
    except (OSError,ValueError):return dict(DEFAULTS)

def command(target,*,local_channel=None):
    target=Path(target)
    if not target.is_absolute() or target.name!='imperioaion-update.exe':raise ValueError('Iniciador instalado inválido.')
    args=[str(target),'--windows-startup']
    if local_channel is not None:args.extend(['--local-channel',str(Path(local_channel).resolve())])
    text=subprocess.list2cmdline(args)
    # Microsoft's Run key limit includes the command and all arguments.
    if len(text)>260:raise ValueError('O caminho do teste é longo demais para iniciar com o Windows. Extraia o teste em uma pasta mais curta.')
    return text

def menu_folder():
    from desktop_shortcut import GUID,_succeeded
    shell=ct.WinDLL('shell32',winmode=0x800);ole=ct.OleDLL('ole32')
    folder=GUID.parse('a77f5d77-2e2b-44c3-a6a2-aba601054a51');value=ct.c_void_p()
    shell.SHGetKnownFolderPath.argtypes=[ct.POINTER(GUID),ct.c_uint32,ct.c_void_p,ct.POINTER(ct.c_void_p)]
    shell.SHGetKnownFolderPath.restype=ct.c_long;ole.CoTaskMemFree.argtypes=[ct.c_void_p]
    _succeeded(shell.SHGetKnownFolderPath(ct.byref(folder),0,None,ct.byref(value)))
    try:return Path(ct.wstring_at(value.value))
    finally:ole.CoTaskMemFree(value)

def apply(data,target,choices,*,local_channel=None,registry=None,menu=None,shortcut=None):
    from desktop_shortcut import create_shortcut
    from verify_core import _plain_ancestors
    if set(choices)!=set(DEFAULTS) or any(type(v) is not bool for v in choices.values()):raise ValueError('Opções de instalação inválidas.')
    if ct.windll.shell32.IsUserAnAdmin():raise OSError('As opções devem ser salvas pela sua conta, sem elevação.')
    target=Path(target)
    if not target.is_absolute() or target.name!='imperioaion-update.exe':raise ValueError('Iniciador instalado inválido.')
    _plain_ancestors(target)
    if not target.is_file():raise ValueError('Iniciador instalado ausente.')
    startup_command=command(target,local_channel=local_channel) if choices['start_windows'] else None
    if registry is None:
        import winreg
        registry=winreg
    menu=Path(menu) if menu is not None else menu_folder()
    _plain_ancestors(menu)
    shortcut=shortcut or create_shortcut
    label='Império AION · teste' if local_channel is not None else 'Império AION'
    if choices['start_menu']:
        shortcut(target,local_channel=local_channel,desktop=menu,label=label)
    else:
        # This reserved application shortcut is in the current user's menu.
        # Do not follow a reparse point if a file was replaced after install.
        p=menu/(label+'.lnk')
        if p.exists():_plain_ancestors(p);p.unlink()
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,RUN_KEY,0,registry.KEY_SET_VALUE) as key:
        if startup_command:registry.SetValueEx(key,VALUE,0,registry.REG_SZ,startup_command)
        else:
            try:registry.DeleteValue(key,VALUE)
            except FileNotFoundError:pass
    data=Path(data);data.mkdir(parents=True,exist_ok=True);_plain_ancestors(data)
    p=data/'install-preferences.json'
    if p.exists():_plain_ancestors(p)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=data,delete=False) as f:
        temporary=Path(f.name);json.dump(choices,f);f.flush();os.fsync(f.fileno())
    try:os.replace(temporary,p)
    finally:
        if temporary.exists():temporary.unlink()
    return dict(choices)
