# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit one-shot local request for a bounded diagnostic from packet one."""
import json
import time

REQUEST_FILE='diagnostico-inicial.json'


def consume_request(directory):
    """A fixed schema, no paths/commands, no recording on ordinary launches."""
    path=directory/REQUEST_FILE
    try:
        if path.is_symlink() or path.is_junction():return False
        info=path.stat()
        if info.st_size>1024 or not 0<=time.time()-info.st_mtime<=3600:return False
        raw=path.read_bytes()
        if len(raw)>1024:return False
        request=json.loads(raw)
        if not isinstance(request,dict) or type(request.get('schema')) is not int or type(request.get('seconds')) is not int:return False
        if request!={'schema':1,'record':'initial-identity','seconds':600}:return False
        path.unlink()  # Must be consumed before recording; a failed delete is not consent to repeat.
    except (OSError,ValueError,TypeError):return False
    return True
