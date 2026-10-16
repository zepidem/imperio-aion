# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Experimental available-entry pools observed in login and private updates.

Nightmare attribute4: 2+5 -> 1+5; daily dungeon attribute101: 0+6 -> 0+5.
Shugo attribute1: login18 -> absolute17; Ascension attribute80000001:
absolute2 followed by login2, corroborating the earlier confirmed zero.
Kinds0/4/8/12 encode the presence of normal/extra scalar values. Initial
and consumption shapes are observed; recharge and weekly rollover still
need live confirmation. Unmapped activities never get guessed zeroes.
"""
from collections import deque
from datetime import datetime,timezone
import time
from inspect_wire import varint
from weekly_orders import next_order_reset,valid_order

ENTRY_LIMITS={'daily_dungeon':14,'shugo_party':21,'nightmare':14,'ascension_rite':3}
EXTRAS={'daily_dungeon','nightmare','ascension_rite'}  # Rito extras observed in login 2026-10-14.
WEEKLY={'daily_dungeon','ascension_rite'}
PROFILES={4:'nightmare',101:'daily_dungeon',1:'shugo_party',80000001:'ascension_rite'}


def scalar(body,at):
    value,end=varint(body,at)
    if end-at>3 or value>999999:raise ValueError('Saldo de entrada fora do perfil')
    return value,end


def read_attribute(body,at):
    if at+5>len(body):raise ValueError('Atributo de entrada truncado')
    kind=body[at];key=int.from_bytes(body[at+1:at+5],'little');at+=5
    if kind==1:
        if at+8>len(body):raise ValueError('Atributo temporal truncado')
        return key,kind,(),at+8
    if kind not in (0,4,8,12):raise ValueError('Tipo de atributo fora do perfil')
    values=[]
    for bit in (4,8):
        if kind&bit:
            value,at=scalar(body,at);values.append(value)
    return key,kind,tuple(values),at


def pool_value(key,kind,values,source):
    activity=PROFILES[key]
    if kind not in (0,4,8,12):raise ValueError('Tipo de saldo de entrada fora do perfil')
    current=values[0] if kind&4 else 0
    bonus=values[-1] if kind&8 else 0
    value=dict(current=current,bonus=bonus,limit=ENTRY_LIMITS[activity],
               attribute_key=key,source=source,experimental=True)
    if not valid_entry(activity,value):raise ValueError('Saldo de entrada acima do limite observado')
    return activity,value


def decode_initial_entries(body):
    if body[:2]!=b'\x0b\x61' or not 3<=len(body)<=65536:raise ValueError('Lista inicial de entradas inválida')
    count,at=varint(body,2)
    if not 0<count<=512:raise ValueError('Quantidade de atributos inválida')
    result={};seen=set()
    for _ in range(count):
        key,kind,values,at=read_attribute(body,at)
        if key in seen:raise ValueError('Atributo inicial duplicado')
        seen.add(key)
        if key in PROFILES:
            activity,value=pool_value(key,kind,values,'0b61-initial');result[activity]=value
    if at!=len(body):raise ValueError('Bytes extras na lista de entradas')
    return result


def decode_entry_update(body):
    if len(body)<9 or body[:2]!=b'\x0c\x61' or body[2] not in (0,1):raise ValueError('Atualização de entrada inválida')
    key,kind,values,at=read_attribute(body,3)
    if key not in PROFILES:raise ValueError('Outro atributo')
    # Cause0 is an absolute spent balance. Cause1 includes replenishment
    # metadata, as in the same typed Od envelope; never integrate its delta.
    if body[2]==0:
        if body[at:]!=b'\x03':raise ValueError('Gasto de entrada fora do perfil')
    else:
        if len(body)!=at+5 or body[at]!=1 or not 0<int.from_bytes(body[at+1:],'little')<=999999:
            raise ValueError('Recarga de entrada fora do perfil')
    return pool_value(key,kind,values,'0c61-absolute-update')


def valid_entry(key,value):
    if not (key in ENTRY_LIMITS and isinstance(value,dict) and type(value.get('current')) is int
            and 0<=value['current']<=ENTRY_LIMITS[key] and value.get('limit')==ENTRY_LIMITS[key]
            and type(value.get('bonus',0)) is int and 0<=value.get('bonus',0)<=100000
            and (key in EXTRAS or not value.get('bonus',0))):return False
    reset=value.get('reset_at')
    return reset is None or valid_order(dict(current=0,limit=5,reset_at=reset))


def historical_entry(key,value,now=None):
    now=time.time() if now is None else now
    if not valid_entry(key,value):return dict(available=False)
    if key in WEEKLY and value.get('reset_at') is None:
        return dict(available=False,detail='Período da leitura de entradas ainda não confirmado')
    if key in WEEKLY and now>=value['reset_at']:
        return dict(available=False,reset_due=True,detail='Semana reiniciada · aguardando entradas do jogo')
    return dict(value,available=True,cached=True,experimental=True,
                detail='Último saldo salvo; ainda não confirmado nesta sessão')


class ActivityEntries:
    LOGIN_WINDOW_US=30_000_000
    SETTLE_US=1_000_000
    MAX_PENDING=512

    def __init__(self,character):
        self.character=character;self.flow=None;self.identity=None;self.awaiting_identity=True;self.login_observed=False
        self.values={};self.pending={};self.problems={}

    def bind_captured_identity(self,flow,binding):
        if self.login_observed:return
        valid=bool(flow and binding and binding.get('character')==self.character
            and type(binding.get('actor')) is int and 0<binding['actor']<10_000_000
            and type(binding.get('server')) is int and 1000<=binding['server']<3000)
        if not valid:
            if self.identity:self.__init__(self.character)
            return
        identity=dict(name=self.character,actor=binding['actor'],server=binding['server'])
        if self.identity and (flow!=self.flow or any(identity[k]!=self.identity[k] for k in ('name','actor','server'))):
            self.__init__(self.character)
        self.identity=identity;self.flow=flow;self.awaiting_identity=False

    def observe(self,ctx,body):
        op=body[:2];flow=ctx['flow'];clock=ctx['clock_us']
        if op not in (b'\x0b\x61',b'\x0c\x61',b'\x33\x36'):return
        if op==b'\x0c\x61' and len(body)>=8 and int.from_bytes(body[4:8],'little') not in PROFILES:return
        if self.flow and self.flow!=flow:self.__init__(self.character)
        self.flow=flow
        if op==b'\x33\x36':
            from live_core import self_identity
            self.login_observed=True
            try:identity=self_identity(body)
            except (ValueError,UnicodeError,IndexError):self.__init__(self.character);self.login_observed=True;return
            same=bool(self.identity and all(identity[k]==self.identity[k] for k in ('name','actor','server')))
            establishing=self.awaiting_identity or not same
            if self.identity and not same:self.pending={}
            if not same:self.values={}
            self.identity=identity;self.awaiting_identity=identity['name']!=self.character
            if same and not establishing:return
            if not self.awaiting_identity:
                for key,events in self.pending.items():
                    recent=[e for e in events if e['flow']==flow and 0<=clock-e['clock_us']<=self.LOGIN_WINDOW_US]
                    if recent:self.values[key]=recent[-1]['value']
            self.pending={};return
        if op==b'\x0b\x61':
            self.values={};self.pending={};self.problems={};self.identity=None;self.awaiting_identity=True;self.login_observed=True
            try:readings=decode_initial_entries(body)
            except (ValueError,IndexError) as exc:
                self.problems={key:str(exc) for key in PROFILES.values()};return
        else:
            if self.identity and self.identity['name']!=self.character:return
            try:key,value=decode_entry_update(body);readings={key:value}
            except (ValueError,IndexError) as exc:
                key=PROFILES.get(int.from_bytes(body[4:8],'little'))
                targets=[key] if key else list(PROFILES.values())
                for key in targets:self.values.pop(key,None);self.pending.pop(key,None);self.problems[key]=str(exc)
                return
        for key,value in readings.items():
            self.problems.pop(key,None)
            value=dict(value,clock_us=clock,observed_at=datetime.now(timezone.utc).isoformat())
            if key in WEEKLY:value['reset_at']=next_order_reset().timestamp()
            self.pending.setdefault(key,deque(maxlen=self.MAX_PENDING)).append(dict(flow=flow,clock_us=clock,value=value))

    def snapshot(self,available,clock_us):
        if available and not self.awaiting_identity and self.identity:
            for key,events in list(self.pending.items()):
                while events and clock_us-events[0]['clock_us']>=self.SETTLE_US:
                    e=events.popleft();previous=self.values.get(key)
                    if e['flow']==self.flow and (previous is None or e['clock_us']>=previous['clock_us']):self.values[key]=e['value']
                if not events:self.pending.pop(key)
        result={}
        for key in PROFILES.values():
            value=self.values.get(key)
            if (available and not self.awaiting_identity and value and not self.problems.get(key)
                    and (key not in WEEKLY or time.time()<value['reset_at'])):
                result[key]=dict(value,available=True)
            else:
                result[key]=dict(available=False,experimental=True,allow_cached=not self.flow or not self.awaiting_identity,
                    reading_error=self.problems.get(key),detail=self.problems.get(key) or 'Aguardando leitura do personagem')
        return result
