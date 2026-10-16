# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Altgard deliveries after an explicit weekly baseline, never shop purchases.

Observed 2026-10-14: two 118d accepts consume scroll 532360075; one 0f8d
success refers to the first accepted instance. Its companion 8456 counter
400100400 is 18, not the weekly count. Only its timestamp is used to identify
the receipt. Missing historical deliveries cannot be reconstructed from this.
The second success has count=1, no optional reward index (13 bytes); the
first has count=2 and reward index 1 (17 bytes).
"""
from datetime import datetime
import hashlib
import time
from inspect_wire import varint
from daily_duty import BRASILIA
from manual_activity import valid_confirmation
from weekly_orders import next_order_reset

ALTGARD_SCROLL=532360075
COMPLETION_COUNTER=400100400


def decode_altgard_accept(body):
    if (body[:2]!=b'\x11\x8d' or not 24<=len(body)<=1024
            or body[6:14]!=b'\x02\x01'+bytes(6) or body[18]!=0
            or int.from_bytes(body[19:23],'little')!=ALTGARD_SCROLL):
        raise ValueError('Outro aceite ou perfil Altgard inválido')
    quest=int.from_bytes(body[2:6],'little');instance=int.from_bytes(body[14:18],'little')
    if not 0<quest<10_000_000 or not 0<instance<10_000_000:raise ValueError('Identificador de missão inválido')
    rewards,at=varint(body,23)
    if rewards!=5:raise ValueError('Recompensas de aceite fora do perfil')
    for _ in range(rewards):
        if at+4>len(body):raise ValueError('Recompensa incompleta')
        item=int.from_bytes(body[at:at+4],'little');at+=4
        amount,at=varint(body,at)
        if not 0<item<2**31 or not 0<amount<10_000_000 or at+9>len(body) or body[at]!=0:
            raise ValueError('Registro de recompensa fora do perfil')
        at+=9
    if at!=len(body):raise ValueError('Bytes extras no aceite')
    return dict(quest=quest,instance=instance)


def valid_tracking(value):
    if not isinstance(value,dict) or not valid_confirmation('altgard',value.get('baseline')):
        return False
    baseline=value['baseline'];receipts=value.get('receipts')
    return (type(value.get('current')) is int and baseline['current']<=value['current']<=12
        and isinstance(receipts,list) and len(receipts)==value['current']-baseline['current']
        and all(isinstance(v,str) and len(v)==64 and all(c in '0123456789abcdef' for c in v) for v in receipts)
        and len(set(receipts))==len(receipts)
        and type(value.get('updated_at')) in (int,float)
        and baseline['confirmed_at']<=value['updated_at']<baseline['reset_at'])


def tracked_presentation(value,now=None):
    if not valid_tracking(value):return dict(available=False)
    now=time.time() if now is None else now
    if now>=value['baseline']['reset_at']:return dict(available=False,reset_due=True)
    return dict(current=value['current'],limit=12,reset_at=value['baseline']['reset_at'],
        available=True,cached=True,tracked=True,experimental=True,
        detail='Última contagem acompanhada; confirme no jogo se houve entregas com o monitor fechado')


class AltgardOrders:
    def __init__(self):
        self.flow=None;self.identity=None;self.accepted={};self.batch=None
        self.stamp=None;self.receipt=None;self.events=[];self.problem=None
        self.unassociated_receipts=0;self.unassociated_signatures=set()

    def diagnostic(self):
        return dict(profile='accepted-altgard-scroll-and-success-receipt',
            weekly_absolute_counter_decoded=False,requires_player_baseline=False,
            weekly_total_status='awaiting-automatic-login-mapping',
            accepted_pending=len(self.accepted),observed_deliveries=len(self.events),
            unassociated_quest_receipts=self.unassociated_receipts,
            unassociated_scope='Generic quest receipts; not necessarily Altgard',
            reading_error=self.problem)

    def observe(self,ctx,body):
        op=body[:2]
        if op==b'\x0b\x61':self.__init__();return
        if op==b'\x33\x36':
            from live_core import self_identity
            try:identity=self_identity(body)
            except ValueError:self.__init__();return
            key=(ctx['flow'],identity['name'],identity['server'],identity['actor'])
            # The first self header can follow accepts on the same owned
            # stream (e.g. a teleport). Publication still requires verified
            # login. Login boundaries and changed identities always clear.
            if (self.identity is not None and self.identity!=key) or (self.flow and self.flow!=ctx['flow']):
                self.__init__()
            self.identity=key
            return
        if op not in (b'\x11\x8d',b'\x84\x56',b'\x0f\x8d'):return
        if self.flow and self.flow!=ctx['flow']:self.__init__()
        self.flow=ctx['flow']
        if op==b'\x11\x8d':
            try:accepted=decode_altgard_accept(body)
            except ValueError:return
            quest=accepted['quest'];instance=accepted['instance']
            if len(self.accepted)>=64 and instance not in self.accepted:
                self.problem='Limite de missões acompanhadas; confirme o total no jogo';return
            prior=self.accepted.get(instance)
            self.accepted[instance]=quest if prior in (None,quest) else False
            return
        batch=(ctx['flow'],ctx['packet'],ctx['clock_us'])
        if batch!=self.batch:self.batch=batch;self.stamp=None;self.receipt=None
        if op==b'\x84\x56':
            if (len(body)!=20 or body[2]!=1 or body[-1]!=2
                    or int.from_bytes(body[3:7],'little')!=COMPLETION_COUNTER):return
            stamp=int.from_bytes(body[11:19],'little')
            if not 1_500_000_000_000<=stamp<=2_500_000_000_000:return
            self.stamp=stamp
        else:
            count=int.from_bytes(body[8:12],'little')
            if count not in (1,2,3) or len(body)!=9+4*count or body[2:4]!=bytes(2) or body[12]!=0:return
            indices=[int.from_bytes(body[i:i+4],'little') for i in range(13,len(body),4)]
            if len(set(indices))!=len(indices) or any(i not in (1,2,3) for i in indices):return
            self.receipt=int.from_bytes(body[4:8],'little')
            if not 0<self.receipt<10_000_000:self.receipt=None;return
        if self.stamp is None or self.receipt is None:return
        if not self.accepted.get(self.receipt):
            signature=(self.stamp,self.receipt)
            if signature not in self.unassociated_signatures:
                self.unassociated_receipts+=1
                if len(self.unassociated_signatures)<64:self.unassociated_signatures.add(signature)
            return
        quest=self.accepted.pop(self.receipt)
        signature=hashlib.sha256(f'{self.stamp}:{self.receipt}:{quest}'.encode()).hexdigest()
        if any(e['signature']==signature for e in self.events):return
        now=time.time()
        reset=next_order_reset(datetime.fromtimestamp(self.stamp/1000,BRASILIA)).timestamp()
        self.events=[e for e in self.events if e['reset_at']==reset]
        if len(self.events)>=12:
            self.problem='Mais de 12 entregas observadas; confirme a contagem no jogo';return
        self.events.append(dict(signature=signature,seen_at=now,reset_at=reset))

    def reconcile(self,record,available,now=None):
        """Return presentation + persistable tracking; no writes without login."""
        now=time.time() if now is None else now
        baseline=(record or {}).get('activity_baselines',{}).get('altgard')
        prior=(record or {}).get('altgard_tracking')
        if not valid_confirmation('altgard',baseline) or now>=baseline['reset_at']:
            return dict(available=False,baseline_required=True,observed_deliveries=len(self.events),reading_error=self.problem),None
        if not valid_tracking(prior) or prior['baseline']!=baseline:
            prior=dict(baseline=dict(baseline),current=baseline['current'],receipts=[],updated_at=baseline['confirmed_at'])
        value=dict(prior,receipts=list(prior['receipts']))
        applicable=[e for e in self.events if e['reset_at']==baseline['reset_at']
            and baseline['confirmed_at']<e['seen_at']<=now]
        if not available or self.problem:return dict(available=False,reading_error=self.problem),None
        for event in applicable:
            if event['signature'] in value['receipts']:continue
            if value['current']>=12:
                return dict(available=False,reading_error='A contagem excederia 12; informe o total correto no jogo'),None
            value['receipts'].append(event['signature']);value['current']+=1
            value['updated_at']=max(value['updated_at'],event['seen_at'])
        if not value['receipts']:return dict(available=False,observed_deliveries=len(self.events)),None
        presentation=tracked_presentation(value,now)
        presentation['cached']=not bool(applicable)
        return presentation,value if value!=prior or value!=(record or {}).get('altgard_tracking') else None
