# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Experimental absolute gain counters confirmed against the user's game UI.

Login captures confirm a5ff/a9ff. The 2026-10-13 farm capture and user-confirmed
74,849 daily Kinah validate a6ff absolute updates outside Abyss. Wallet updates
are unrelated: Kinah earned inside Abyss does not count toward this daily cap.
No local accrual, local-time reset or guessed zero baseline. Historical display
is provided separately by CharacterCache and never seeds this decoder.
"""
from datetime import datetime,timezone
from collections import deque

KINAH_LIMIT=1_000_000
ABYSS_LIMITS=frozenset((1_000_000,1_500_000))  # Both captured login profiles.


def valid_gain_value(key,value):
    if not isinstance(value,dict):return False
    limit=value.get('limit');current=value.get('current')
    limits=(KINAH_LIMIT,) if key=='kinah_daily' else ABYSS_LIMITS if key=='abyss_weekly' else ()
    return type(limit) is int and limit in limits and type(current) is int and 0<=current<=limit

PROFILES={b'\xa5\xff':'kinah_daily',b'\xa6\xff':'kinah_daily',b'\xa9\xff':'abyss_weekly'}
PERIODS={'kinah_daily':('Kinah diário','D'),'abyss_weekly':('Pontos do Abismo semanais','S')}


def decode_gain(body):
    key=PROFILES.get(body[:2])
    if key is None or len(body)!=18:raise ValueError('Formato de ganho não reconhecido')
    current=int.from_bytes(body[2:10],'little')
    extra=int.from_bytes(body[10:18],'little')
    if key=='kinah_daily':
        # This field changes between equal counter readings and resembles a
        # server timestamp. Preserve it as raw metadata, never as a reset time.
        if not 1_500_000_000_000<=extra<=2_500_000_000_000:
            raise ValueError('Marcador de Kinah fora do perfil observado')
        limit=KINAH_LIMIT;source='user_reference'
    else:
        if extra not in ABYSS_LIMITS:raise ValueError('Limite de Abismo fora do perfil observado')
        limit=extra;source='wire'
    if current>limit:raise ValueError('Ganho acima do limite do perfil observado')
    return key,dict(current=current,limit=limit,limit_source=source,
        server_marker_raw=extra if key=='kinah_daily' else None,experimental=True)


class ResourceGains:
    LOGIN_WINDOW_US=30_000_000
    SETTLE_US=1_000_000
    LOGIN_LEAD_US=2_000_000  # Login preface retention is independent of display latency.
    MAX_PENDING=512

    def __init__(self,character):
        self.character=character;self.flow=None;self.identity=None
        self.values={};self.pending={};self.problems={};self.awaiting_identity=True
        self.login_observed=False
        self.login_boundary_us=None
        self.login_trace=deque(maxlen=64)

    def bind_captured_identity(self,flow,binding):
        """When starting mid-session, use the DPS reader's resolved identity.

        Only a fresh capture without a login boundary can use this fallback.
        A later login must wait for its own header, never a stale combat name.
        """
        if self.login_observed:return
        valid=bool(flow and binding and binding.get('character')==self.character
            and type(binding.get('actor')) is int and 0<binding['actor']<10_000_000
            and type(binding.get('server')) is int and 1000<=binding['server']<3000)
        if not valid:
            if self.identity:
                self.identity=None;self.awaiting_identity=True;self.values={};self.pending={}
            return
        identity=dict(name=self.character,actor=binding['actor'],server=binding['server'],
                      basis=binding.get('basis','captured combat identity'))
        if self.identity and (flow!=self.flow or any(identity[k]!=self.identity[k] for k in ('name','actor','server'))):
            self.values={};self.pending={}
        self.identity=identity;self.flow=flow;self.awaiting_identity=False

    def observe(self,ctx,body):
        op=body[:2];flow=ctx['flow'];clock=ctx['clock_us']
        if op in (b'\x0b\x61',b'\x33\x36',*PROFILES):
            self.login_trace.append(dict(opcode=op.hex(),flow=flow,clock_us=clock,
                bytes=len(body),awaiting_identity=self.awaiting_identity,
                published_keys=list(self.values),pending_keys=list(self.pending)))
        if op==b'\x0b\x61':
            self.login_observed=True
            self.login_boundary_us=clock
            # The weekly initial counter precedes this login boundary by 151ms.
            # Keep only the brief, unpublished leading portion of this login.
            recent={k:deque((v for v in events if v['flow']==flow
                and 0<=clock-v['clock_us']<=self.LOGIN_LEAD_US),maxlen=self.MAX_PENDING)
                for k,events in self.pending.items()}
            # A preface may already have settled while the previous identity
            # was still active. Retain a genuinely recent wire value as an
            # unpublished candidate, subject to the new identity header.
            if self.flow==flow:
                for key,value in self.values.items():
                    if 0<=clock-value['clock_us']<=self.LOGIN_LEAD_US:
                        events=recent.setdefault(key,deque(maxlen=self.MAX_PENDING))
                        events.appendleft(dict(flow=flow,clock_us=value['clock_us'],value=value))
            self.pending={k:events for k,events in recent.items() if events}
            self.values={};self.problems={};self.awaiting_identity=True;self.flow=flow
            return
        if op==b'\x33\x36':
            self.login_observed=True
            from live_core import self_identity
            try:identity=self_identity(body)
            except (ValueError,UnicodeError,IndexError):
                self.values={};self.pending={};self.identity=None;self.awaiting_identity=True
                return
            same=bool(self.flow==flow and self.identity and
                all(identity[k]==self.identity[k] for k in ('name','actor','server')))
            if self.identity and self.identity['name']!=identity['name']:
                # A different self header can now switch the automatic name.
                # Never transfer the preceding character's settled preface.
                boundary=self.login_boundary_us
                self.pending={key:deque((event for event in events
                    if boundary is not None and event['clock_us']>=boundary),maxlen=self.MAX_PENDING)
                    for key,events in self.pending.items()}
            establishing=self.awaiting_identity or not same
            if not same:self.values={}
            self.identity=identity;self.flow=flow
            self.awaiting_identity=identity['name']!=self.character
            if same and not establishing:
                # Repeated identity messages must not bypass the transition guard.
                return
            if not self.awaiting_identity:
                for key,events in self.pending.items():
                    for event in reversed(events):
                        if event['flow']==flow and 0<=clock-event['clock_us']<=self.LOGIN_WINDOW_US:
                            self.values[key]=event['value'];break
            else:self.values={}
            self.pending={}
            return
        key=PROFILES.get(op)
        if key is None:return
        self.flow=flow
        try:_,value=decode_gain(body)
        except ValueError as exc:
            self.values.pop(key,None);self.pending.pop(key,None);self.problems[key]=str(exc)
            return
        self.problems.pop(key,None)
        # Every reading gets its own transition guard. Replacing a single
        # pending reading restarts the guard on every gain and starves display
        # during continuous farm. These are absolute counters, never deltas.
        events=self.pending.setdefault(key,deque(maxlen=self.MAX_PENDING))
        events.append(dict(flow=flow,clock_us=clock,value=dict(value,clock_us=clock,
            observed_at=datetime.now(timezone.utc).isoformat())))

    def snapshot(self,available,clock_us):
        if available and not self.awaiting_identity and self.identity:
            for key,events in list(self.pending.items()):
                while events and clock_us-events[0]['clock_us']>=self.SETTLE_US:
                    event=events.popleft()
                    previous=self.values.get(key)
                    if event['flow']==self.flow and (previous is None or event['clock_us']>=previous['clock_us']):
                        self.values[key]=event['value']
                if not events:self.pending.pop(key)
        result={}
        for key in PERIODS:
            value=self.values.get(key)
            if available and not self.awaiting_identity and value is not None:
                result[key]=dict(value,available=True)
            else:
                result[key]=dict(available=False,experimental=True,
                    reading_error=self.problems.get(key),
                    allow_cached=not self.flow or not self.awaiting_identity,
                    detail=self.problems.get(key) or 'Aguardando leitura do personagem')
        return result


def compact_gain(value):
    if type(value) is not int or value<0 or value>max(ABYSS_LIMITS):return '—'
    if value>=1_000_000:
        tenths=value%1_000_000//100_000
        return f'{value//1_000_000}'+(f'.{tenths}' if tenths else '')+'M'
    if value>=1000:return f'{value//1000}.{value%1000//100}K'
    return str(value)


def gain_presentation(key,state):
    name,suffix=PERIODS[key]
    state=state if isinstance(state,dict) else {}
    current=state.get('current');limit=state.get('limit')
    known=state.get('available') is True and valid_gain_value(key,state)
    if not known:
        return dict(text='—/'+suffix,hint=name+' · aguardando leitura.\nContinue jogando ou abra a janela de recursos.\nSe houver dessincronização, relogue com a captura ativa.',at_limit=False,available=False)
    full=lambda n:f'{n:,}'.replace(',','.')
    hint=f'{name}: {full(current)} / {full(limit)} · {current*100//limit}%'
    cached=state.get('cached') is True
    if current>=limit:hint+=' · limite atingido no valor salvo' if cached else ' · limite atingido'
    if cached:
        hint+='\nÚltimo ganho salvo · não confirmado neste período.'
        if state.get('confirmed_at') or state.get('saved_at'):
            hint+='\nÚltima leitura: '+display_time(state.get('confirmed_at') or state['saved_at'])
        hint+='\nAguardando nova leitura do jogo. Se estiver dessincronizado, relogue com a captura ativa.'
    if state.get('reading_error'):hint+='\nLeitura incompleta; último valor preservado.'
    if key=='kinah_daily':hint+='\nReinício às 04:00, conforme o horário exibido pelo jogo.'
    return dict(text=('~' if cached else '')+compact_gain(current)+'/'+suffix,hint=hint,at_limit=current>=limit and not cached,available=True)


def display_time(value):
    try:return datetime.fromisoformat(value).astimezone().strftime('%d/%m/%Y %H:%M:%S')
    except (TypeError,ValueError,OverflowError):return 'data indisponível'
