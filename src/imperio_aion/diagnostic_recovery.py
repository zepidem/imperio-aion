# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit recovery of historical Latesran facts; never restore live actors."""
import json
import time
from datetime import datetime
from pathlib import Path
from character_cache import CharacterCache, identity_key
from weekly_orders import WeeklyOrders, valid_order


def recover_latesran(path, cache=None, now=None):
    now=time.time() if now is None else now
    path=Path(path)
    if path.stat().st_size>20*1024*1024:raise ValueError('Diagnóstico maior que 20 MB.')
    try:
        report=json.loads(path.read_text(encoding='utf-8-sig'))
        if report['kind']!='collection-diagnostic':raise ValueError()
        capture=report['capture_state'];saved=report['character_cache']
        identity=saved['selected']
        if (not identity_key(identity) or not saved['identity_confirmed']
                or report['character']!=identity['name'] or capture['mode']!='live'
                or not capture['running'] or capture.get('error')):raise ValueError()
        bindings=report['bindings']
        if (len(bindings)!=1 or bindings[0]['character']!=identity['name']
                or bindings[0]['server']!=identity['server']
                or type(bindings[0]['actor']) is not int or bindings[0]['actor']<=0):raise ValueError()
        directions=[d for d in capture['directions'] if d.get('accepted') and not d.get('failed')
                    and d.get('incoming_game_socket') and type(d.get('process_id')) is int and d['process_id']>0]
        if len(directions)!=1:raise ValueError()
        flow=directions[0]['flow']
        messages=report['messages']
        if not isinstance(messages,list) or not 1<=len(messages)<=20000:raise ValueError()
        start=datetime.fromisoformat(report['started_utc'])
        end=datetime.fromisoformat(report['finished_utc'])
        if start.tzinfo is None or end.tzinfo is None:raise ValueError()
        start,end=start.timestamp(),end.timestamp()
        if not start<=end<=now+60:raise ValueError()
        orders=WeeklyOrders();previous=-1
        for m in messages:
            # The report's binding list does not contain flow keys. Limit this
            # importer to a single incoming flow with no character transitions.
            if m['flow']!=flow or type(m['clock_us']) is not int or m['clock_us']<previous:raise ValueError()
            previous=m['clock_us']
            op=m['opcode']
            if op in ('0b61','3336'):raise ValueError()
            if op not in ('8456','0f8d'):continue
            if m.get('truncated') or type(m['packet']) is not int or len(m['body_hex'])>256:raise ValueError()
            body=bytes.fromhex(m['body_hex'])
            if len(body)!=m['bytes'] or body[:2].hex()!=op:raise ValueError()
            orders.observe(m,body)
        value=orders.value
        if not valid_order(value) or orders.problem or not start-2<=value['observed_at']<=end+2:raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError) as exc:
        raise ValueError('Não foi possível confirmar entregas e personagem neste diagnóstico. Use um registro de entregas sem troca de personagem.') from exc
    if now>=value['reset_at']:raise ValueError('A semana desse diagnóstico já reiniciou; o total antigo não será recuperado.')
    cache=CharacterCache() if cache is None else cache
    old=(cache.select(identity['name'],identity) or {}).get('latesran')
    if valid_order(old) and (old['reset_at']>value['reset_at'] or
            old['reset_at']==value['reset_at'] and old['current']>=value['current']):
        return dict(identity=identity,current=old['current'],changed=False)
    cache.remember(identity,{},latesran=dict(value,available=True))
    cache.flush(force=True)
    if cache.error:raise OSError('Não foi possível salvar o histórico: '+cache.error)
    return dict(identity=identity,current=value['current'],changed=True)
