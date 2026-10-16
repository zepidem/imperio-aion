# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded collection state, observed initial/soul updates and local diagnostics.

Kills never become souls. Live state is isolated by character and connection;
an account GUID has not been decoded. Level 1 -> 2 is an observed, paired
notification/consumption update, never inferred from a threshold alone.
"""
from collections import deque
import hashlib
import math
import time
from pet_catalog import CATALOG
from datetime import datetime,timezone,timedelta


class PetCollection:
    def __init__(self):
        self.scope=None;self.entries={};self.events=set();self.event_order=deque();self.sequence=0

    def apply(self,scope,event):
        """Absolute update from a future verified adapter, not a kill counter."""
        if not isinstance(scope,tuple) or len(scope)!=2 or not all(isinstance(x,str) and 0<len(x)<=100 for x in scope):
            raise ValueError('Conta/servidor não identificados')
        required=('event_id','pet_id','name','level','souls','next_required','kind')
        if any(k not in event for k in required):raise ValueError('Coleção incompleta')
        if not all(isinstance(event[k],str) and 0<len(event[k])<=100 and not any(ord(c)<32 for c in event[k]) for k in ('event_id','pet_id','name')):
            raise ValueError('Identificador de mascote inválido')
        level,souls,denominator=event['level'],event['souls'],event['next_required']
        if type(level) is not int or not 1<=level<=3:raise ValueError('Nível fora do perfil Global3 confirmado')
        if type(souls) is not int or souls<0 or type(denominator) is not int or denominator<0:
            raise ValueError('Contagem inválida')
        if level<3 and denominator!={1:25,2:75}[level] or level<3 and souls>denominator:
            raise ValueError('Etapa não confirmada pelo perfil Global3')
        if event['kind'] not in ('snapshot','collection'):raise ValueError('Evento desconhecido')
        if self.scope!=scope:
            self.scope=scope;self.entries={};self.events=set();self.event_order=deque();self.sequence=0
        if event['event_id'] in self.events:return False
        if len(self.entries)>=500 and event['pet_id'] not in self.entries:
            raise ValueError('Limite de coleção atingido')
        previous=self.entries.get(event['pet_id'])
        if previous and (level<previous['level'] or level==previous['level'] and souls<previous['souls']):
            raise ValueError('Atualização antiga da coleção')
        # Retain a bounded recent deduplication window, not a lifetime stop.
        if len(self.event_order)>=20000:self.events.discard(self.event_order.popleft())
        self.events.add(event['event_id']);self.event_order.append(event['event_id']);self.sequence+=1
        # Repeated absolute notifications are not new collections.
        advanced=previous is None or level>previous['level'] or souls>previous['souls']
        recent=self.sequence if event['kind']=='collection' and advanced else previous.get('recent',0) if previous else 0
        # Level 1 -> 2 counter reset/carry has not been observed. Do not guess
        # a total to level 3 by adding the next stage's threshold.
        remaining=0 if level==3 else denominator-souls if level==2 else None
        self.entries[event['pet_id']]=dict(event,remaining_to_three=remaining,
            remaining_to_next_level=0 if level==3 else denominator-souls,recent=recent,
            account_scope=list(scope),icon_key=event.get('icon_key'),npc_id=event.get('npc_id'))
        return True

    def snapshot(self):
        recent=sorted((dict(e) for e in self.entries.values() if e['recent']),key=lambda e:e['recent'],reverse=True)[:500]
        return dict(status='synced' if self.scope else 'unavailable',account_scope=list(self.scope) if self.scope else None,
                    entries=recent,wire_decoder_validated=False)


def initial_collection(body):
    """Decode only the bounded 0090 prefix validated in the login sample.

    Two independent raw maps. A separately recorded default of zero is qualified
    only for level1 under the observed complete 985-byte-tail profile; raw absent
    values stay absent. Other properties in the tail remain unsupported.
    """
    from inspect_wire import varint
    if body[:2]!=b'\x00\x90' or not 8<=len(body)<=65536:
        raise ValueError('Lista inicial de mascotes inválida')
    equipped=int.from_bytes(body[2:6],'little');at=6;tables=[]
    for width in (12,8):
        count,at=varint(body,at)
        if not 1<=count<=500 or at+count*width>len(body):
            raise ValueError('Lista inicial truncada ou fora do limite')
        values={}
        for _ in range(count):
            identifier=int.from_bytes(body[at:at+4],'little')
            value=int.from_bytes(body[at+width-4:at+width],'little')
            if not 0<identifier<1000000 or identifier in values:
                raise ValueError('Identificador de mascote repetido/inválido')
            if width==12 and (int.from_bytes(body[at+4:at+8],'little')!=identifier or not 1<=value<=3):
                raise ValueError('Etapa de mascote fora do perfil observado')
            if width==8 and value>75:raise ValueError('Almas fora do perfil observado')
            values[identifier]=value;at+=width
        tables.append(values)
    levels,souls=tables
    if equipped and equipped not in levels:raise ValueError('Mascote equipada ausente da lista de níveis')
    for identifier in levels.keys() & souls.keys():
        level=levels[identifier]
        if level<3 and souls[identifier]>{1:25,2:75}[level]:
            raise ValueError('Contagem incompatível com a etapa')
    tail=body[at:]
    sparse_profile=(len(tail)==985 and tail[:2]==b'\x06\x01' and
        tail[-20:]==bytes.fromhex('0000000003010000000000000000000000000201'))
    defaults={identifier:0 for identifier,level in levels.items()
        if level==1 and identifier not in souls} if sparse_profile else {}
    return dict(equipped=equipped,levels=levels,souls=souls,prefix_bytes=at,
                unsupported_tail_bytes=len(tail),soul_defaults=defaults,
                sparse_zero_profile_validated=sparse_profile,
                soul_default_source='experimental-sparse-zero-level1' if sparse_profile else None)


class PetWireCollection(PetCollection):
    """Observed soul receipts plus an optional 0090 absolute collection base.

    Observe only already framed/reassembled messages. TCP retransmissions are
    removed upstream; identical bodies at distinct positions are real events.
    No persistent count, guessed baseline, cross-flow or kill-derived progress.
    """
    NAMES={1067:'Escorpião de Minério',1181:'Brax'}

    def __init__(self,character='Zepidem'):
        super().__init__();self.character=character;self.flow=None;self.identity=None
        self.pending={};self.serial=0;self.initial=None;self.initial_digest=None
        self.seed=[];self.problem=None;self.blocked=set();self.discarded=0
        self.raw_souls={};self.transitions={};self.level_updates={};self.soul_sources={}
        self.login_base=None
        self.pending_incomplete=set()
        self.session_counts={};self.session_sequence=0;self.session_last_serial=0
        self.session_incomplete=False;self.session_detail=None
        self.account_gap=False;self.rejected_messages=[];self.last_clock_us=0;self.unresolved_species=set()
        self.recent_messages=deque(maxlen=128);self.fault_sink=None;self.last_fault=None;self.next_fault_attempt=0;self.automatic_diagnostic_error=None

    def identity_scope(self,flow=None,identity=None):
        # The local world socket can survive a logout. Actor is part of the
        # character session boundary, never a decoded account identifier.
        flow=self.flow if flow is None else flow
        identity=self.identity if identity is None else identity
        if identity.get('basis')=='captured_game_process':
            key=f"{flow}|captured|{identity['process_id']}"
            server='captured-game-process'
        else:
            key=f"{flow}|{identity['actor']}"
            server=str(identity['server'])
        return (self.character+':'+hashlib.sha256(key.encode()).hexdigest()[:16],server)

    def _clear_session_counts(self):
        self.session_counts={};self.session_sequence=0;self.session_last_serial=0
        self.session_incomplete=False;self.session_detail=None
        self.account_gap=False;self.unresolved_species.clear()

    def attach_live_channel(self,flow,process_id):
        """Bind the collector's exact, accepted incoming game-process channel.

        The caller supplies socket ownership and protocol acceptance. This
        local proof supplies neither a character actor nor a server/account ID.
        A healthy established channel cannot be replaced by another socket.
        """
        if not isinstance(flow,str) or not 0<len(flow)<=500 or flow in self.blocked:
            return False
        if type(process_id) is not int or not 0<process_id<2**32:return False
        if self.flow==flow:
            owner=self.identity.get('process_id') if self.identity else None
            if owner is None and self.identity is not None:
                # Login identity can precede ownership proof. Remember the
                # owner without changing collection state or receipt order.
                self.identity['process_id']=process_id
            return owner is None or owner==process_id
        if self.flow is not None and self.flow not in self.blocked:return False
        previous_owner=self.identity.get('process_id') if self.identity else None
        if self.flow is not None:
            # A fresh socket cannot reuse an old absolute table. Receipts
            # observed during the same ON period remain useful across a gap.
            self.scope=None;self.entries={};self.events=set();self.event_order=deque();self.sequence=0
            self.initial=None;self.initial_digest=None;self.seed=[]
            self.raw_souls={};self.transitions={};self.level_updates={};self.soul_sources={};self.login_base=None
            if previous_owner!=process_id:self._clear_session_counts()
        self.flow=flow
        self.identity=dict(name=self.character,actor=None,server=None,
            basis='captured_game_process',process_id=process_id)
        incomplete=flow in self.pending_incomplete
        queued=self.pending.pop(flow,[])
        self.pending.clear();self.pending_incomplete.clear()
        self.problem=None
        if incomplete:
            self.session_incomplete=True;self.session_detail='Parte das almas anteriores não pôde ser registrada'
        else:
            for context,message in queued:self._apply_message(context,message)
        return True

    def _count_session_soul(self,ctx,body):
        """Count each framed 0d90 receipt once, independently of account totals."""
        # Validate the entire envelope before mutating any counter. Captured
        # receipts confirm one or two (species, amount) records, in wire order.
        if len(body)<3 or body[2] not in (1,2) or len(body)!=3+8*body[2]:
            raise ValueError('Formato de coleta ainda não confirmado')
        receipts=[]
        for at in range(3,len(body),8):
            identifier=str(int.from_bytes(body[at:at+4],'little'))
            amount=int.from_bytes(body[at+4:at+8],'little')
            if not 0<int(identifier)<1000000 or amount not in (1,2):
                raise ValueError('Formato de coleta ainda não confirmado')
            receipts.append((identifier,amount))
        serial=ctx['event_serial']
        if serial<=self.session_last_serial:return []
        if len(set(self.session_counts)|{key for key,_ in receipts})>500:
            raise ValueError('Limite de mascotes nesta captura atingido')
        for identifier,amount in receipts:
            meta=CATALOG.get(identifier) or dict(name=f'Mascote {identifier}',icon_key=None)
            previous=self.session_counts.get(identifier,{})
            self.session_sequence+=1
            self.session_counts[identifier]=dict(pet_id=identifier,name=meta['name'],icon_key=meta['icon_key'],
                session_souls=previous.get('session_souls',0)+amount,recent=self.session_sequence,
                first_clock_us=previous.get('first_clock_us',ctx['clock_us']))
        self.session_last_serial=serial
        return receipts

    def observe(self,ctx,body):
        if body[:2] in (b'\x00\x90',b'\x0d\x90',b'\x09\x90',b'\x14\x90',b'\x0e\x90',b'\x33\x36'):
            self.recent_messages.append(dict(flow=ctx['flow'],clock_us=ctx['clock_us'],
                packet=ctx.get('packet'),opcode=body[:2].hex(),body_hex=body[:8192].hex(),
                bytes=len(body),truncated=len(body)>8192))
        self._observe(ctx,body)
        self._report_fault()

    def _report_fault(self):
        reason=self.problem or ('Coleção inicial ausente após recebimento de almas' if self.session_counts and not self.initial else None)
        if not reason:
            self.last_fault=None;return
        signature=(self.flow,reason,self.account_gap)
        if not self.fault_sink or signature==self.last_fault:return
        now=time.monotonic()
        if now<self.next_fault_attempt:return
        self.next_fault_attempt=now+10
        try:
            saved=self.fault_sink(dict(reason=reason,pet_collection=self.snapshot(),
                recent_pet_messages=list(self.recent_messages),rejections=self.rejection_samples()))
            if saved:self.last_fault=signature;self.automatic_diagnostic_error=None
        except OSError as exc:self.automatic_diagnostic_error=str(exc)

    def _observe(self,ctx,body):
        op=body[:2];flow=ctx['flow']
        if flow in self.blocked:return
        if flow==self.flow:self.last_clock_us=max(self.last_clock_us,ctx['clock_us'])
        if op==b'\x33\x36':
            from live_core import self_identity
            try:entry=self_identity(body)
            except (ValueError,UnicodeError):return
            if flow==self.flow and self.identity and self.identity.get('process_id'):
                entry=dict(entry,process_id=self.identity['process_id'])
            if entry['name']!=self.character:
                if flow==self.flow:
                    self.invalidate(flow,'Personagem mudou · reinicie a captura')
                    self.scope=None;self.entries={};self.seed=[];self.initial=None;self.level_updates={};self.soul_sources={}
                    self._clear_session_counts()
                self.pending.pop(flow,None);self.pending_incomplete.discard(flow);return
            promotion=bool(self.flow==flow and self.identity and
                self.identity.get('basis')=='captured_game_process' and self.identity.get('actor') is None)
            if promotion:
                # The first own actor confirms the existing local process
                # binding; it is not proof that these receipts were another
                # character's. Known actor changes remain a reset boundary.
                entry=dict(entry,process_id=self.identity['process_id'])
                self.identity=entry
                if self.scope is not None:
                    self.scope=self.identity_scope()
                    for previous in self.entries.values():previous['account_scope']=list(self.scope)
            if self.flow and not promotion and (flow!=self.flow or any(entry[k]!=self.identity[k] for k in ('name','actor','server'))):
                old_flow=self.flow
                candidate=self.login_base
                replay=(candidate['messages'] if candidate and candidate['flow']==flow and
                    0<=ctx['clock_us']-candidate['clock_us']<=2000000 else [])
                # A new own identity on a healthy, unchanged physical flow is
                # a new character session, not a TCP failure. Do not blacklist
                # that socket and silently discard every subsequent soul.
                if old_flow!=flow:self.invalidate(old_flow,'Conexão mudou · aguardando coleção inicial')
                self.scope=None;self.entries={};self.events=set();self.event_order=deque();self.sequence=0
                self.initial=None;self.initial_digest=None;self.seed=[]
                self.raw_souls={};self.transitions={};self.level_updates={};self.soul_sources={}
                self._clear_session_counts()
                self.problem='Personagem reconectado · aguardando coleção inicial'
                self.flow=flow;self.identity=entry;self.login_base=None
                for context,message in replay:self._apply_message(context,message)
            self.flow=flow;self.identity=entry
            incomplete=flow in self.pending_incomplete
            queued=self.pending.pop(flow,[])
            if not incomplete:
                for context,message in queued:self._apply_message(context,message)
            elif not self.initial:
                self.problem='Coleção inicial incompleta'
                self.session_incomplete=True;self.session_detail='Parte das almas anteriores não pôde ser registrada'
            # Other sockets must never supply this character's collection.
            self.pending.clear();self.pending_incomplete.clear();return
        if op not in (b'\x00\x90',b'\x0d\x90',b'\x09\x90',b'\x14\x90',b'\x0e\x90'):return
        self.serial+=1
        context=dict(ctx,event_serial=self.serial)
        if flow==self.flow:
            if op==b'\x00\x90':
                try:
                    initial_collection(body)
                except ValueError:self.login_base=None
                else:self.login_base=dict(flow=flow,clock_us=ctx['clock_us'],messages=[(context,bytes(body))],bytes=len(body))
            elif self.login_base and 0<=ctx['clock_us']-self.login_base['clock_us']<=2000000:
                if len(self.login_base['messages'])<256 and self.login_base['bytes']+len(body)<=1000000:
                    self.login_base['messages'].append((context,bytes(body)));self.login_base['bytes']+=len(body)
                else:self.login_base=None
            elif self.login_base:self.login_base=None
        if flow==self.flow:self._apply_message(context,body)
        elif self.flow is None or self.flow in self.blocked:
            if flow in self.pending_incomplete:self.discarded+=1;return
            if len(self.pending)+len(self.pending_incomplete)>=32 and flow not in self.pending:
                self.discarded+=1;return
            queue=self.pending.setdefault(flow,[])
            if len(queue)>=256:
                # Losing this semantic queue does not imply a TCP gap. Keep
                # the healthy transport eligible for future exact receipts.
                self.discarded+=len(queue)+1;self.pending.pop(flow,None);self.pending_incomplete.add(flow)
                self.problem='Coleção inicial incompleta';return
            queue.append((context,bytes(body)))

    def invalidate(self,flow,reason='Captura interrompida · contagem preservada'):
        self.blocked.add(flow);self.pending.pop(flow,None);self.pending_incomplete.discard(flow)
        if flow==self.flow:
            self.problem=reason;self.session_incomplete=True;self.session_detail=reason;self.account_gap=True
            self._report_fault()

    def rejection_samples(self):
        return [dict(sample) for sample in self.rejected_messages]

    def _remember_rejection(self,ctx,body,reason):
        # Keep evidence even when the user starts a diagnostic after the fault.
        # Bounded memory only; payload is exported locally on request/session save.
        if len(self.rejected_messages)>=16:del self.rejected_messages[4]
        self.rejected_messages.append(dict(flow=ctx['flow'],clock_us=ctx['clock_us'],
            packet=ctx.get('packet'),event_serial=ctx['event_serial'],
            opcode=body[:2].hex(),bytes=len(body),body_hex=body[:512].hex(),
            truncated=len(body)>512,reason=str(reason)))

    def _apply_message(self,ctx,body):
        self.last_clock_us=max(self.last_clock_us,ctx['clock_us'])
        try:
            if body[:2]==b'\x00\x90':
                parsed=initial_collection(body);digest=hashlib.sha256(body).hexdigest()
                balances={**parsed['soul_defaults'],**parsed['souls']}
                sources={str(key):'0090-explicit' for key in parsed['souls']}
                sources.update({str(key):parsed['soul_default_source'] for key in parsed['soul_defaults']})
                # A fresh absolute table can repair a semantic rejection even
                # when its bytes equal the previous base. Do not skip recovery.
                repairing=bool(self.account_gap or self.unresolved_species or
                    any(e.get('sync_pending') for e in self.entries.values()))
                if digest==self.initial_digest and not repairing:return
                # A repeated/older initial notification cannot roll back a soul
                # update that has already been observed on this connection.
                for identifier,previous in self.entries.items():
                    level=parsed['levels'].get(int(identifier));souls=balances.get(int(identifier))
                    if level is not None and (level<previous['level'] or level==previous['level'] and souls is not None and souls<previous['souls']):
                        raise ValueError('Lista antiga de mascotes ignorada')
                for identifier,previous_level in self.level_updates.items():
                    incoming=parsed['levels'].get(int(identifier))
                    if incoming is not None and incoming<previous_level:
                        raise ValueError('Lista antiga de mascotes ignorada')
                # Also protect balances for species absent from the level map.
                # They have no entry above, but their received souls are real.
                for identifier,previous_souls in self.raw_souls.items():
                    incoming=balances.get(int(identifier))
                    old_level=self.entries.get(identifier,{}).get('level',self.initial['levels'].get(int(identifier)) if self.initial else None)
                    new_level=parsed['levels'].get(int(identifier))
                    advanced=new_level is not None and (old_level is None or new_level>old_level)
                    if incoming is not None and incoming<previous_souls and not advanced:
                        raise ValueError('Lista antiga de mascotes ignorada')
                scope=self.identity_scope()
                for identifier in sorted(parsed['levels'].keys() & balances.keys()):
                    level=parsed['levels'][identifier]
                    meta=CATALOG.get(identifier) or dict(name=self.NAMES.get(identifier,f'Mascote {identifier}'),icon_key=None)
                    event=dict(event_id=f"{ctx['event_serial']}:{identifier}",pet_id=str(identifier),
                        name=meta['name'],icon_key=meta['icon_key'],level=level,
                        souls=balances[identifier],next_required={1:25,2:75,3:0}[level],kind='snapshot',
                        soul_baseline_source=sources[str(identifier)])
                    self.apply(scope,event)
                self.initial=parsed;self.initial_digest=digest;self.problem=None
                self.account_gap=False;self.unresolved_species.clear()
                self.raw_souls={str(k):v for k,v in balances.items()};self.transitions={};self.soul_sources=sources
                self.level_updates={key:level for key,level in self.level_updates.items()
                    if int(key) not in parsed['levels']}
                complete=parsed['levels'].keys() & balances.keys()
                for key,entry in self.entries.items():
                    if int(key) in complete:
                        entry.pop('sync_pending',None);entry.pop('sync_pending_us',None)
                    else:
                        entry['sync_pending']=True;entry.setdefault('sync_pending_us',ctx['clock_us'])
                # Login establishes the unlocked roster. Actual receipts can
                # change its recent order, but cannot replace that roster.
                self.seed=[]
            elif body[:2] in (b'\x09\x90',b'\x14\x90'):
                from inspect_wire import varint
                if body[:2]==b'\x09\x90':
                    if self.identity.get('actor') is None:return
                    actor,at=varint(body,2)
                    if actor!=self.identity['actor']:return
                else:
                    if body[2:4]!=b'\x00\x00':return
                    at=4
                if len(body)!=at+21:return
                identifier=str(int.from_bytes(body[at:at+4],'little'))
                if not 0<int(identifier)<1000000:raise ValueError('Identificador de mascote inválido')
                level=int.from_bytes(body[at+4:at+8],'little');entry=self.entries.get(identifier)
                previous_level=entry['level'] if entry else self.level_updates.get(identifier,
                    self.initial['levels'].get(int(identifier)) if self.initial else None)
                if self.initial and previous_level==2 and level==3:
                    # The own stage-3 notification is observed. Its following
                    # soul-consumption profile is not yet qualified; keep only
                    # the confirmed maximum level, never the prior-stage balance.
                    self.level_updates[identifier]=3
                    if entry and entry.get('sync_pending') and not entry.get('awaiting_growth'):
                        self.unresolved_species.add(identifier)
                    self.entries.pop(identifier,None);self.raw_souls.pop(identifier,None)
                    self.transitions.pop(identifier,None)
                    if not self.account_gap and not self.unresolved_species and not any(e.get('sync_pending') for e in self.entries.values()):
                        self.problem=None
                    return
                # Only the unlock and 1 -> 2 shapes observed in the confirmed
                # 193822 sample. Never treat another player's equipment as ours.
                expected=(1,5) if previous_level is None else (2,25) if previous_level==1 else None
                if (self.initial and previous_level is not None and previous_level<level<=3
                        and (not expected or level!=expected[0])):
                    # The stage change is visible, but its consumption profile
                    # is not validated. Stop presenting the old level as exact.
                    if entry:
                        entry['sync_pending']=True;entry.setdefault('sync_pending_us',ctx['clock_us'])
                    else:
                        self.unresolved_species.add(identifier)
                        self.transitions[identifier]=dict(level=previous_level,consume=None,clock_us=ctx['clock_us'])
                    self.problem='Evolução sem saldo confirmado · aguardando coleção inicial'
                    self._remember_rejection(ctx,body,self.problem);return
                if not self.initial or not expected or level!=expected[0]:return
                # Own level notifications provide a level independently of the
                # separate initial soul map. Preserve that observed metadata,
                # but never turn a missing prior balance into zero.
                if not entry:
                    roster=set(map(str,self.initial['levels']))|set(self.entries)|set(self.level_updates)|set(self.session_counts)
                    if identifier not in roster and len(roster)>=500:
                        raise ValueError('Limite de coleção atingido')
                    self.level_updates[identifier]=level
                self.transitions[identifier]=dict(level=level,consume=expected[1],clock_us=ctx['clock_us'])
                if entry:
                    entry['sync_pending']=True;entry.setdefault('sync_pending_us',ctx['clock_us'])
                    self.problem='Confirmando evolução da mascote'
            elif body[:2]==b'\x0e\x90':
                if len(body)!=11 or body[2]!=1:return
                identifier=str(int.from_bytes(body[3:7],'little'));transition=self.transitions.get(identifier)
                if not transition:
                    if self.entries.get(identifier,{}).get('sync_pending'):
                        self._remember_rejection(ctx,body,'Consumo recebido durante evolução não confirmada')
                    return
                amount=int.from_bytes(body[7:11],'little');raw=self.raw_souls.get(identifier)
                if amount!=transition['consume'] or not 0<=ctx['clock_us']-transition['clock_us']<=2000000:
                    if identifier in self.entries:self.entries[identifier]['sync_pending']=True
                    self.problem='Evolução incompleta · saldo da conta indisponível';return
                if raw is None:
                    # The paired receipt confirms the observed stage, while its
                    # absolute soul balance remains unavailable in both maps.
                    self.transitions.pop(identifier,None)
                    return
                if raw<amount:
                    if identifier in self.entries:self.entries[identifier]['sync_pending']=True
                    self.problem='Evolução incompleta · saldo da conta indisponível';return
                level=transition['level'];souls=raw-amount
                if souls>{1:25,2:75}[level]:raise ValueError('Saldo de evolução fora do perfil')
                meta=CATALOG.get(identifier) or dict(name=f'Mascote {identifier}',icon_key=None)
                event=dict(event_id=str(ctx['event_serial']),pet_id=identifier,name=meta['name'],icon_key=meta['icon_key'],
                    level=level,souls=souls,next_required={1:25,2:75}[level],kind='collection',
                    soul_baseline_source=self.soul_sources.get(identifier,'0090-explicit'))
                self.apply(self.identity_scope(),event);self.raw_souls[identifier]=souls
                self.transitions.pop(identifier,None)
                self.level_updates.pop(identifier,None)
                self.entries[identifier].pop('sync_pending',None)
                self.entries[identifier].pop('sync_pending_us',None)
                if not any(e.get('sync_pending') for e in self.entries.values()):self.problem=None
            else:
                for index,(identifier,amount) in enumerate(self._count_session_soul(ctx,body)):
                    entry=self.entries.get(identifier)
                    if not self.initial or identifier not in self.raw_souls:
                        # The receipt is complete even when the prior account
                        # balance is unknown. Do not manufacture a zero baseline.
                        continue
                    if not entry:
                        self.raw_souls[identifier]+=amount;continue # Locked pet has no level; await paired unlock.
                    if entry.get('sync_pending'):
                        # A confirmed growth message can be followed by another
                        # receipt before its paired consumption. Preserve that
                        # received carry while the level update remains pending.
                        if identifier in self.transitions or entry.get('awaiting_growth'):self.raw_souls[identifier]+=amount
                        continue
                    if entry['level']==3:continue
                    self.raw_souls[identifier]+=amount
                    if self.raw_souls[identifier]>entry['next_required']:
                        entry['sync_pending']=True;entry.setdefault('sync_pending_us',ctx['clock_us'])
                        entry['awaiting_growth']=True
                        self.problem='Etapa mudou · aguardando confirmação da evolução';continue
                    event={k:entry[k] for k in ('pet_id','name','level','souls','next_required','icon_key')}
                    event['soul_baseline_source']=self.soul_sources.get(identifier,'0090-explicit')
                    event.update(event_id=f"{ctx['event_serial']}:{index}",kind='collection',souls=entry['souls']+amount)
                    self.apply(self.scope,event)
        except ValueError as exc:
            self.discarded+=1;self.problem=str(exc)
            self._remember_rejection(ctx,body,exc)
            if body[:2]==b'\x0d\x90':
                self.session_incomplete=True;self.session_detail=str(exc)
                identifier=str(int.from_bytes(body[3:7],'little')) if len(body)==11 and body[2]==1 else None
                if identifier is not None and not 0<int(identifier)<1000000:identifier=None
                if identifier is None:self.account_gap=True
                else:self.unresolved_species.add(identifier)
                # A known one-entry envelope affects this species only. An
                # unrecognized envelope cannot safely identify its scope.
                for key,entry in self.entries.items():
                    if identifier is None or key==identifier:
                        entry['sync_pending']=True;entry.setdefault('sync_pending_us',ctx['clock_us'])

    def snapshot(self):
        state=super().snapshot()
        sessions=sorted(self.session_counts.values(),key=lambda e:e['recent'],reverse=True)
        # Keep each unlocked login species visible after the first soul receipt.
        # A soul-only initial record can belong to a still locked species, so it
        # joins the list only after an actual receipt or confirmed unlock.
        login_keys=[str(key) for key in self.initial['levels']] if self.initial else []
        keys=list(dict.fromkeys([e['pet_id'] for e in sessions]+login_keys+list(self.entries)+list(self.level_updates)))
        # This is a character/connection boundary, NOT a decoded account ID.
        rows=[]
        for key in keys[:500]:
            receipt=self.session_counts.get(key)
            if key in self.entries:row=dict(self.entries[key])
            else:
                meta=CATALOG.get(key) or dict(name=f'Mascote {key}',icon_key=None)
                row=dict(receipt or dict(pet_id=key,name=meta['name'],icon_key=meta['icon_key'],recent=0),
                    level=None,souls=None,next_required=None,
                    remaining_to_three=None,remaining_to_next_level=None)
            # Independent maps can legitimately omit a species' level or balance.
            # Preserve each observed field without inventing the other or asking
            # for another login after a valid collection has already arrived.
            metadata_missing=bool(self.initial and key not in self.entries)
            if metadata_missing:
                row['level']=self.level_updates.get(key,self.initial['levels'].get(int(key)))
                row['souls']=self.raw_souls.get(key)
                row['next_required']={1:25,2:75,3:0}.get(row['level'])
                if key in self.transitions and (key in self.raw_souls or key in self.unresolved_species):
                    row['sync_pending']=True
                    row['sync_pending_us']=self.transitions[key]['clock_us']
            if key in self.unresolved_species:row['sync_pending']=True
            row['soul_baseline_source']=self.soul_sources.get(key,row.get('soul_baseline_source'))
            row['max_level_confirmed']=bool(row.get('level')==3 and self.initial and
                not self.account_gap and key not in self.unresolved_species and not row.get('sync_pending') and
                self.identity and self.flow not in self.blocked)
            row['metadata_missing']=metadata_missing
            row['soul_balance_known']=bool(row.get('souls') is not None and self.initial and
                not self.account_gap and key not in self.unresolved_species and not row.get('sync_pending'))
            row['account_scope']=None
            row['session_souls']=receipt['session_souls'] if receipt else 0
            row['counter_mode']='session'
            row['account_count_known']=bool(key in self.entries and
                not row.get('sync_pending') and not self.account_gap)
            row['account_reading_complete']=row['account_count_known']
            row['session_count_complete']=not self.session_incomplete
            rows.append(row)
        active=bool(self.identity and self.flow not in self.blocked)
        overdue=any(row.get('sync_pending') and self.last_clock_us-
            row.get('sync_pending_us',self.session_counts.get(row['pet_id'],{}).get('first_clock_us',self.last_clock_us))>=2000000 for row in rows)
        needs_relog=bool(self.account_gap or (sessions and not self.initial) or overdue)
        partial=self.session_incomplete
        missing_metadata=any(row.get('metadata_missing') and not row.get('max_level_confirmed') for row in rows)
        state.update(entries=rows,account_scope=None,
            status='partial' if partial else 'synced' if active or self.initial else 'unavailable',
            detail=self.session_detail or ('Almas coletadas nesta captura' if sessions else
                'Aguardando coleta de almas' if active else 'Ative ON para contar almas coletadas'),
            account_status='partial' if self.problem or missing_metadata else 'synced' if self.initial else 'unavailable',
            account_detail=self.problem or ('Coleção recebida; nível ou saldo não informado para algumas espécies' if missing_metadata else 'Coleção inicial recebida' if self.initial else 'Saldo anterior da conta não recebido'),
            counter_mode='session',hud_display_mode='account_progress',
            session_collected_total=sum(e['session_souls'] for e in self.session_counts.values()),
            session_count_complete=not self.session_incomplete,
            relog_required=needs_relog,
            synchronization_state='relog_required' if needs_relog else 'metadata_partial' if missing_metadata else 'ready' if self.initial else 'waiting',
            session_counts={key:dict(value) for key,value in self.session_counts.items()},
            account_count_known=bool(self.initial and not self.account_gap and
                all(row.get('account_count_known') for row in rows)),
            account_progress_complete=bool(self.initial and not self.account_gap and not missing_metadata and
                not any(row.get('sync_pending') for row in rows)),
            wire_decoder_validated=bool(self.initial),decoder_profile='0090/0d90-batch-1-2-entries/paired-0990-1490-0e90-experimental',
            character_scope=dict(self.identity,flow=self.flow) if self.identity else None,
            character_identity_wire_verified=bool(self.identity and self.identity.get('basis')!='captured_game_process'),
            account_identifier_decoded=False,known_species=list(self.NAMES),discarded=self.discarded,
            sparse_zero_profile_validated=bool(self.initial and self.initial.get('sparse_zero_profile_validated')),
            soul_default_source=self.initial.get('soul_default_source') if self.initial else None,
            automatic_diagnostic_error=self.automatic_diagnostic_error,catalog_entries=len(CATALOG.entries),catalog_errors=list(CATALOG.errors),
            initial_entries=len(self.entries),unsupported_tail_bytes=self.initial['unsupported_tail_bytes'] if self.initial else None)
        return state


class CollectionDiagnostic:
    # Exclude already understood combat/position noise; record unknown messages
    # only after the user explicitly starts a short local diagnostic.
    EXCLUDED={bytes.fromhex(x) for x in ('0036','0438','0538','4136','1b92','008d','048d','3336','4536','0297')}
    MAX_MESSAGES=4096;MAX_BYTES=4*1024*1024;MAX_BODY=65536
    PER_OPCODE=96;KEEP_FIRST=8
    DEFAULT_DURATION_SECONDS=600;MAX_DURATION_SECONDS=600
    TRANSPORT_MAX_MESSAGES=65536;TRANSPORT_MAX_BYTES=32*1024*1024
    # A quiet single connection must be allowed to use the global allowance.
    # Ten-minute diagnostics need room for idle world ticks in both directions.
    TRANSPORT_PER_FLOW=TRANSPORT_MAX_MESSAGES;TRANSPORT_KEEP_FIRST=128;TRANSPORT_MAX_FLOWS=64
    def __init__(self):self.reset()
    def reset(self):
        self.until=0;self.records=[];self.bytes=0;self.seen=set();self.dropped=0;self.counts={}
        self.groups={};self.serial={};self.evicted=0;self.duplicates=0;self.excluded=0
        self.started_utc=None;self.deadline_utc=None;self.finished_utc=None;self.duration_seconds=0
        self.first_clock_us=None;self.last_clock_us=None;self.received=0
        self.transport=[];self.transport_groups={};self.transport_counts={};self.transport_bytes=0
        self.transport_observed=0;self.transport_evicted=0;self.transport_dropped=0;self.transport_invalid=0
    def start(self,seconds=DEFAULT_DURATION_SECONDS):
        self.reset();self.duration_seconds=max(1,min(self.MAX_DURATION_SECONDS,seconds));self.until=time.monotonic()+self.duration_seconds
        started=datetime.now(timezone.utc);self.started_utc=started.isoformat()
        self.deadline_utc=(started+timedelta(seconds=self.duration_seconds)).isoformat()
    def stop(self):
        self.until=0
        if self.started_utc:self.finished_utc=datetime.now(timezone.utc).isoformat()
    @property
    def active(self):return time.monotonic()<self.until
    @property
    def remaining_seconds(self):return max(0,math.ceil(self.until-time.monotonic()))
    def observe_transport(self,row,generation):
        """Explicit bounded evidence before protocol acceptance; no semantic decoding.

        Identical payloads remain separate TCP observations. Existing process/socket
        filters remain the capture boundary, and no additional traffic is requested.
        """
        if not self.active:return
        self.transport_observed+=1
        try:
            payload=row['tcp.payload'].replace(':','')
            if not payload or len(payload)%2:raise ValueError('Invalid hex size')
            raw=bytes.fromhex(payload[:self.MAX_BODY*2])
            source=row['ip.src'] or row['ipv6.src'];dest=row['ip.dst'] or row['ipv6.dst']
            source_port=int(row['tcp.srcport']);dest_port=int(row['tcp.dstport'])
            packet=int(row['frame.number']);stream=int(row['tcp.stream']);seq=int(row['tcp.seq_raw'])
            epoch=str(row['frame.time_epoch'])
            if len(epoch)>50 or not 0<=float(epoch)<1e12:raise ValueError('Invalid timestamp')
            if not source or not dest or not 1<=source_port<=65535 or not 1<=dest_port<=65535 or packet<0 or stream<0 or not 0<=seq<2**32:
                raise ValueError('Invalid TCP context')
            fields=(str(generation),str(stream),source,str(source_port),dest,str(dest_port))
            if any(len(x)>100 for x in fields):raise ValueError('Oversized context')
            flow='|'.join(fields)
        except (KeyError,TypeError,ValueError,AttributeError):
            self.transport_invalid+=1;return
        if flow not in self.transport_counts and len(self.transport_counts)>=self.TRANSPORT_MAX_FLOWS:
            self.transport_dropped+=1;return
        self.transport_counts[flow]=self.transport_counts.get(flow,0)+1
        group=self.transport_groups.setdefault(flow,[])
        if len(group)>=self.TRANSPORT_PER_FLOW:self.remove_transport(group[self.TRANSPORT_KEEP_FIRST])
        while self.transport and (len(self.transport)>=self.TRANSPORT_MAX_MESSAGES or self.transport_bytes+len(raw)>self.TRANSPORT_MAX_BYTES):
            crowded=max(self.transport_groups.values(),key=len)
            victim=crowded[self.TRANSPORT_KEEP_FIRST] if len(crowded)>self.TRANSPORT_KEEP_FIRST else crowded[-1]
            self.remove_transport(victim)
        if self.transport_bytes+len(raw)>self.TRANSPORT_MAX_BYTES:
            self.transport_dropped+=1
            if not self.transport_groups.get(flow):self.transport_groups.pop(flow,None)
            return
        record=dict(flow=flow,generation=str(generation),packet=packet,frame_time_epoch=epoch,
                    tcp_stream=stream,source=source,source_port=source_port,destination=dest,destination_port=dest_port,
                    tcp_seq_raw=seq,tcp_syn=row.get('tcp.flags.syn',''),tcp_fin=row.get('tcp.flags.fin',''),
                    tcp_reset=row.get('tcp.flags.reset',''),payload_bytes=len(payload)//2,payload_hex=raw.hex(),
                    truncated=len(raw)*2!=len(payload),sample_number=self.transport_counts[flow],
                    protocol_semantics_validated=False)
        self.transport.append(record);self.transport_groups.setdefault(flow,[]).append(record);self.transport_bytes+=len(raw)

    def remove_transport(self,record):
        self.transport.remove(record);group=self.transport_groups[record['flow']];group.remove(record)
        if not group:self.transport_groups.pop(record['flow'])
        self.transport_bytes-=len(record['payload_hex'])//2;self.transport_evicted+=1

    def observe(self,ctx,body):
        if not self.active:return
        self.received+=1
        clock=ctx['clock_us'];self.first_clock_us=clock if self.first_clock_us is None else min(self.first_clock_us,clock)
        self.last_clock_us=clock if self.last_clock_us is None else max(self.last_clock_us,clock)
        if body[:2] in self.EXCLUDED:self.excluded+=1;return
        op=body[:2].hex();self.counts[op]=self.counts.get(op,0)+1
        digest=hashlib.sha256(body).hexdigest()
        if digest in self.seen:self.duplicates+=1;return
        raw=body[:self.MAX_BODY]
        values=self.groups.setdefault(op,[])
        if len(values)>=self.PER_OPCODE:self.remove(values[self.KEEP_FIRST])
        while self.records and (len(self.records)>=self.MAX_MESSAGES or self.bytes+len(raw)>self.MAX_BYTES):
            # Evict an old sample from a frequent opcode before a sole rare one.
            groups=list(self.groups.values())
            crowded=max(groups,key=lambda g:len(g))
            if len(crowded)>self.KEEP_FIRST:victim=crowded[self.KEEP_FIRST]
            elif len(crowded)>1:victim=crowded[-1]
            else:victim=max(self.records,key=lambda r:min(r['bytes'],self.MAX_BODY))
            self.remove(victim)
        if self.bytes+len(raw)>self.MAX_BYTES:self.dropped+=1;return
        self.seen.add(digest);self.bytes+=len(raw)
        self.serial[op]=self.serial.get(op,0)+1
        record=dict(ctx,opcode=op,bytes=len(body),body_hex=raw.hex(),truncated=len(raw)!=len(body),
                    sample_number=self.serial[op],_digest=digest,_stored_bytes=len(raw))
        self.records.append(record);self.groups.setdefault(op,[]).append(record)

    def remove(self,record):
        self.records.remove(record);values=self.groups[record['opcode']];values.remove(record)
        if not values:self.groups.pop(record['opcode'])
        self.seen.remove(record['_digest']);self.bytes-=record['_stored_bytes'];self.evicted+=1

    def snapshot(self):return dict(schema_version=3,active=self.active,remaining_seconds=self.remaining_seconds,
        messages=[{k:v for k,v in r.items() if not k.startswith('_')} for r in self.records],opcode_counts=dict(self.counts),
        dropped=self.dropped,bytes=self.bytes,pet_progress_validated=False,
        sampling_policy='first-eight-and-recent-per-opcode-with-global-bounds',sampling_lossless=False,
        limits=dict(messages=self.MAX_MESSAGES,payload_bytes=self.MAX_BYTES,body_bytes=self.MAX_BODY,
                    per_opcode=self.PER_OPCODE,keep_first=self.KEEP_FIRST),
        samples_replaced=self.evicted,duplicate_messages=self.duplicates,excluded_messages=self.excluded,
        messages_observed=self.received,first_observed_clock_us=self.first_clock_us,last_observed_clock_us=self.last_clock_us,
        started_utc=self.started_utc,deadline_utc=self.deadline_utc,finished_utc=self.finished_utc,duration_seconds=self.duration_seconds,
        transport_samples=[dict(r) for r in self.transport],transport_observed=self.transport_observed,
        transport_payload_bytes=self.transport_bytes,transport_flow_counts=dict(self.transport_counts),
        transport_samples_replaced=self.transport_evicted,transport_dropped=self.transport_dropped,
        transport_invalid=self.transport_invalid,transport_sampling_lossless=False,
        transport_sampling_policy='first-128-and-recent-per-direction-with-global-bounds-no-content-deduplication',
        transport_limits=dict(messages=self.TRANSPORT_MAX_MESSAGES,payload_bytes=self.TRANSPORT_MAX_BYTES,
                              body_bytes=self.MAX_BODY,per_flow=self.TRANSPORT_PER_FLOW,
                              keep_first=self.TRANSPORT_KEEP_FIRST,flows=self.TRANSPORT_MAX_FLOWS),
        transport_scope='existing game-process TCP connection filters; both directions; before protocol acceptance',
        transport_network_lossless=False)
