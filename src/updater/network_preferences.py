# SPDX-License-Identifier: GPL-3.0-only
"""Explicit opt-in to automatic update checks; missing/corrupt state is offline."""
import json,os,tempfile
from pathlib import Path
from verify_core import _plain_ancestors

def load(data):
    p=Path(data)/'network-preferences.json'
    try:
        _plain_ancestors(p)
        if p.stat().st_size>1024:return False
        value=json.loads(p.read_text(encoding='utf8'))
        return value=={'automatic_update_checks':True} and type(value.get('automatic_update_checks')) is bool
    except (OSError,ValueError):return False

def save(data,enabled):
    if type(enabled) is not bool:raise ValueError('Use a boolean preference.')
    data=Path(os.path.abspath(data))
    ancestor=data
    while not ancestor.exists():ancestor=ancestor.parent
    _plain_ancestors(ancestor)
    data.mkdir(parents=True,exist_ok=True);_plain_ancestors(data)
    p=data/'network-preferences.json'
    if p.exists():_plain_ancestors(p)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=data,delete=False) as f:
            temporary=Path(f.name);json.dump({'automatic_update_checks':enabled},f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,p)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)

def start_service(service,automatic_checks):
    """Local authentication always precedes an optional network check."""
    if type(automatic_checks) is not bool:raise ValueError('Use a boolean preference.')
    service.detect()
    if automatic_checks:service.check_updates()
    else:service.prepare_offline()
