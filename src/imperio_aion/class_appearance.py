# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only appearance hints; no persistence or combat attribution."""
from character_cache import identity_key,number

def display_classes(cache,bindings,rosters):
    """Cosmetic hints for current identities; never live DPS attribution.

    Disk contributes only name/server/class. Flow and actor must come from
    the current reader, or membership from its explicit current roster.
    """
    hints=[]
    for flow,binding in bindings.items():
        identity=dict(name=binding.get('character'),server=binding.get('server'))
        record=cache.records.get(identity_key(identity)) or {}
        if number(record.get('class_id'),64) and record['class_id']>0:
            hints.append(dict(flow=flow,**identity,actor=binding.get('actor'),
                class_id=record['class_id'],self_identity=True))
    for flow,roster in rosters.items():
        for member in roster.get('members',[]):
            identity=dict(name=member.get('name'),server=member.get('server'))
            record=cache.records.get(identity_key(identity)) or {}
            if number(record.get('class_id'),64) and record['class_id']>0:
                hints.append(dict(flow=flow,**identity,class_id=record['class_id'],self_identity=False))
    return hints

