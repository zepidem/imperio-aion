# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit player confirmations, separate from automatic server readings."""
import time
from datetime import datetime,timedelta
from daily_duty import BRASILIA
from weekly_orders import next_order_reset

LIMITS={'daily':5,'altgard':12,'latesran':5}

def next_daily_reset(now=None):
    value=datetime.fromtimestamp(time.time() if now is None else now,BRASILIA)
    reset=value.replace(hour=4,minute=0,second=0,microsecond=0)
    if reset<=value:reset+=timedelta(days=1)
    return reset.timestamp()

def confirmation(key,current,now=None):
    now=time.time() if now is None else now
    if key not in LIMITS or type(current) is not int or not 0<=current<=LIMITS[key]:
        raise ValueError('Contagem de atividade fora do limite')
    reset=next_daily_reset(now) if key=='daily' else next_order_reset(datetime.fromtimestamp(now,BRASILIA)).timestamp()
    return dict(current=current,limit=LIMITS[key],reset_at=reset,confirmed_at=now,source='player-confirmation')

def valid_confirmation(key,value):
    basic=(key in LIMITS and isinstance(value,dict) and type(value.get('current')) is int
        and 0<=value['current']<=LIMITS[key] and value.get('limit')==LIMITS[key]
        and type(value.get('confirmed_at')) in (int,float) and type(value.get('reset_at')) in (int,float)
        and 1_500_000_000<=value['confirmed_at']<value['reset_at']<=2_500_000_000
        and value.get('source')=='player-confirmation')
    if not basic:return False
    return value['reset_at']==confirmation(key,value['current'],value['confirmed_at'])['reset_at']

def presentation_state(key,value,now=None):
    if not valid_confirmation(key,value):return None
    now=time.time() if now is None else now
    if now>=value['reset_at']:
        return dict(confirmation(key,0,now),available=True,reset_pending=True,manual=True,
            detail='Período reiniciado · confirme o progresso no jogo')
    return dict(value,available=True,manual=True,cached=False,detail='Valor informado pelo jogador')
