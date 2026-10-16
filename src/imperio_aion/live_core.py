# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Incremental, bounded reader and per-target encounter arithmetic.

Own implementation. Observed protocol subset remains experimental.
No search for damage signatures, post-acquisition resynchronization, or cross-flow
sums. A newly attached TCP stream may acquire framing from a heartbeat chain.
"""
from collections import Counter, deque
from decimal import Decimal
from dataclasses import dataclass, field
import json
from pathlib import Path

from inspect_wire import varint, expand_bundles
from probe_amounts import record
from analisar_aion2 import identity, resolve_identity, periodic, skill_label
from group_core import GroupBook, trailing_impact

FIELDS = ['frame.number', 'frame.time_epoch', 'tcp.stream', 'ip.src', 'ip.dst',
          'ipv6.src', 'ipv6.dst', 'tcp.srcport', 'tcp.dstport', 'tcp.seq_raw',
          'tcp.len', 'tcp.payload', 'tcp.flags.syn', 'tcp.flags.fin', 'tcp.flags.reset']
PROFILE = json.loads(Path(__file__).with_name('perfil-sa-experimental.json').read_text(encoding='utf-8-sig'))


def microseconds(value):
    return int((Decimal(str(value)) * 1000000).to_integral_value())


def self_identity(body):
    """Bounded experimental 33 36 name header. No placeholder or name scan."""
    if body[:2] != b'\x33\x36':
        raise ValueError('Not a self-name message')
    actor, at = varint(body, 2)
    at += 4  # fixed mask word
    if not 0 < actor < 10000000 or at + 2 > len(body) or not body[at] & 1:
        raise ValueError('Unsupported self-name header')
    size = body[at+1]
    at += 2
    if not 0 < size <= 36 or at + size + 2 > len(body):
        raise ValueError('Truncated self-name header')
    name = body[at:at+size].decode('utf-8', 'strict')
    server = int.from_bytes(body[at+size:at+size+2], 'little')
    if not name or any(ord(c) < 32 for c in name) or not 1000 <= server < 3000:
        raise ValueError('Unsupported self-name/server field')
    return dict(actor=actor, name=name, server=server, basis='experimental 33 36 name header')


class Direction:
    """Reassemble TCP once; bounded reorder wait and verified duplicate overlap."""
    ACQUISITION_BYTES = 1_000_000
    ACQUISITION_CANDIDATES = 2048
    ACQUISITION_STEPS = 200_000
    ACQUISITION_DECODED_BYTES = 8_000_000

    def __init__(self, allow_midstream=False):
        self.base = None
        self.next = 0
        self.history = bytearray()
        self.pending = []
        self.gap_since = None
        self.buf = bytearray()
        self.offset = 0
        self.spans = deque()
        self.index = 0
        self.heartbeats = 0
        self.accepted = False
        self.failed = None
        self.initial = []
        self.initial_bytes = 0
        self.duplicate_bytes = 0
        # Only a new, not-yet-recognized direction may acquire its first frame.
        # Once accepted, every gap/framing/overlap failure remains terminal.
        self.allow_midstream = allow_midstream
        self.midstream_skipped_bytes = 0
        self._acquisition_scan = 1
        self._acquisition_paths = [dict(start=0, cursor=0, heartbeats=0)]
        self._acquisition_starts = {0}
        self._acquisition_cache = {}
        self._acquisition_steps = 0
        self._acquisition_decoded_bytes = 0
        self._acquisition_compressed_until = 0

    def fail(self, reason):
        self.failed = reason
        self.buf.clear(); self.pending.clear(); self.initial.clear(); self.spans.clear()
        self._clear_acquisition()
        raise ValueError(reason)

    def _clear_acquisition(self):
        self._acquisition_paths.clear()
        self._acquisition_starts.clear()
        self._acquisition_cache.clear()

    def tick(self, now_us):
        if self.gap_since is not None and now_us - self.gap_since > 2000000:
            self.fail('Lacuna TCP por mais de 2 s; fluxo interrompido.')

    def feed(self, seq, payload, context):
        if self.failed:
            return []
        if self.base is None:
            self.base = seq
        # Unwrap against the current cursor, not the connection's first byte.
        # The old signed offset went negative after 2 GiB and dropped new data.
        expected_seq = (self.base + self.next) % 2**32
        start = self.next + (seq - expected_seq + 2**31) % 2**32 - 2**31
        if start < 0:
            cut = min(-start, len(payload)); start += cut; payload = payload[cut:]
        if not payload:
            return []
        self.pending.append((start, payload, context))
        self.pending.sort(key=lambda item: item[0])
        if len(self.pending) > 256 or sum(len(p[1]) for p in self.pending) > 1000000:
            self.fail('Limite de reordenação TCP atingido.')
        output = []
        while self.pending and self.pending[0][0] <= self.next:
            start, block, ctx = self.pending.pop(0)
            overlap = min(len(block), self.next-start)
            if overlap:
                history_start = self.next-len(self.history)
                if start < history_start:
                    self.fail('Retransmissão fora da janela verificável.')
                begin = start-history_start
                if self.history[begin:begin+overlap] != block[:overlap]:
                    self.fail('Bytes conflitantes em retransmissão TCP.')
                self.duplicate_bytes += overlap
            fresh = block[overlap:]
            if fresh:
                self.spans.append((self.next, self.next+len(fresh), ctx))
                self.next += len(fresh)
                self.history.extend(fresh)
                if len(self.history) > 262144:
                    del self.history[:-262144]
                self.buf.extend(fresh)
                if self.allow_midstream and not self.accepted:
                    output.extend(self.acquire_frames())
                else:
                    output.extend(self.frames())
        if self.pending:
            if self.gap_since is None:
                self.gap_since = context['clock_us']
        else:
            self.gap_since = None
        self.tick(context['clock_us'])
        return output

    def _acquisition_frame(self, cursor):
        """Probe framing only; never emit/attribute an unverified damage record."""
        cached = self._acquisition_cache.get(cursor)
        if cached is not None:
            return cached
        if cursor >= len(self.buf):
            return None
        if self.buf[cursor] == 0:
            result = (cursor + 1, 0)
        else:
            try:
                size, body_start = varint(self.buf, cursor)
            except ValueError as exc:
                if str(exc) == 'Incomplete varint':
                    return None
                self._acquisition_cache[cursor] = False
                return False
            if not 6 <= size <= 65539:
                self._acquisition_cache[cursor] = False
                return False
            end = body_start + size - 4
            if end > len(self.buf):
                return None
            counters = Counter()
            try:
                expanded = expand_bundles([(0, 0, bytes(self.buf[body_start:end]))], counters)
            except ValueError:
                self._acquisition_cache[cursor] = False
                return False
            self._acquisition_decoded_bytes += (counters['decoded_bytes'] +
                                               sum(len(logical) for _, _, logical in expanded))
            if self._acquisition_decoded_bytes > self.ACQUISITION_DECODED_BYTES:
                self.fail('Limite de expansão durante reconhecimento do fluxo.')
            result = (end, sum(logical[:2] == b'\x00\x36' for _, _, logical in expanded))
        self._acquisition_cache[cursor] = result
        return result

    def acquire_frames(self):
        """Acquire once from 20 contiguous, validly framed heartbeat messages.

        An arbitrary first TCP segment can start inside a frame. Candidate
        boundaries start at heartbeat/compressed headers or a completely
        validated initial counter/resource table, never at damage signatures.
        Retain an initial login that arrives before direction recognition;
        otherwise acquiring at its first heartbeat silently loses its balances.
        No message is delivered until its candidate
        chain passes the original 20-heartbeat direction-recognition threshold.
        Prefix bytes are discarded, not decoded or attributed. Initial aligned
        streams retain their original pre-heartbeat messages through path zero.
        """
        if len(self.buf) > self.ACQUISITION_BYTES:
            self.fail('Limite de dados sem enquadramento reconhecido; reinicie a captura.')
        # A framed compressed body is opaque until validated as a whole. Do not
        # choose heartbeat-looking bytes inside its still-unverified LZ4 data.
        try:
            size, body_start = varint(self.buf, 0)
            if (6 <= size <= 65539 and
                    self.buf[body_start:body_start + 2] == b'\xff\xff'):
                self._acquisition_compressed_until = max(self._acquisition_compressed_until,
                                                        body_start + size - 4)
        except ValueError:
            pass
        # Keep a suffix for a possibly fragmented uint32 header/compressed tag.
        # Each completed prefix is inspected once, so the byte scan is linear.
        stop = max(1, len(self.buf) - 10)
        for start in range(self._acquisition_scan, stop):
            if self.buf[start] == 0 or start < self._acquisition_compressed_until:
                continue
            try:
                size, body_start = varint(self.buf, start)
            except ValueError:
                continue
            op = self.buf[body_start:body_start + 2]
            if 6 <= size <= 65539 and op == b'\xff\xff':
                self._acquisition_compressed_until = body_start + size - 4
            if (6 <= size <= 65539 and op in (b'\x00\x36', b'\xff\xff', b'\x83\x56', b'\x0b\x61', b'\x33\x36', b'\x04\x8d') and
                    start not in self._acquisition_starts):
                if len(self._acquisition_starts) >= self.ACQUISITION_CANDIDATES:
                    self.fail('Limite de candidatos durante reconhecimento do fluxo.')
                self._acquisition_starts.add(start)
                self._acquisition_paths.append(dict(start=start, cursor=start, heartbeats=0,
                    login_anchor=bytes(op) if op in (b'\x83\x56', b'\x0b\x61', b'\x33\x36', b'\x04\x8d') else None))
        self._acquisition_scan = stop
        pending = []
        for path in self._acquisition_paths:
            while True:
                self._acquisition_steps += 1
                if self._acquisition_steps > self.ACQUISITION_STEPS:
                    self.fail('Limite de trabalho durante reconhecimento do fluxo.')
                item = self._acquisition_frame(path['cursor'])
                if item is False:
                    break
                if item is None:
                    pending.append(path)
                    break
                if path.get('login_anchor'):
                    # Opcode bytes alone are not an acquisition anchor. Apply
                    # the existing bounded decoder to the entire first table,
                    # including record ordering, exact length and extension.
                    # Publication still requires the normal same-flow login
                    # boundary, own identity, time window and settle guards.
                    _, body_start = varint(self.buf, path['start'])
                    body = bytes(self.buf[body_start:item[0]])
                    try:
                        if path['login_anchor'] == b'\x83\x56':
                            from order_accumulator import decode_initial_accumulated
                            decode_initial_accumulated(body)
                        elif path['login_anchor'] == b'\x0b\x61':
                            from activity_entries import decode_initial_entries
                            decode_initial_entries(body)
                        elif path['login_anchor'] == b'\x33\x36':
                            self_identity(body)
                        else:
                            info=identity(body)
                            if not 1000<=info['server']<3000:raise ValueError('Servidor fora do perfil')
                    except (ValueError, IndexError):
                        break
                    path['login_anchor'] = None
                path['cursor'], count = item
                path['heartbeats'] += count
                if path['heartbeats'] >= 20:
                    skipped = path['start']
                    self.midstream_skipped_bytes = skipped
                    if skipped:
                        del self.buf[:skipped]
                        self.offset += skipped
                    self._clear_acquisition()
                    return self.frames()
        self._acquisition_paths = pending
        return []

    def frames(self):
        ready = []
        while self.buf:
            if self.buf[0] == 0:
                width, body = 1, None
            else:
                try:
                    size, header = varint(self.buf, 0)
                except ValueError as exc:
                    if str(exc) == 'Incomplete varint':
                        break
                    self.fail('Cabeçalho de quadro inválido.')
                if not 6 <= size <= 65539:
                    self.fail('Enquadramento não reconhecido; reinicie a coleta.')
                width = header+size-4
                if len(self.buf) < width:
                    break
                body = bytes(self.buf[header:width])
            while self.spans and self.spans[0][1] <= self.offset:
                self.spans.popleft()
            ctx = dict(self.spans[0][2])
            del self.buf[:width]; self.offset += width
            if body is None:
                continue
            try:
                expanded = expand_bundles([(0, 0, body)], Counter())
            except ValueError:
                self.fail('Bloco comprimido inválido; fluxo interrompido.')
            for _, _, logical in expanded:
                self.index += 1
                item = (dict(ctx, message_index=self.index), logical)
                if logical[:2] == b'\x00\x36':
                    self.heartbeats += 1
                if self.accepted:
                    ready.append(item)
                else:
                    self.initial.append(item); self.initial_bytes += len(logical)
                    if self.initial_bytes > 1000000 or len(self.initial) > 10000:
                        self.fail('Direção sem assinatura reconhecida.')
                    if self.heartbeats >= 20:
                        self.accepted = True
                        ready.extend(self.initial); self.initial.clear()
        return ready

    def finish(self):
        if self.pending or self.buf:
            self.fail('Fim com quadro ou segmento incompleto.')


@dataclass
class Encounter:
    key: str
    flow: str
    target: int
    attempt: int
    start_us: int
    end_us: int | None = None
    end_reason: str | None = None
    partial: bool = False
    events: list = field(default_factory=list)

    def snapshot(self, now_us):
        end = self.end_us if self.end_us is not None else now_us
        elapsed = max(0, end-self.start_us)
        damage = sum(e['candidate_amount'] for e in self.events)
        average = damage/(elapsed/1e6) if elapsed else None
        window_us = min(5_000_000, elapsed)
        recent_damage = sum(e['candidate_amount'] for e in self.events
                            if e['clock_us'] <= end and
                            (e['clock_us'] > end-window_us or
                             (elapsed < 5_000_000 and e['clock_us'] == self.start_us)))
        current = recent_damage/(window_us/1e6) if window_us else None
        groups = {}
        for e in self.events:
            s = groups.setdefault(e['raw_skill'], dict(code=e['raw_skill'], name=e['skill_name'], damage=0, records=0))
            s['damage'] += e['candidate_amount']; s['records'] += 1
        return dict(key=self.key, flow=self.flow, target=self.target, attempt=self.attempt,
                    start_us=self.start_us, end_us=self.end_us, end_reason=self.end_reason,
                    duration_us=elapsed, damage=damage, dps=average,
                    dps_average=average, dps_current=current, dps_current_window_us=window_us,
                    records=len(self.events), partial=self.partial, dps_screen_validated=False,
                    skills=sorted(groups.values(), key=lambda s: -s['damage']))


class Meter:
    def __init__(self):
        self.encounters = []
        self.current = {}
        self.active_key = None
        self.ids = set()
        self.invalid_flows = set()
        self.births = {}

    def spawn(self, flow, target, now_us):
        self.births[(flow,target)] = now_us
        e=self.current.get((flow,target))
        if e and e.end_us is None and now_us>e.start_us:
            e.partial=True
            self.close(flow,target,now_us,'novo nascimento')

    def add(self, event):
        if event['id'] in self.ids:
            return
        if len(self.ids) >= 20000:
            raise ValueError('Limite de 20.000 registros; inicie outra sessão.')
        self.ids.add(event['id'])
        pair = event['flow'], event['target']
        encounter = self.current.get(pair)
        if encounter and encounter.end_us is not None and trailing_impact(encounter.end_reason,encounter.end_us,event['clock_us'],self.births.get(pair)):
            encounter.events.append(dict(event,late_after_death=True))
            return
        unconfirmed_life=bool(encounter and encounter.end_reason=='abate' and
                              not (self.births.get(pair) is not None and self.births[pair]>=encounter.end_us))
        if encounter is None or encounter.end_us is not None:
            attempt = 1 if encounter is None else encounter.attempt+1
            key = f"{event['flow']}/{event['target']}/{attempt}"
            encounter = Encounter(key, event['flow'], event['target'], attempt, event['clock_us'])
            self.current[pair] = encounter; self.encounters.append(encounter)
            if unconfirmed_life:encounter.partial=True
        encounter.events.append(event)
        self.active_key = encounter.key

    def close(self, flow, target, now_us, reason='abate'):
        e = self.current.get((flow, target))
        if e and e.end_us is None:
            e.end_us = max(e.start_us, now_us); e.end_reason = reason

    def interrupt(self, flow, now_us):
        for e in self.encounters:
            if e.flow == flow and e.end_us is None:
                e.partial = True
                e.end_us = max(e.start_us, now_us); e.end_reason = 'interrompido'

    def invalidate(self, flow):
        self.invalid_flows.add(flow)
        self.encounters = [e for e in self.encounters if e.flow != flow]
        self.current = {k:e for k,e in self.current.items() if e.flow != flow}
        self.active_key = self.encounters[-1].key if self.encounters else None


class LiveReader:
    def __init__(self, character='Zepidem', server=None, generation='1'):
        self.character = character
        self.server = server
        self.generation = generation
        self.origin_us = None
        self.clock_us = 0
        self.directions = {}
        self.evidence = {}
        self.bindings = {}
        self.pending_events = {}
        self.meter = Meter()
        self.rejected = 0
        self.packets = 0
        self.warnings = []
        self.group = GroupBook()
        self.damage_diagnostics = []
        self.damage_messages = []
        self.message_observer=None
        self.combat_floor_us=0

    def reset_combat(self,now):
        """Keep TCP framing and identities while discarding combat attempts."""
        from copy import deepcopy
        old=self.group;fresh=GroupBook()
        for name in ('names','classes','rosters','lifetimes'):
            setattr(fresh,name,deepcopy(getattr(old,name)))
        for flow,roster in fresh.rosters.items():
            fresh.timeline.append(dict(kind='roster',flow=flow,clock_us=now,roster=deepcopy(roster)))
        latest={}
        for h in old.current_health:latest[(h['flow'],h['target'])]=h
        latest_damage={};latest_uncertain={}
        for e in old.timeline:
            if e['kind']=='damage':
                pair=(e['flow'],e['target']);latest_damage[pair]=max(e['clock_us'],latest_damage.get(pair,-1))
        for flow,records in old.uncertain_damage.items():
            for clock,target in records:
                pair=(flow,target);latest_uncertain[pair]=max(clock,latest_uncertain.get(pair,-1))
        for pair,values in old.health.items():
            if values:
                anchor=dict(values[-1],clock_us=now)
                current=latest.get(pair)
                if current and current['clock_us']>=values[-1]['clock_us']:
                    anchor['current']=current['current']
                    anchor['reset_baseline_basis']='latest-observed-current-hp'
                fresh.health[pair]=[anchor]
                baseline=max(values[-1]['clock_us'],current['clock_us'] if current else -1)
                unresolved=max(latest_damage.get(pair,-1),latest_uncertain.get(pair,-1),latest_uncertain.get((pair[0],None),-1))>baseline
                # The first interval may contain HP lost before the reset. Keep
                # that boundary uncertain until a new HP observation arrives.
                if unresolved:fresh.uncertain(dict(flow=pair[0],clock_us=now+1),pair[1])
        for h in latest.values():fresh.current_health.append(dict(h,clock_us=now))
        meter=Meter();meter.invalid_flows=set(self.meter.invalid_flows);meter.births=dict(self.meter.births)
        self.meter=meter;self.group=fresh;self.pending_events={}
        self.damage_diagnostics=[];self.damage_messages=[];self.combat_floor_us=now

    def reject_damage(self, ctx, body, offset, reason):
        self.rejected += 1
        target=None
        hp_ambiguous=True
        if body[:2]==b'\x05\x38':
            try:
                target,at=varint(body,2)
                # Only periodic effects 02 / 0A are damage in this profile.
                # Unsupported control/status effects remain in diagnostics,
                # but must not invalidate a later HP drop from a known hit.
                if at<len(body) and body[at] not in (0x02,0x0A):hp_ambiguous=False
            except ValueError:pass
        if hp_ambiguous:self.group.uncertain(ctx,target)
        if len(self.damage_diagnostics)<64:
            self.damage_diagnostics.append(dict(**ctx,opcode=body[:2].hex(),body_offset=offset,
                                                reason=str(reason),bytes=len(body),body_hex=body[:8192].hex()))

    def warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)
            self.warnings = self.warnings[-16:]

    def ingest(self, row):
        stamp = microseconds(row['frame.time_epoch'])
        if self.origin_us is None:
            self.origin_us = stamp
        now = max(0, stamp-self.origin_us)
        self.clock_us = max(self.clock_us, now)
        self.packets += 1
        for key, d in self.directions.items():
            try:
                if not d.failed: d.tick(self.clock_us)
            except ValueError as exc:
                if d.accepted:
                    self.warn(str(exc)); self.meter.interrupt(key, self.clock_us)
                    self.group.interrupted[key]=self.clock_us
        if not row.get('tcp.payload'):
            return
        source = row.get('ip.src') or row.get('ipv6.src') or ''
        dest = row.get('ip.dst') or row.get('ipv6.dst') or ''
        flow = '|'.join([self.generation, row['tcp.stream'], source, row['tcp.srcport'], dest, row['tcp.dstport']])
        if len(self.directions) >= 32 and flow not in self.directions:
            raise ValueError('Limite de 32 direções TCP nesta sessão.')
        d = self.directions.setdefault(flow, Direction(allow_midstream=True))
        if d.failed:
            return
        try:
            payload = bytes.fromhex(row['tcp.payload'].replace(':', ''))
            if len(payload) != int(row['tcp.len']):
                d.fail('Tamanho de payload inconsistente.')
            ctx = dict(flow=flow, clock_us=now, packet=int(row['frame.number']))
            for context, body in d.feed(int(row['tcp.seq_raw']), payload, ctx):
                self.message(context, body)
        except ValueError as exc:
            if d.accepted:
                self.warn(str(exc)); self.meter.interrupt(flow, self.clock_us)
                self.group.interrupted[flow]=self.clock_us
            # An opaque outbound direction failing framing does not corrupt inbound.

    def message(self, ctx, body):
        flow, op = ctx['flow'], body[:2]
        # Resetting a combat must not drop a newly reassembled pet message
        # whose first TCP segment preceded that combat reset.
        if self.message_observer:self.message_observer(ctx,body)
        if op==b'\x0b\x61':
            # A relog can reuse the same TCP stream with a different actor.
            # Only a structurally valid initial resource list starts a new
            # identity epoch; unrelated/malformed messages do not clear conflicts.
            from activity_entries import decode_initial_entries
            try:decode_initial_entries(body)
            except ValueError:return
            direction=self.directions.get(flow)
            if direction and (direction.failed or not direction.accepted):return
            self.meter.interrupt(flow,ctx['clock_us'])
            self.meter.invalid_flows.discard(flow)
            self.bindings.pop(flow,None);self.evidence.pop(flow,None);self.pending_events.pop(flow,None)
            self.group.rosters.pop(flow,None)
            self.group.timeline.append(dict(kind='roster',**ctx,roster=dict(key=None,members=[])))
            self.group.interrupted.pop(flow,None)
            if not self.meter.invalid_flows:
                self.warnings=[w for w in self.warnings if w!='Identidade ambígua; atribuição removida nesta conexão.']
            return
        if ctx['clock_us']<self.combat_floor_us:return
        metadata=self.group.observe(ctx,body)
        if metadata and op==b'\x41\x36':
            self.meter.spawn(flow,metadata['target'],ctx['clock_us'])
        elif metadata and metadata.get('death_confirmed'):
            self.meter.close(flow,metadata['target'],ctx['clock_us'])
        if flow in self.meter.invalid_flows:
            return
        if op in (b'\x04\x8d', b'\x33\x36'):
            try:
                entry = identity(body) if op == b'\x04\x8d' else self_identity(body)
            except (ValueError, UnicodeDecodeError):
                self.rejected += 1; return
            evidence = self.evidence.setdefault(flow, [])
            if len(evidence) >= 2000:
                raise ValueError('Limite de evidências de identidade.')
            evidence.append(entry)
            self.group.identify(flow,entry)
            if op == b'\x04\x8d':self.group.death(ctx,entry['victim'])
            binding = resolve_identity(evidence, self.character, self.server)
            if binding:
                old = self.bindings.get(flow)
                if old and (old['actor'], old['server']) != (binding['actor'], binding['server']):
                    self.conflict(flow); return
                if flow not in self.bindings:
                    self.bindings[flow] = binding
                    for event in self.pending_events.pop(flow, []): self.attribute(event, binding)
            elif flow in self.bindings:
                self.conflict(flow); return
            if op == b'\x04\x8d':
                self.meter.close(flow, entry['victim'], ctx['clock_us'])
            return
        candidates = []
        if op in (b'\x04\x38',b'\x05\x38') and len(self.damage_messages)<64:
            self.damage_messages.append(dict(**ctx,opcode=op.hex(),bytes=len(body),body_hex=body[:8192].hex()))
        if op == b'\x04\x38':
            at = 2
            while at < len(body):
                if body[at:at+2] == b'\x01\x00':
                    at += 2
                    if at == len(body): break
                try:
                    e = record(body, at)
                    if e is None: break
                    candidates.append(dict(e, body_offset=at, kind='direct_damage_candidate'))
                    at = e['body_end']
                    if at < len(body) and body[at:at+2] != b'\x01\x00': break
                except ValueError as exc:
                    self.reject_damage(ctx,body,at,exc); break
        elif op == b'\x05\x38':
            try:
                e = periodic(body, PROFILE)
                if e['kind'] == 'periodic_damage': candidates.append(dict(e, body_offset=2))
            except ValueError as exc:
                self.reject_damage(ctx,body,2,exc)
        for e in candidates:
            if ctx['clock_us']<self.combat_floor_us:continue
            e = dict(e, **ctx, skill_name=skill_label(e['raw_skill'], PROFILE), screen_confirmed=False,
                     id=f"{flow}/{ctx['message_index']}/{e['body_offset']}")
            self.group.add(e)
            binding = self.bindings.get(flow)
            if binding: self.attribute(e, binding)
            else:
                waiting = self.pending_events.setdefault(flow, [])
                if len(waiting) >= 10000:
                    raise ValueError('Limite de eventos aguardando identificação.')
                waiting.append(e)

    def attribute(self, event, binding):
        if event['actor'] == binding['actor'] and event['actor'] != event['target']:
            self.meter.add(event)

    def conflict(self, flow):
        self.bindings.pop(flow, None); self.pending_events.pop(flow, None)
        self.meter.invalidate(flow); self.warn('Identidade ambígua; atribuição removida nesta conexão.')

    def finish(self):
        for flow, direction in self.directions.items():
            if direction.failed: continue
            try: direction.finish()
            except ValueError as exc:
                if direction.accepted:
                    self.warn(str(exc)); self.meter.interrupt(flow, self.clock_us)
                    self.group.interrupted[flow]=self.clock_us

    def snapshot(self, extra_us=0):
        # Never advance inferred time through a stale capture or ended encounter.
        now = self.clock_us + max(0, int(extra_us))
        result = dict(character=self.character, clock_us=now, packets=self.packets,
                    identified=bool(self.bindings), bindings=list(self.bindings.values()),
                    recognized=any(d.accepted for d in self.directions.values()),
                    rejected=self.rejected, warnings=list(self.warnings),
                    damage_diagnostics=list(self.damage_diagnostics),
                    damage_messages=list(self.damage_messages),
                    pending_records=sum(len(v) for v in self.pending_events.values()),
                    active_key=self.meter.active_key,
                    encounters=[e.snapshot(now) for e in self.meter.encounters],
                    events=[dict(event,encounter_key=fight.key) for fight in self.meter.encounters for event in fight.events],
                    experimental=True, dps_screen_validated=False)
        result.update(self.group.snapshot(now))
        for fight in result['encounters']:
            end=fight['end_us'] if fight['end_us'] is not None else now
            hp=self.group.hp_reference(fight['key'],fight['flow'],fight['target'],fight['start_us'],end)
            actor=self.bindings.get(fight['flow'],{}).get('actor')
            contribution=self.group.hp_contribution(fight['key'],fight['flow'],fight['target'],fight['start_us'],end,actor,fight['end_us'] is not None)
            fight.update(**hp,**contribution)
        return result
