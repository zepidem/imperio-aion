# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Exact NPC cast profile and offline client-authored skill ownership facts.

No names, HP thresholds, shared targets or damage frequency infer a boss.
Ownership lists include ordinary NPCs and bosses, not just a positive whitelist.
"""
from functools import lru_cache
from pathlib import Path
import json,math,struct
from inspect_wire import varint

@lru_cache(maxsize=1)
def skill_owners():
    try:
        data=json.loads((Path(__file__).resolve().parent/'dados/npc-skill-owners.json').read_text(encoding='utf8'))
        if data['schema']!=1 or not 1<=data['npc_count']<=30000:raise ValueError('Unknown NPC ownership schema')
        result={}
        if not 1<=len(data['owners'])<=20000:raise ValueError('Invalid ownership count')
        for key,values in data['owners'].items():
            code=int(key)
            if str(code)!=key or not 0<code<300000000 or not isinstance(values,list) or not 1<=len(values)<=30000:
                raise ValueError('Invalid skill ownership')
            if any(type(v) is not int or not 0<v<10000000 for v in values) or values!=sorted(set(values)):
                raise ValueError('Invalid NPC owner list')
            result[code]=tuple(values)
        return result
    except (OSError,ValueError,KeyError,TypeError):return {}


def npc_cast(body):
    """Observed 0238 actor/skill header with an exact pose/timing tail.

    A parsed cast alone is not NPC-role proof; a separate 008d kind=2
    observation is mandatory before this can classify a target.
    """
    if body[:2]!=b'\x02\x38' or not 29<=len(body)<=64:raise ValueError('Unknown NPC cast size')
    actor,p=varint(body,2);flag,p=varint(body,p)
    if p+6>len(body):raise ValueError('Short cast header')
    skill=int.from_bytes(body[p:p+4],'little');kind=body[p+5];p+=6
    target,p=varint(body,p)
    if flag!=0 or kind!=2 or not 0<actor<10000000 or not 0<target<10000000 or not 0<skill<300000000:
        raise ValueError('Unknown cast header')
    if p+16>len(body):raise ValueError('Short pose')
    pose=struct.unpack_from('<ffff',body,p);p+=16
    if not all(math.isfinite(n) for n in pose) or abs(pose[0])>360 or any(abs(n)>1000000 for n in pose[1:]):
        raise ValueError('Invalid cast pose')
    scale,p=varint(body,p);kind,p=varint(body,p);cooldown,p=varint(body,p)
    if scale!=10000 or kind!=1 or cooldown>3600000 or p!=len(body):raise ValueError('Unknown cast tail')
    return actor,skill
