# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Experimental Od notification profile, observed 2026-10-13.

Item replenishment and dungeon spending shapes are evidenced. Capacity 840 is a
user-supplied reference, not a decoded field. Never integrate delta into balance.
"""
from inspect_wire import varint
from datetime import datetime,timezone


def decode_initial_od(body):
    """Observed 0b61 typed attribute list; reject unvalidated shapes in full.

    Key 0x03938701 and types are an experimental, user-confirmed profile.
    No inference from byte substring matches or a previous character balance.
    """
    if len(body)<3 or body[:2]!=b'\x0b\x61':
        raise ValueError('Lista inicial de Od inválida')
    count,at=varint(body,2)
    if not 0<count<=512:raise ValueError('Quantidade de atributos fora do perfil')
    found=None;seen=set()
    def number(pos):
        value,end=varint(body,pos)
        if end-pos>3 or value>999999:raise ValueError('Atributo fora do perfil')
        return value,end
    for _ in range(count):
        if at+5>len(body):raise ValueError('Atributo inicial truncado')
        kind=body[at];key=int.from_bytes(body[at+1:at+5],'little');at+=5
        if key in seen:raise ValueError('Atributo inicial duplicado')
        seen.add(key)
        if kind==0:values=()
        elif kind==1:
            if at+8>len(body):raise ValueError('Atributo fixo truncado')
            at+=8;values=()
        elif kind in (4,8):
            value,at=number(at);values=(value,)
        elif kind==12:
            current,at=number(at);bonus,at=number(at);values=(current,bonus)
        else:raise ValueError('Tipo inicial ainda não reconhecido')
        if key==0x03938701:
            if kind not in (0,4,8,12):raise ValueError('Saldo inicial de Od fora do perfil')
            # The shared typed attribute format omits zero-valued fields.
            # Captured kind 8 is bonus-only; it is not a missing normal balance.
            # Require the complete validated list and explicit Od key first.
            found=dict(current=values[0] if kind&4 else 0,
                bonus=values[-1] if kind&8 else 0,delta=None,
                attribute_key=key,update_kind='initial')
    if at!=len(body):raise ValueError('Bytes extras na lista inicial')
    if found is None:raise ValueError('Saldo inicial de Od ausente')
    return found


def decode_od(body):
    if len(body)<8 or body[:2]!=b'\x0c\x61' or body[2] not in (0,1):
        raise ValueError('Formato de Od ainda não reconhecido')
    kind=body[3];key=int.from_bytes(body[4:8],'little')
    if key!=0x03938701 or kind not in (0,4,8,12):
        raise ValueError('Atributo de Od fora do perfil')
    # The same typed attribute used by login omits zero-valued balances.
    # 0c61/00/08/01879303/8201/03 is a captured 0(+130) update.
    # Always read the whole 32-bit key before its optional normal/extra values.
    at=8;values=[]
    for bit in (4,8):
        if not kind&bit:continue
        start=at;value,at=varint(body,at)
        if at-start>3 or value>999999:raise ValueError('Campo de Od fora do perfil')
        values.append(value)
    current=values[0] if kind&4 else 0
    bonus=values[-1] if kind&8 else 0
    if body[2]==0:
        if len(body)!=at+1 or body[at]!=3:
            raise ValueError('Gasto de Od fora do perfil observado')
        return dict(current=current,bonus=bonus,delta=None,attribute_key=key,update_kind='spend')
    if len(body)!=at+5 or body[at]!=1:
        raise ValueError('Reposição de Od fora do perfil observado')
    delta=int.from_bytes(body[at+1:],'little')
    if not 0<delta<=999999:raise ValueError('Variação de Od fora do perfil')
    return dict(current=current,bonus=bonus,delta=delta,attribute_key=key,update_kind='replenishment')



class OdEnergy:
    # In the confirmed login the initial table precedes the self header by
    # 6.3 seconds. Keep it private until that header binds the login to a name.
    LOGIN_WINDOW_US=30_000_000

    def __init__(self,character=None):
        self.character=character
        self.flow=None;self.state=None;self.problem=None
        self.identity=None;self.pending=None;self.awaiting_identity=False
        self.wrong_character=False
        self.balance_problem=None

    def _state(self,ctx,values):
        return dict(values,clock_us=ctx['clock_us'],experimental=True,
            observed_at=datetime.now(timezone.utc).isoformat(),
            capacity=840,capacity_source='user_reference',profile='0b61-initial-0c61-updates-experimental')

    def observe(self,ctx,body):
        if body[:2]==b'\x33\x36':
            from live_core import self_identity
            try:identity=self_identity(body)
            except (ValueError,UnicodeError,IndexError):
                self.state=None;self.pending=None;self.identity=None
                self.awaiting_identity=True;self.problem='Identificação do personagem incompleta'
                return
            same=bool(self.flow==ctx['flow'] and self.identity and
                all(identity[k]==self.identity[k] for k in ('name','server','actor')))
            pending=self.pending;awaiting=self.awaiting_identity
            self.pending=None;self.awaiting_identity=False
            self.wrong_character=bool(self.character and identity['name']!=self.character)
            self.flow=ctx['flow'];self.identity=identity;self.problem=None
            if self.wrong_character:
                self.state=None;self.problem='Od pertence a outro personagem';return
            if pending is not None:
                # A table opens a new login even when the physical socket and
                # character name are unchanged. Never save it under the old actor.
                if pending['flow']==ctx['flow'] and 0<=ctx['clock_us']-pending['clock_us']<=self.LOGIN_WINDOW_US:
                    self.state=pending['state']
                else:
                    self.state=None;self.problem='Saldo inicial fora da janela de identificação'
            elif not same:
                self.state=None
            if self.state is None and (awaiting or same):
                # A valid identity is not a repair of an invalid balance.
                # Keep the actual decoder error visible until a valid reading.
                self.problem=self.balance_problem
            return
        if body[:2] not in (b'\x0c\x61',b'\x0b\x61'):return
        # 0c61 updates many typed attributes. A different pool is not an Od
        # failure; malformed updates for Od itself still follow strict parsing.
        if body[:2]==b'\x0c\x61' and len(body)>=8 and int.from_bytes(body[4:8],'little')!=0x03938701:return
        if body[:2]==b'\x0b\x61':
            self.state=None;self.pending=None;self.awaiting_identity=True
            self.flow=ctx['flow']
        try:values=decode_initial_od(body) if body[:2]==b'\x0b\x61' else decode_od(body)
        except (ValueError,IndexError) as exc:
            self.state=None;self.pending=None;self.problem=str(exc);self.balance_problem=self.problem;return
        self.flow=ctx['flow'];self.problem=None;self.balance_problem=None
        state=self._state(ctx,values)
        if body[:2]==b'\x0b\x61':
            self.pending=dict(flow=ctx['flow'],clock_us=ctx['clock_us'],state=state)
        elif self.awaiting_identity:
            if self.pending and self.pending['flow']==ctx['flow']:
                self.pending['state']=state
        elif not self.wrong_character:
            self.state=state

    def snapshot(self,available):
        if self.awaiting_identity or self.wrong_character:
            return dict(available=False,experimental=True,allow_cached=False,
                awaiting_identity=self.awaiting_identity,
                detail=self.problem or 'Aguardando identificação do personagem para confirmar Od')
        if not available or self.state is None:
            return dict(available=False,experimental=True,reading_error=self.problem,
                detail=self.problem or 'Aguardando atualização de Od')
        return dict(self.state,available=True,detail='Leitura experimental · limite 840 informado pelo usuário')


def energy_parts(state):
    if not isinstance(state,dict) or state.get('available') is not True:
        return ('—','','')
    values=[state.get(key) for key in ('current','bonus','capacity')]
    if any(type(value) is not int or value<0 or value>999999 for value in values) or values[2]==0:
        return ('—','','')
    current,bonus,capacity=values
    return (str(current),f'(+{bonus})',f'/{capacity}')
