# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Experimental Latesran receipt profile, observed at five user-confirmed deliveries.

8456 counter 244211400 advanced 1..5 alongside five successful 0f8d
receipts on 2026-10-13. Require both in the same decoded TCP packet/clock.
Purchases, accepts and other counters are not completions. Weekly rollover
is supplied by the user, not decoded from this timestamp; never invent zero.
"""
from datetime import datetime,timedelta
import time
from daily_duty import BRASILIA

LATESRAN_COUNTER=244211400


def next_order_reset(now=None):
    now=datetime.now(BRASILIA) if now is None else now.astimezone(BRASILIA)
    reset=now.replace(hour=4,minute=0,second=0,microsecond=0)
    reset+=timedelta(days=(2-now.weekday())%7)
    if reset<=now:reset+=timedelta(days=7)
    return reset


def decode_latesran(body):
    # Both raw flags occur before completion (0 at 1..3, 1 at 4..5).
    # Do not interpret the flag as a completion boolean.
    if len(body)!=20 or body[:3]!=b'\x84\x56\x01' or body[-1] not in (0,1):
        raise ValueError('Contador Latesran fora do perfil')
    if int.from_bytes(body[3:7],'little')!=LATESRAN_COUNTER:
        raise ValueError('Outro contador')
    current=int.from_bytes(body[7:11],'little');stamp=int.from_bytes(body[11:19],'little')/1000
    if not 0<=current<=5 or not 1_500_000_000<=stamp<=2_500_000_000:
        raise ValueError('Valor Latesran fora do perfil')
    return dict(current=current,limit=5,observed_at=stamp,
        reset_at=next_order_reset(datetime.fromtimestamp(stamp,BRASILIA)).timestamp(),
        source='8456-counter-and-0f8d-receipt',experimental=True)


def valid_order(value):
    if not (isinstance(value,dict) and type(value.get('current')) is int and 0<=value['current']<=5
        and value.get('limit')==5 and type(value.get('reset_at')) in (int,float)
        and 1_500_000_000<=value['reset_at']<=2_500_000_000):return False
    dt=datetime.fromtimestamp(value['reset_at'],BRASILIA)
    return (dt.weekday(),dt.hour,dt.minute,dt.second,dt.microsecond)==(2,4,0,0,0)


def historical_order(value,now=None):
    now=time.time() if now is None else now
    if not valid_order(value):return dict(available=False)
    if now>=value['reset_at']:
        return dict(current=0,limit=5,available=True,reset_due=True,reset_pending=True,
            reset_at=next_order_reset(datetime.fromtimestamp(now,BRASILIA)).timestamp(),
            detail='Semana reiniciada · confirme o progresso no jogo')
    return dict(value,available=True,cached=True,experimental=True)


class WeeklyOrders:
    def __init__(self):
        self.flow=None;self.batch=None;self.candidate=None;self.receipt=False
        self.value=None;self.problem=None;self.identity=None
        self.initial_pending=None;self.login_clock=None

    def observe(self,ctx,body):
        op=body[:2]
        if op==b'\x83\x56':
            from login_orders import decode_login_latesran
            try:value=decode_login_latesran(body)
            except ValueError:return
            self.initial_pending=dict(value,flow=ctx['flow'],clock_us=ctx['clock_us'])
            return
        if op==b'\x0b\x61':
            pending=self.initial_pending
            self.__init__();self.login_clock=ctx['clock_us']
            if pending and pending['flow']==ctx['flow'] and 0<=ctx['clock_us']-pending['clock_us']<=1_000_000:
                self.initial_pending=pending
            return
        if op==b'\x33\x36':
            from live_core import self_identity
            try:identity=self_identity(body)
            except ValueError:self.__init__();return
            key=(ctx['flow'],identity['name'],identity['server'],identity['actor'])
            if self.identity!=key:
                pending=self.initial_pending;login_clock=self.login_clock
                self.__init__();self.identity=key
                if (pending and pending['flow']==ctx['flow'] and login_clock is not None
                        and 0<=ctx['clock_us']-login_clock<=30_000_000):
                    self.flow=ctx['flow'];self.value={k:v for k,v in pending.items() if k!='flow'}
            return
        if op not in (b'\x84\x56',b'\x0f\x8d'):return
        if self.flow and self.flow!=ctx['flow']:self.__init__()
        self.flow=ctx['flow']
        batch=(ctx['flow'],ctx['packet'],ctx['clock_us'])
        if batch!=self.batch:
            self.batch=batch;self.candidate=None;self.receipt=False
        if op==b'\x84\x56':
            if body[3:7]!=LATESRAN_COUNTER.to_bytes(4,'little'):return
            try:self.candidate=decode_latesran(body)
            except ValueError as exc:self.problem=str(exc);self.candidate=None;return
        else:
            # Observed success reply: u16 result=0, quest id, reward records.
            count=int.from_bytes(body[8:12],'little')
            if (count not in (2,3,4) or len(body)!=9+4*count or body[2:4]!=b'\0\0'
                or not 0<int.from_bytes(body[4:8],'little')<10_000_000
                or body[12:17]!=b'\0\x01\0\0\0'):return
            indices=[int.from_bytes(body[i:i+4],'little') for i in range(13,len(body),4)]
            if len(set(indices))!=len(indices) or any(i not in (1,2,3) for i in indices):return
            self.receipt=True
        if self.candidate and self.receipt:
            if self.value and self.value['reset_at']==self.candidate['reset_at'] and self.candidate['current']<self.value['current']:
                self.problem='Contador semanal regrediu; aguardando confirmação';return
            self.value=dict(self.candidate,clock_us=ctx['clock_us']);self.problem=None

    def snapshot(self,available,clock_us,now=None):
        now=time.time() if now is None else now
        if not available or self.problem or not self.value or clock_us-self.value['clock_us']<1_000_000:
            return dict(available=False,reading_error=self.problem)
        if now>=self.value['reset_at']:
            return historical_order(self.value,now)
        return dict(self.value,available=True,reading_error=self.problem)
