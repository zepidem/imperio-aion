# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Experimental Dever list profile, separate from paid list refreshes.

The supplied 19:28 recording contains two complete 1c8d lists: refresh count
4 -> 5, completion count 5 -> 5, and the same five completed entries. The
server's next reset is 2026-10-14 07:00 UTC (04:00 Brasília). Intermediate
completion transitions still require a live-game confirmation.
"""
from datetime import datetime,timezone,timedelta
import time
from inspect_wire import varint

BRASILIA=timezone(timedelta(hours=-3),'Brasília')


def decode_duty(body):
    if body[:2]!=b'\x1c\x8d' or not 28<=len(body)<=65536:
        raise ValueError('Lista de Dever fora do perfil observado')
    refreshes=int.from_bytes(body[2:6],'little')
    current=int.from_bytes(body[6:10],'little')
    count,at=varint(body,10)
    if refreshes>1000 or current>5 or not 1<=count<=100:
        raise ValueError('Contadores de Dever fora do perfil observado')
    completed=0;identifiers=set()
    for _ in range(count):
        if at+18>len(body):raise ValueError('Lista de Dever incompleta')
        identifier=int.from_bytes(body[at:at+4],'little')
        quest=int.from_bytes(body[at+4:at+8],'little')
        state=body[at+16];at+=17
        if not 0<identifier<10000000 or identifier in identifiers or not 2302000<=quest<2303000 or state not in (1,2,3,4):
            raise ValueError('Entrada de Dever fora do perfil observado')
        identifiers.add(identifier);completed+=state==4
        rewards,at=varint(body,at)
        if rewards!=5:raise ValueError('Recompensas de Dever fora do perfil observado')
        for _ in range(rewards):
            if at+4>len(body):raise ValueError('Recompensa de Dever incompleta')
            at+=4
            amount,at=varint(body,at)
            if not 0<amount<10000000 or at+9>len(body) or body[at]!=0:
                raise ValueError('Recompensa de Dever fora do perfil observado')
            at+=9
    if completed!=current or len(body)-at!=17 or body[at+8]!=0:
        raise ValueError('Totais de Dever inconsistentes')
    reset=int.from_bytes(body[at+9:at+17],'little')
    list_reset=int.from_bytes(body[at:at+8],'little')
    if reset!=list_reset or not 1_500_000_000_000<=reset<=2_500_000_000_000:
        raise ValueError('Reinício de Dever fora do perfil observado')
    stamp=datetime.fromtimestamp(reset/1000,BRASILIA)
    if (stamp.hour,stamp.minute,stamp.second,stamp.microsecond)!=(4,0,0,0):
        raise ValueError('Reinício de Dever diferente das 04:00 de Brasília')
    return dict(current=current,limit=5,refreshes=refreshes,reset_at=reset/1000,
                experimental=True,source='1c8d-validated-list')


def valid_duty(value):
    return (isinstance(value,dict) and type(value.get('current')) is int and 0<=value['current']<=5
        and value.get('limit')==5 and type(value.get('reset_at')) in (int,float)
        and 1_500_000_000<=value['reset_at']<=2_500_000_000)


def historical_duty(value,now=None):
    now=time.time() if now is None else now
    if not valid_duty(value):return dict(available=False)
    # An expired record cannot prove what was completed while the app was off.
    if now>=value['reset_at']:
        from manual_activity import next_daily_reset
        return dict(current=0,limit=5,available=True,reset_due=True,reset_pending=True,
            reset_at=next_daily_reset(now),detail='Dia reiniciado · confirme o progresso no jogo')
    return dict(value,available=True,cached=True)


class DailyDuty:
    def __init__(self):
        self.flow=None;self.pending=None;self.value=None;self.problem=None;self.reset_seen=False

    def observe(self,ctx,body):
        if body[:2]==b'\x0b\x61':
            self.flow=ctx['flow'];self.pending=None;self.value=None;self.problem=None;return
        if body[:2]!=b'\x1c\x8d':return
        self.flow=ctx['flow']
        try:value=decode_duty(body)
        except (ValueError,IndexError) as exc:
            self.pending=None;self.value=None;self.problem=str(exc);return
        self.problem=None
        self.pending=dict(value,clock_us=ctx['clock_us'],observed_at=datetime.now(timezone.utc).isoformat())

    def snapshot(self,available,clock_us,now=None):
        now=time.time() if now is None else now
        if available and self.pending and clock_us-self.pending['clock_us']>=1_000_000:
            self.value=self.pending;self.pending=None;self.reset_seen=False
        if not available or not self.value:
            return dict(available=False,reading_error=self.problem)
        if now>=self.value['reset_at']:
            # Only an active, identified capture that observed the preceding
            # day can show the clock reset. Never store it as a server reading.
            if not self.reset_seen:self.reset_seen=True
            return dict(available=True,current=0,limit=5,reset_pending=True,
                detail='Dia reiniciado às 04:00 de Brasília · aguardando confirmação do jogo')
        return dict(self.value,available=True)


def duty_presentation(state):
    if not state.get('available'):
        return dict(text='Dever —/5',note='Abra Dever no jogo',hint=state.get('detail') or 'Abra Diário → Dever para receber a contagem diária. Reinício às 04:00 de Brasília.',complete=False)
    current=state['current'];cached=state.get('cached',False)
    note='Reiniciado · confirmar no jogo' if state.get('reset_pending') else 'Informado pelo jogador' if state.get('manual') else 'Última leitura salva' if cached else 'Completo' if current==5 else f'Faltam {5-current}'
    hint=f'Dever diário: {current}/5 · {note}.\nReinício às 04:00 de Brasília. Atualizar a lista de missões não reinicia as conclusões.'
    if cached or state.get('reset_pending'):hint+='\nAbra Dever no jogo para confirmar o progresso atual.'
    hint+='\nLeitura experimental: 5/5 validado; transições intermediárias aguardam teste no jogo.'
    return dict(text='Dever '+('~' if cached else '')+f'{current}/5',note=note,hint=hint,complete=current==5 and not cached)
