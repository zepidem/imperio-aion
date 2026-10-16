# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit capture request from a Windows-started, unelevated HUD."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys

def needed():
    return os.name=='nt' and getattr(sys,'frozen',False) and not ctypes.windll.shell32.IsUserAnAdmin()

def prepare():
    from installation_guard import WindowsInspector,_check_object
    inspector=WindowsInspector()
    root=Path(inspector.program_files())/'ImperioAION'
    helper=root/'maintenance/imperio-install.exe'
    # Validate the actual protected native helper and its config, before
    # closing the unelevated HUD. Never elevate a path from AppData or input.
    for path in (Path(inspector.program_files()),root,helper.parent,helper,helper.with_suffix('.exe.config')):
        if not path.exists() or inspector.attributes(path)&0x400:raise OSError('Reinstale a versão atual para habilitar a captura pela inicialização do Windows.')
        _check_object(inspector,path)
    return helper

def launch(helper):
    unsafe=('COR_','CORECLR_','COMPLUS_','DOTNET_','PYTHON','TCL','TK_')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith(unsafe) and k.upper() not in ('DEVPATH','APPDOMAIN_MANAGER_ASM','APPDOMAIN_MANAGER_TYPE','PSMODULEPATH')}
    return subprocess.Popen([str(helper),'--launch-capture'],cwd=helper.parent,env=env,creationflags=0x08000000)
