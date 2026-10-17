# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Local subprocess source. No HTTP server, game injection, or broad filters."""
import csv
import hashlib
import os
import ipaddress
import json
import queue
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from datetime import datetime,timezone
from live_core import LiveReader, FIELDS
from od_energy import OdEnergy
from resource_gains import ResourceGains
from daily_duty import DailyDuty
from weekly_orders import WeeklyOrders
from altgard_orders import AltgardOrders
from order_accumulator import OrderAccumulator,reconcile as reconcile_orders
from activity_entries import ActivityEntries
from character_cache import CharacterCache,identity_key
from class_appearance import display_classes
from character_selection import CharacterSelection
from self_combat_identity import LocalCombatIdentity,current_party_identity
from party_vitals_scope import PartyVitalsScope
from resource_refresh import ResourceRefresh
from app_paths import diagnostics_directory
from windows_tools import powershell_executable, powershell_environment
from installation_guard import enforce_elevated_installation

ROOT = Path(__file__).resolve().parent
HIDDEN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0


def recovery_delay(attempt):
    # Retry throughout ON, without spinning or exhausting a long-running session.
    return (1,3,8,30,60)[min(max(1,attempt),5)-1]


def capture_filter(connections):
    parts, keys = set(), set()
    for row in connections:
        local = ipaddress.ip_address(row['LocalAddress'])
        remote = ipaddress.ip_address(row['RemoteAddress'])
        lp, rp = int(row['LocalPort']), int(row['RemotePort'])
        if local.version != remote.version or '%' in str(local)+str(remote):
            raise ValueError('Endereços IP fora do perfil suportado.')
        if not 1 <= lp <= 65535 or not 1 <= rp <= 65535:
            raise ValueError('Porta inválida.')
        if rp in (80, 443): continue
        family = 'ip' if local.version == 4 else 'ip6'
        pair = sorted([(str(local), lp), (str(remote), rp)])
        keys.add(tuple(pair))
        parts.add(f'({family} and ((src host {local} and src port {lp} and dst host {remote} and dst port {rp}) or (src host {remote} and src port {rp} and dst host {local} and dst port {lp})))')
    if not parts: raise ValueError('Sem conexões elegíveis do jogo.')
    if len(parts) > 32: raise ValueError('Mais de 32 conexões; perfil de captura não reconhecido.')
    return 'tcp and (' + ' or '.join(sorted(parts)) + ')', tuple(sorted(keys))


def tshark_args(tool, interface=None, bpf=None, pcap=None):
    if pcap:
        args = [str(tool), '-r', str(pcap)]
    else:
        if not interface or not bpf: raise ValueError('Interface e filtro exato são obrigatórios.')
        args = [str(tool), '-i', interface, '-f', bpf, '-p', '-B', '16', '-s', '0']
    args += ['-l', '-n', '-Y', 'tcp.len > 0', '-T', 'fields', '-E', 'header=y', '-E', 'separator=/t', '-E', 'occurrence=f']
    for field in FIELDS: args += ['-e', field]
    return args


def list_interfaces(tool):
    result = subprocess.run([str(tool), '-D'], capture_output=True, timeout=12, creationflags=HIDDEN)
    if result.returncode: raise ValueError(result.stderr.decode('utf-8', 'replace')[-700:])
    interfaces = []
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        match = re.match(r'^\d+\.\s+(\S+)\s*(.*)$', line)
        if match:
            interfaces.append(dict(device=match[1], label=match[2].strip('() ') or match[1]))
    if not interfaces: raise ValueError('Nenhuma interface Npcap encontrada.')
    return interfaces


def capture_plan(connections,interface,interfaces):
    """Plan exact game sockets. WinDivert supports direct and local paths."""
    candidates=[r for r in connections if int(r['RemotePort']) not in (80,443)]
    if interface=='windivert':
        eligible=candidates;device='windivert'
    elif interface=='auto':
        eligible=[r for r in candidates if all(ipaddress.ip_address(r[k]).is_loopback for k in ('LocalAddress','RemoteAddress'))]
        device=next((r['device'] for r in interfaces if 'loopback' in (r['device']+' '+r['label']).lower()),None)
        if not device:raise ValueError('Interface de loopback não encontrada no Npcap.')
    else:
        if interface not in {r['device'] for r in interfaces}:raise ValueError('Interface selecionada não está disponível.')
        eligible=candidates;device=interface
    bpf,keys=capture_filter(eligible) if eligible else (None,())
    ownership={}
    for row in eligible:
        pid=row.get('OwningProcess')
        if type(pid) is not int or pid<=0:continue
        local=(str(ipaddress.ip_address(row['LocalAddress'])),int(row['LocalPort']))
        remote=(str(ipaddress.ip_address(row['RemoteAddress'])),int(row['RemotePort']))
        key=tuple(sorted((local,remote)))
        owner=dict(process_id=pid,local_endpoint=local)
        if type(row.get('ProcessStartedFiletime')) is int and row['ProcessStartedFiletime']>0:
            owner['process_started_filetime']=row['ProcessStartedFiletime']
        if type(row.get('ConnectionStartedFiletime')) is int and row['ConnectionStartedFiletime']>0:
            owner['connection_started_filetime']=row['ConnectionStartedFiletime']
        elif row.get('ConnectionStartedFiletime') is not None and not (
                type(row['ConnectionStartedFiletime']) is int and row['ConnectionStartedFiletime']==0):
            owner['invalid_connection_started_filetime']=True
        if key in ownership and ownership[key]!=owner:ownership[key]=None
        else:ownership[key]=owner
    return dict(device=device,filter=bpf,keys=keys,ownership=ownership,eligible_connections=len(eligible),
        detected_connections=len(candidates),skipped_non_loopback=len(candidates)-len(eligible) if interface=='auto' else 0)


class Controller:
    def __init__(self, tool=None, cache_directory=None, native_factory=None, npcap_factory=None, windivert_factory=None):
        self.character_cache=CharacterCache(cache_directory)
        self.cached_identity=None
        self.resource_refresh=ResourceRefresh()
        self.character_selection=CharacterSelection()
        self.local_combat_identity=LocalCombatIdentity();self.party_vitals_scope=PartyVitalsScope()
        self.last_auto_fault_at=None;self.last_auto_fault_path=None
        self.tool = Path(tool or r'C:\Program Files\Wireshark\tshark.exe')
        self.native_factory=native_factory
        self.npcap_factory=npcap_factory
        self.windivert_factory=windivert_factory
        self.lock = threading.RLock()
        self.reader = LiveReader()
        self.status = 'Pronto para iniciar'
        self.mode = 'idle'
        self.error = None
        self.last_packet = None
        self.running = False
        self.stop_event = threading.Event()
        self.owned = set()
        self.owned_native=set()
        self.worker = None
        self.generation = 0
        self.logs = []
        self.metadata = {}
        self.capture_trace=[];self.capture_trace_replaced=0;self.available_interfaces=[]
        self.archives = []
        self.display_clock_us = 0
        self.stream_serial = 0
        self.capture_retries = {}
        self.flow_processes = {}
        self.incoming_flows = set()
        from pet_collection import PetWireCollection,CollectionDiagnostic
        self.od_energy=OdEnergy(self.reader.character)
        self.resource_gains=ResourceGains(self.reader.character)
        self.daily_duty=DailyDuty();self.weekly_orders=WeeklyOrders();self.altgard_orders=AltgardOrders();self.order_accumulator=OrderAccumulator(self.reader.character)
        self.activity_entries=ActivityEntries(self.reader.character)
        self.pets=PetWireCollection();self.collection_diagnostic=CollectionDiagnostic()
        self.reader.message_observer=self._observe_message

    def _observe_message(self,ctx,body):
        self.collection_diagnostic.observe(ctx,body)
        if self.mode=='live' and (not self.running or self.stop_event.is_set()):return
        if self.running and self.mode=='live' and body[:2]==b'\x0d\x90':
            # ON owns these exact sockets. A unique, recognized incoming world
            # stream can count private soul notifications without a login,
            # character header, collection screen or an absolute account base.
            # Ownership is set before LiveReader emits this first ready message.
            candidates=[flow for flow,direction in self.reader.directions.items()
                if flow in self.incoming_flows and type(self.flow_processes.get(flow)) is int
                and self.flow_processes[flow]>0 and direction.accepted and direction.heartbeats>=20
                and not direction.failed and flow not in self.pets.blocked]
            if len(candidates)==1 and candidates[0]==ctx['flow']:
                self.pets.attach_live_channel(ctx['flow'],self.flow_processes[ctx['flow']])
        if self.mode=='live' and self.running:
            self.character_selection.observe(ctx,body,self._od_channel())
            if self.character_selection.at_selection:
                self.local_combat_identity=LocalCombatIdentity();self.party_vitals_scope=PartyVitalsScope()
            elif body[:2]==b'\x0b\x61' and ctx['flow']==self._od_channel():
                from activity_entries import decode_initial_entries
                try:decode_initial_entries(body)
                except ValueError:pass
                else:self.local_combat_identity=LocalCombatIdentity();self.party_vitals_scope=PartyVitalsScope()
            elif body[:2]==b'\x4a\x36':self._identify_midstream_combat(ctx,body)
            elif body[:2]==b'\x1b\x92' and not self.error and self.metadata.get('capture_source_ready'):
                proof=self.party_vitals_scope.observe(ctx,body,self._od_channel(),self.reader.group.roster_epochs.get(ctx['flow'],0))
                if proof:self.reader.group.confirm_party_actor(ctx,proof)
            self._identify_character(ctx,body)
        self.pets.observe(ctx,body)
        if self.mode=='live' and self.running:
            channel=self._od_channel()
            if body[:2] in (b'\x83\x56',b'\x0b\x61',b'\x33\x36',b'\x1c\x8d',b'\x0c\x61',b'\x84\x56',b'\x0f\x8d'):
                # Metadata only: explain missing/rejected activity readings in
                # the existing local diagnostic without retaining more payload.
                if not hasattr(self,'activity_login_trace'):
                    self.activity_login_trace=deque(maxlen=64)
                self.activity_login_trace.append(dict(opcode=body[:2].hex(),flow=ctx['flow'],
                    clock_us=ctx['clock_us'],bytes=len(body),applied=channel==ctx['flow']))
            if self.od_energy.flow and channel!=self.od_energy.flow:
                self.od_energy=OdEnergy(self.reader.character)
            if self.resource_gains.flow and channel!=self.resource_gains.flow:
                self.resource_gains=ResourceGains(self.reader.character)
                self.daily_duty=DailyDuty();self.weekly_orders=WeeklyOrders();self.altgard_orders=AltgardOrders();self.order_accumulator=OrderAccumulator(self.reader.character)
                self.activity_entries=ActivityEntries(self.reader.character)
            if channel==ctx['flow']:self.od_energy.observe(ctx,body)
            if channel==ctx['flow']:
                self.resource_gains.observe(ctx,body)
                self.daily_duty.observe(ctx,body)
                self.weekly_orders.observe(ctx,body)
                self.altgard_orders.observe(ctx,body)
                self.order_accumulator.observe(ctx,body)
                self.activity_entries.observe(ctx,body)
            if body[:2] in (b'\x00\x90',b'\x0d\x90',b'\x09\x90',b'\x14\x90',b'\x0e\x90',b'\x33\x36'):
                self._remember_pet_facts()

    def _identify_midstream_combat(self,ctx,body):
        if self.error or not self.metadata.get('capture_source_ready'):return
        proof=self.local_combat_identity.observe(ctx,body,self._od_channel())
        if not proof:return
        flow=ctx['flow']
        known=self.reader.bindings.get(flow)
        if known and known['actor']==proof['actor']:
            self.reader.confirm_local_combat(flow,proof)
            return
        identity=current_party_identity(self.reader.group,flow,proof['actor'])
        if not self.reader.confirm_local_combat(flow,proof,identity):return
        if not identity:return
        name=identity['name']
        self.reader.character=name;self.reader.server=identity['server']
        self.cached_identity=None
        for reader in (self.pets,self.od_energy,self.resource_gains,self.activity_entries,self.order_accumulator):
            reader.character=name
        self.metadata['automatic_character']=dict(name=name,server=identity['server'],
            actor=identity['actor'],flow=flow,basis=identity['basis'])

    def _identify_character(self,ctx,body):
        """Adopt only the self header on the uniquely owned incoming world flow.

        A saved/typed name is a compatibility hint for midstream kill credits,
        never a reason to reject a new self header. Nearby-player headers,
        rosters, kill credits and cached actors cannot discover the local name.
        Run before the field readers so this same header releases pending data.
        """
        if body[:2]!=b'\x33\x36' or ctx['flow']!=self._od_channel():return
        if ctx['flow'] in self.reader.meter.invalid_flows:return
        from live_core import self_identity
        try:identity=self_identity(body)
        except (ValueError,UnicodeError,IndexError):return
        old=self.reader.bindings.get(ctx['flow'])
        if old and (old['actor'],old['server'])!=(identity['actor'],identity['server']):
            # An initial login epoch clears bindings normally. A conflicting
            # header in the same epoch must still fail closed in LiveReader.
            self.reader.conflict(ctx['flow']);return
        name=identity['name']
        if name!=self.reader.character:
            anonymous=self.reader.local_combat_actors.get(ctx['flow'])
            same_anonymous_actor=not old and anonymous and anonymous['actor']==identity['actor']
            if self.reader.meter.encounters and not same_anonymous_actor:
                # Keep completed combat under its original character name.
                # Never re-label old damage when another character logs in.
                self.archives.append(self._export_current())
                self.archives=self.archives[-16:]
                self.reader.reset_combat(ctx['clock_us'])
            self.reader.character=name
            for reader in (self.pets,self.od_energy,self.resource_gains,
                           self.activity_entries,self.order_accumulator):
                reader.character=name
            self.cached_identity=None
        self.reader.server=identity['server']
        self.metadata['automatic_character']=dict(name=name,server=identity['server'],
            actor=identity['actor'],flow=ctx['flow'],basis='owned-self-header-3336')

    def _od_channel(self):
        candidates=[flow for flow,direction in self.reader.directions.items()
            if flow in self.incoming_flows and type(self.flow_processes.get(flow)) is int
            and self.flow_processes[flow]>0 and direction.accepted
            and direction.heartbeats>=20 and not direction.failed]
        return candidates[0] if len(candidates)==1 else None

    def _spawn(self, args, *, env=None):
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', errors='replace', bufsize=1,
                             creationflags=HIDDEN, shell=False, env=env)
        with self.lock: self.owned.add(p)
        def errors():
            with p.stderr as stream:
                for line in stream:
                    with self.lock: self.logs = (self.logs+[line.strip()])[-12:]
        threading.Thread(target=errors, daemon=True).start()
        return p

    def _terminate(self, p):
        if p is None: return
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=2)
        with self.lock: self.owned.discard(p)

    def stop(self):
        with self.lock:
            if self.running and self.mode=='live':self.snapshot()
            self.character_cache.flush(force=True)
        self.stop_event.set()
        self.pets.fault_sink=None
        with self.lock: processes = list(self.owned)
        for p in processes: self._terminate(p)
        with self.lock:native_sources=list(self.owned_native)
        for source in native_sources:
            try:source.close()
            except Exception as exc:
                with self.lock:self.logs.append('Encerramento da captura nativa: '+str(exc))
            finally:
                with self.lock:
                    if not source.meta.get('close_incomplete'):self.owned_native.discard(source)
                    else:self.error='Uma sessão nativa não encerrou completamente; feche o aplicativo antes de reiniciar.'
        if self.worker and self.worker is not threading.current_thread(): self.worker.join(timeout=5)
        with self.lock:
            for flow in self.reader.directions:
                self.reader.meter.interrupt(flow, max(self.reader.clock_us,self.display_clock_us))
                self.reader.group.interrupted[flow]=max(self.reader.clock_us,self.display_clock_us)
            if self.mode == 'sample':
                for e in self.reader.meter.encounters:
                    if e.end_us is None:
                        e.end_us = self.reader.clock_us; e.end_reason = 'amostra interrompida'; e.partial=True
            self.running=False
            if self.pets.flow:self.pets.invalidate(self.pets.flow)
            if self.error is None: self.status='Coleta parada' if self.mode!='sample' else 'Amostra parada'

    def _prepare(self, character, mode):
        self.stop()
        if self.owned_native:raise ValueError('Uma sessão nativa ainda está aberta; feche o aplicativo antes de iniciar outra captura.')
        with self.lock:
            self.generation += 1
            self.reader = LiveReader(character, generation=str(self.generation))
            self.character_selection=CharacterSelection()
            self.local_combat_identity=LocalCombatIdentity();self.party_vitals_scope=PartyVitalsScope()
            from pet_collection import PetWireCollection
            self.od_energy=OdEnergy(self.reader.character)
            self.resource_gains=ResourceGains(self.reader.character)
            self.daily_duty=DailyDuty();self.weekly_orders=WeeklyOrders();self.altgard_orders=AltgardOrders();self.order_accumulator=OrderAccumulator(self.reader.character)
            self.activity_entries=ActivityEntries(self.reader.character)
            self.pets=PetWireCollection(character)
            self.reader.message_observer=self._observe_message
            self.stop_event = threading.Event()
            self.mode=mode; self.running=True; self.error=None; self.logs=[]
            self.metadata={'capture_source_ready':False} if mode=='live' else {}
            self.last_packet=None; self.archives=[];self.display_clock_us=0
            self.capture_trace=[];self.capture_trace_replaced=0;self.available_interfaces=[]
            self.activity_login_trace=deque(maxlen=64)
            self.status='Preparando captura' if mode=='live' else 'Reproduzindo amostra'
            self.stream_serial=0
            self.capture_retries={}
            self.flow_processes={};self.incoming_flows=set()

    def start(self, character, interface='auto'):
        enforce_elevated_installation(refresh=True)
        self._prepare(character, 'live')
        interface='windivert' if interface=='auto' else interface
        target=self._live_native if interface in ('native','windivert') else self._live_npcap if interface=='npcap' else self._live
        self.worker=threading.Thread(target=target, args=(interface,),daemon=True)
        self.worker.start()

    def demo(self, speed=4):
        self._prepare('Zepidem', 'sample')
        self.worker=threading.Thread(target=self._demo,args=(speed,),daemon=True)
        self.worker.start()

    def _new_flow_session(self, character, metadata):
        with self.lock:
            self.snapshot();self.character_cache.flush(force=True)
            if self.reader.meter.encounters or self.reader.group.timeline:
                for flow in self.reader.directions:
                    self.reader.meter.interrupt(flow,self.reader.clock_us)
                    self.reader.group.interrupted[flow]=max(self.reader.clock_us,self.display_clock_us)
                self.archives.append(self._export_current())
                if len(self.archives)>16: raise ValueError('Limite de 16 mudanças de conexão; inicie outra sessão.')
            self.generation += 1
            self.reader=LiveReader(character,generation=str(self.generation))
            self.character_selection=CharacterSelection()
            self.local_combat_identity=LocalCombatIdentity();self.party_vitals_scope=PartyVitalsScope()
            from pet_collection import PetWireCollection
            self.od_energy=OdEnergy(self.reader.character)
            self.resource_gains=ResourceGains(self.reader.character)
            self.daily_duty=DailyDuty()
            self.weekly_orders=WeeklyOrders();self.altgard_orders=AltgardOrders();self.order_accumulator=OrderAccumulator(self.reader.character);self.activity_entries=ActivityEntries(self.reader.character)
            self.pets=PetWireCollection(character)
            self.reader.message_observer=self._observe_message
            self.last_packet=None; self.metadata=metadata;self.display_clock_us=0
            self.flow_processes={};self.incoming_flows=set()

    def _ingest_capture_row(self,row,reader,channel=None):
        with self.lock:
            if reader is not self.reader:return False
            if self.stop_event.is_set() or channel is not None and channel.get('stop') is not None and channel['stop'].is_set():return False
            if channel is not None and channel.get('confirmed_until') is not None and time.monotonic()>channel['confirmed_until']:
                channel['error']='Confirmação das conexões expirou; relogue após reiniciar a captura.'
                return False
            if channel is not None:
                original=row['tcp.stream']
                if original not in channel['streams']:
                    self.stream_serial+=1;channel['streams'][original]=str(self.stream_serial)
                row['tcp.stream']=channel['streams'][original]
                source=row.get('ip.src') or row.get('ipv6.src') or ''
                dest=row.get('ip.dst') or row.get('ipv6.dst') or ''
                flow='|'.join((reader.generation,row['tcp.stream'],source,row['tcp.srcport'],dest,row['tcp.dstport']))
                channel['flows'].add(flow)
                pid=channel.get('process_id')
                if type(pid) is int and pid>0:
                    self.flow_processes[flow]=pid
                    local=channel.get('local_endpoint')
                    if local and (str(ipaddress.ip_address(dest)),int(row['tcp.dstport']))==tuple(local):self.incoming_flows.add(flow)
            self.collection_diagnostic.observe_transport(row,reader.generation)
            reader.ingest(row); self.last_packet=time.monotonic()
            for flow,direction in reader.directions.items():
                # Combat attribution can be ambiguous while the own
                # collection flow remains identified and intact.
                if direction.failed:
                    self.pets.invalidate(flow,'Leitura parcial · aguardando recuperação da captura')
            if channel is not None:
                channel['recognized']=any(reader.directions.get(flow) and reader.directions[flow].accepted and not reader.directions[flow].failed
                    for flow in channel['flows'])
                if channel['recognized'] and 'key' in channel:
                    self.capture_retries.pop(channel['key'],None)
                failed_incoming=[reader.directions[flow].failed for flow in channel['flows']
                    if flow in self.incoming_flows and reader.directions.get(flow) and reader.directions[flow].failed]
                if failed_incoming:
                    # A capture may begin inside an existing TCP frame.
                    # Retry this exact owned socket as a new namespace;
                    # never scan or reframe the discarded payload. An
                    # opaque outgoing direction alone is not a failure
                    # of the healthy incoming world channel.
                    channel['error']='Leitura recebida interrompida: '+str(failed_incoming[0])
                    return False
            return True

    def _read_process(self, process, reader, stop, channel=None):
        try:
            rows=csv.DictReader(process.stdout,delimiter='\t')
            # TShark emits its field header with the first matching packet.
            # An idle socket can end without ever having produced a header.
            if rows.fieldnames is None:
                if not stop.is_set() and not self.stop_event.is_set():
                    raise ValueError('Conexão encerrou sem pacotes de captura.')
                return
            if rows.fieldnames != FIELDS: raise ValueError('Saída do TShark fora do formato esperado.')
            for row in rows:
                if stop.is_set() or self.stop_event.is_set(): break
                if not self._ingest_capture_row(row,reader,channel):break
        except Exception as exc:
            if not stop.is_set() and not self.stop_event.is_set():
                with self.lock:
                    if channel is None:
                        self.error=str(exc); self.status='Coleta interrompida'; self.running=False
                    else:
                        channel['error']=str(exc)
        finally:
            with self.lock:
                if reader is self.reader:
                    if channel is None:reader.finish()
                    else:
                        for flow in channel['flows']:
                            direction=reader.directions.get(flow)
                            if direction:
                                try:direction.finish()
                                except ValueError:pass
                    for flow,direction in reader.directions.items():
                        if direction.failed:self.pets.invalidate(flow,'Leitura parcial · aguardando recuperação da captura')
            process.stdout.close()
            if channel is not None:channel['finished']=True

    def _retire_capture(self,channel,reason):
        channel['stop'].set();self._terminate(channel['process'])
        channel['thread'].join(timeout=3)
        with self.lock:
            for flow in channel['flows']:
                self.reader.meter.interrupt(flow,self.reader.clock_us)
                self.reader.group.interrupted[flow]=self.reader.clock_us
                self.pets.invalidate(flow,reason)
                self.reader.directions.pop(flow,None)
                self.flow_processes.pop(flow,None);self.incoming_flows.discard(flow)

    def _capture_status(self,captures,plan):
        with self.lock:
            retries=[dict(self.capture_retries[key]) for key in plan['keys'] if key in self.capture_retries]
        if retries:
            if captures:
                return 'Captura parcial · recuperando conexão'
            return 'Reconectando captura'
        return 'Capturando eventos' if captures else None

    def _update_captures(self,captures,plan):
        """Keep exact unchanged sockets alive; an auxiliary socket is not a login.

        Each TShark sees one eligible process socket. Its stream numbers are
        namespaced before ingestion, so independent processes cannot merge TCP.
        No wider filter, packet replay or guessed framing resynchronization.
        """
        wanted=set(plan['keys'])
        now=time.monotonic()
        with self.lock:
            for key in list(self.capture_retries):
                if key not in wanted:self.capture_retries.pop(key,None)
        for key in list(captures):
            channel=captures[key]
            failed=bool(channel.get('error') or channel.get('finished') or channel['process'].poll() is not None)
            owner=plan.get('ownership',{}).get(key) or {}
            owner_changed=channel.get('process_id')!=owner.get('process_id') or channel.get('local_endpoint')!=owner.get('local_endpoint')
            if key in wanted and not failed and not owner_changed:continue
            captures.pop(key)
            self._retire_capture(channel,'Conexão encerrada · contagem parcial; aguardando reconexão')
            if key in wanted:
                with self.lock:
                    previous=self.capture_retries.get(key,{})
                    attempts=1 if channel.get('recognized') else previous.get('attempts',0)+1
                    delay=recovery_delay(attempts)
                    self.capture_retries[key]=dict(attempts=attempts,next_at=now+delay,
                        error=channel.get('error') or 'Processo de captura encerrou')
                    self._trace_capture('socket_capture_failed',socket_key=key,
                        error=self.capture_retries[key]['error'],retry_number=attempts,
                        retry_in_seconds=delay)
        for key in sorted(wanted-set(captures)):
            with self.lock:
                current=self.capture_retries.get(key)
                retry=dict(current) if current else None
            if retry and now<retry['next_at']:continue
            (source,sp),(dest,dp)=key
            bpf,_=capture_filter([dict(LocalAddress=source,LocalPort=sp,RemoteAddress=dest,RemotePort=dp)])
            try:process=self._spawn(tshark_args(self.tool,plan['device'],bpf))
            except OSError as exc:
                attempts=(retry or {}).get('attempts',0)+1
                delay=recovery_delay(attempts)
                with self.lock:
                    self.capture_retries[key]=dict(attempts=attempts,next_at=now+delay,error=str(exc))
                self._trace_capture('socket_capture_failed',socket_key=key,error=str(exc),
                    retry_number=attempts,retry_in_seconds=delay)
                continue
            channel=dict(process=process,stop=threading.Event(),streams={},flows=set(),
                error=None,finished=False,recognized=False,key=key)
            channel.update(plan.get('ownership',{}).get(key) or {})
            channel['thread']=threading.Thread(target=self._read_process,args=(process,self.reader,channel['stop'],channel),daemon=True)
            captures[key]=channel;channel['thread'].start()

    def _trace_capture(self,kind,**details):
        with self.lock:
            record=dict(kind=kind,**details)
            if self.capture_trace and {k:v for k,v in self.capture_trace[-1].items() if k!='utc'}==record:return
            record['utc']=datetime.now(timezone.utc).isoformat()
            if len(self.capture_trace)>=128:
                del self.capture_trace[16];self.capture_trace_replaced+=1
            self.capture_trace.append(record)

    def _save_pet_fault(self,report):
        now=time.monotonic()
        if self.last_auto_fault_at is not None and now-self.last_auto_fault_at<10:return False
        folder=diagnostics_directory();folder.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        path=folder/('mascotes-falha-'+stamp+'.json');temporary=path.with_suffix('.tmp')
        report.update(product='Império AION',monitor_version='0.8.80',utc=datetime.now(timezone.utc).isoformat(),
            capture_state=self._capture_diagnostic_state(),sampling='last-128-pet-messages-max-8192-bytes-each; not a full TCP recording')
        try:
            temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()
        for old in sorted(folder.glob('mascotes-falha-*.json'))[:-3]:old.unlink()
        self.last_auto_fault_at=now;self.last_auto_fault_path=str(path)
        return True

    def _clear_native_flows(self,channel,reason):
        with self.lock:
            for flow in channel['flows']:
                self.reader.meter.interrupt(flow,self.reader.clock_us)
                self.reader.group.interrupted[flow]=self.reader.clock_us
                self.pets.invalidate(flow,reason)
                self.reader.directions.pop(flow,None)
                self.flow_processes.pop(flow,None);self.incoming_flows.discard(flow)
            channel['flows'].clear();channel['streams'].clear()
            channel['recognized']=False

    def _retire_native_capture(self,channel,reason):
        channel['stop'].set()
        channel['native'].close()
        incomplete=channel['native'].meta.get('close_incomplete')
        with self.lock:
            if not incomplete:self.owned_native.discard(channel['native'])
        thread=channel.get('thread')
        if thread and thread is not threading.current_thread():thread.join(timeout=3)
        self._clear_native_flows(channel,reason)
        if incomplete:raise RuntimeError('A sessão nativa não encerrou completamente; feche o aplicativo')

    def _read_native_capture(self,reader,channel):
        from native_packets import parse_packet,packet_row
        from native_pktmon import NativeCaptureError
        seen={};packet_id=0;seen_syn={};has_payload=False
        try:
            while not channel['stop'].is_set() and not self.stop_event.is_set():
                record=channel['native'].read(timeout=.25)
                if record is None:continue
                if record.get('drop_reason'):
                    raise NativeCaptureError('O Windows informou descarte de pacote na conexão capturada')
                if record.get('missed_packet_write_count',0) or record.get('missed_packet_read_count',0):
                    raise NativeCaptureError('Perda de pacotes informada pela captura nativa')
                if record['packet_type'] not in (1,3):
                    with self.lock:
                        self.metadata['native_unsupported_snapshots']=self.metadata.get('native_unsupported_snapshots',0)+1
                    continue
                packet=parse_packet(record['data'],record['packet_type'])
                if packet is None:continue
                pair=tuple(sorted(((packet['source'],packet['source_port']),
                                  (packet['destination'],packet['destination_port']))))
                if pair!=channel['key']:continue  # Recheck the exact pair before attribution.
                group=record.get('pkt_group_id')
                if group is not None:
                    tag=(record.get('processor',0),group,record.get('pkt_count',0))
                    digest=hashlib.blake2s(packet['payload']).digest()
                    value=(packet['source'],packet['destination'],packet['source_port'],
                           packet['destination_port'],packet['tcp_seq_raw'],packet['tcp_flags'],digest)
                    previous=seen.get(tag)
                    if previous is not None:
                        if previous!=value:raise NativeCaptureError('Aparições do mesmo pacote têm conteúdo TCP diferente')
                        with self.lock:
                            self.metadata['native_duplicate_snapshots']=self.metadata.get('native_duplicate_snapshots',0)+1
                        continue
                    if len(seen)>=4096:seen.pop(next(iter(seen)))
                    seen[tag]=value
                direction=(packet['source'],packet['source_port'],packet['destination'],packet['destination_port'])
                if packet['tcp_flags']&2:
                    if has_payload and seen_syn.get(direction)!=packet['tcp_seq_raw']:
                        self._clear_native_flows(channel,'Nova conexão TCP · aguardando identificação do personagem')
                        seen_syn={};has_payload=False
                        self._trace_capture('native_socket_reused',socket_key=channel['key'])
                    seen_syn[direction]=packet['tcp_seq_raw']
                if not packet['payload']:continue
                stamp=record['timestamp_filetime']-116444736000000000
                if stamp<0:raise NativeCaptureError('Timestamp da captura nativa fora do perfil')
                seconds,fraction=divmod(stamp//10,1000000)
                packet_id+=1
                row=packet_row(packet,timestamp=f'{seconds}.{fraction:06d}',packet_id=packet_id,stream_id=1)
                if not self._ingest_capture_row(row,reader,channel):
                    raise NativeCaptureError(channel.get('error') or 'Leitura nativa interrompida')
                has_payload=True
        except Exception as exc:
            if not channel['stop'].is_set() and not self.stop_event.is_set():
                with self.lock:
                    channel['error']=str(exc)
                    self.metadata['native_source_error']=channel['native'].meta
                    self.error='Captura nativa interrompida: '+str(exc)+'. Reinicie a captura e relogue o personagem.'
                    self.status='Captura nativa interrompida · dados atuais não confirmados'
                self._trace_capture('native_capture_failed',socket_key=channel['key'],error=str(exc))
        finally:
            channel['finished']=True

    def _update_native_captures(self,captures,plan,backend_factory=None):
        from native_pktmon import NativeCapture,NativeCaptureError
        wanted={key for key in plan['keys'] if type((plan.get('ownership',{}).get(key) or {}).get('process_id')) is int
            and (plan.get('ownership',{}).get(key) or {})['process_id']>0}
        # Drop the ready signal before retiring/replacing any owned source.
        # It must never mean that merely requesting capture opened the driver.
        if set(captures)!=wanted:self._set_source_ready(False)
        for key in list(captures):
            channel=captures[key];owner=plan.get('ownership',{}).get(key) or {}
            changed=(channel.get('process_id')!=owner.get('process_id') or
                     channel.get('process_started_filetime')!=owner.get('process_started_filetime') or
                     channel.get('connection_started_filetime')!=owner.get('connection_started_filetime') or
                     channel.get('local_endpoint')!=owner.get('local_endpoint'))
            if channel.get('error'):raise NativeCaptureError(channel['error'])
            if key in wanted and not changed and not channel.get('finished'):
                if plan.get('confirmed_until') is not None:channel['confirmed_until']=plan['confirmed_until']
                continue
            self._set_source_ready(False)
            captures.pop(key)
            self._retire_native_capture(channel,'Conexão encerrada · aguardando confirmação no novo login')
        for key in sorted(wanted-set(captures)):
            backend=(backend_factory or self.native_factory or NativeCapture)(exact_endpoint_keys=[key])
            with self.lock:self.owned_native.add(backend)
            try:backend.start()
            except Exception:
                backend.close()
                with self.lock:
                    if not backend.meta.get('close_incomplete'):self.owned_native.discard(backend)
                raise
            channel=dict(native=backend,stop=threading.Event(),streams={},flows=set(),
                error=None,finished=False,recognized=False,key=key)
            channel.update(plan['ownership'][key])
            if plan.get('confirmed_until') is not None:channel['confirmed_until']=plan['confirmed_until']
            channel['thread']=threading.Thread(target=self._read_native_capture,args=(self.reader,channel),daemon=True)
            captures[key]=channel;channel['thread'].start()
            self._trace_capture('native_socket_started',socket_key=key,process_id=channel['process_id'])
        self._set_source_ready(bool(captures) and all(not c.get('finished') and not c.get('error') for c in captures.values()))

    def _set_source_ready(self,ready):
        with self.lock:
            ready=bool(ready)
            previous=self.metadata.get('capture_source_ready')
            self.metadata['capture_source_ready']=ready
            if ready!=previous:
                self._trace_capture('capture_source_ready',ready=ready)

    def _live_npcap(self,interface='npcap'):
        return self._live_native(interface='npcap')

    def _save_capture_fault(self):
        """Save metadata even when discovery fails before the first packet."""
        try:
            folder=diagnostics_directory();folder.mkdir(parents=True,exist_ok=True)
            stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            path=folder/('captura-falha-'+stamp+'.json')
            report=dict(product='Império AION',monitor_version='0.8.80',package_revision=1,
                kind='capture-startup-failure',capture_state=self._capture_diagnostic_state(),
                scope='connection metadata only; no packet payloads')
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            with self.lock:self.metadata['capture_failure_diagnostic']=str(path)
        except Exception as exc:
            with self.lock:self.logs=(self.logs+['Não foi possível salvar o diagnóstico da captura: '+str(exc)])[-12:]

    def _live_native(self,interface='native'):
        """Owned independent Windows sessions. Never change global Pktmon state.

        The legacy source stays available while full loopback capture is qualified.
        Native source loss is fatal for this run: no guessed framing or partial sums.
        """
        from native_pktmon import probe,NativeCaptureError
        is_npcap=interface=='npcap'
        is_windivert=interface=='windivert'
        verified_backend=is_npcap or is_windivert
        label='Captura integrada' if is_windivert else 'Npcap' if is_npcap else 'Captura nativa'
        backend_factory=None
        if is_npcap:
            from native_npcap import NativeCapture as NpcapCapture,probe as npcap_probe
            backend_factory=self.npcap_factory or NpcapCapture
            probe=npcap_probe
        if is_windivert:
            from native_windivert import NativeCapture as WinDivertCapture,probe as windivert_probe
            backend_factory=self.windivert_factory or WinDivertCapture;probe=windivert_probe
        self.pets.fault_sink=self._save_pet_fault
        watcher=None;captures={};fast_observer=False
        try:
            if (self.windivert_factory is None if is_windivert else self.npcap_factory is None if is_npcap else self.native_factory is None):
                report=probe()
                if not report.get('available'):raise NativeCaptureError(report.get('error') or report.get('reason') or ('Npcap não encontrado. Instale o Npcap pelo site oficial.' if is_npcap else 'API nativa indisponível neste Windows'))
                if not is_npcap and not report.get('administrator'):
                    raise NativeCaptureError('Abra o aplicativo como administrador para iniciar a captura integrada.' if is_windivert else 'A captura nativa do Windows exige execução como administrador')
                with self.lock:self.metadata['native_probe']=report
            with self.lock:
                self.available_interfaces=[dict(device=interface,label=label+' · conexão automática')]
                self.metadata.update(capture_backend='windivert-passive' if is_windivert else 'npcap-direct' if is_npcap else 'windows-pktmon-api',native_qualified=False,
                    native_truncation_limit=None if verified_backend else 9000,native_duplicate_snapshots=0,native_unsupported_snapshots=0)
            self._trace_capture('native_capture_started',requested_interface=interface)
            if is_windivert and self.windivert_factory is None:
                from native_connections import NativeConnectionObserver
                watcher=NativeConnectionObserver()
                fast_observer=True
                with self.lock:
                    self.owned.add(watcher)
                    self.metadata['connection_observer']='windows-ip-helper-200ms'
            else:
                # Keep the optional legacy sources and their injected test
                # harnesses intact; production WinDivert uses IP Helper.
                shell=powershell_executable()
                watcher=self._spawn([shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'Observar-Conexoes.ps1')],env=powershell_environment(shell))
            observations_queue=queue.Queue(maxsize=1)
            last_confirmed=time.monotonic()
            def observations(source=watcher,destination=observations_queue):
                with source.stdout as stream:
                    for line in stream:
                        try:item=json.loads(line)
                        except json.JSONDecodeError:continue
                        item['_received_monotonic']=time.monotonic()
                        try:destination.put_nowait(item)
                        except queue.Full:
                            try:destination.get_nowait()
                            except queue.Empty:pass
                            try:destination.put_nowait(item)
                            except queue.Full:pass
            threading.Thread(target=observations,daemon=True).start()
            while not self.stop_event.is_set():
                if self.error:raise NativeCaptureError(self.error)
                try:observed=observations_queue.get(timeout=.4)
                except queue.Empty:
                    if watcher.poll() is not None:raise NativeCaptureError('O observador de conexões encerrou')
                    if verified_backend and captures and time.monotonic()-last_confirmed>6:
                        raise NativeCaptureError('A confirmação das conexões do jogo expirou. Reinicie o aplicativo e relogue o personagem.')
                    continue
                if not observed.get('ok'):
                    if fast_observer and not captures and observed.get('error_code')==5:
                        # Some Windows security policies deny IP Helper. Retain
                        # the existing ownership-checked source, with no broad
                        # capture and no fabricated successful observation.
                        self._terminate(watcher);fast_observer=False
                        observations_queue=queue.Queue(maxsize=1)
                        shell=powershell_executable()
                        watcher=self._spawn([shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'Observar-Conexoes.ps1')],env=powershell_environment(shell))
                        with self.lock:self.metadata['connection_observer']='powershell-fallback-ip-helper-denied'
                        threading.Thread(target=observations,args=(watcher,observations_queue),daemon=True).start()
                        continue
                    raise NativeCaptureError('Falha ao confirmar as conexões do jogo: '+str(observed.get('error','erro desconhecido')))
                plan=capture_plan(observed.get('connections') or [],'windivert' if is_windivert else 'auto',[dict(device='native',label='loopback')])
                if verified_backend:
                    with self.lock:
                        self.metadata['last_connection_observation']=dict(
                            observed_utc_ms=observed.get('observed_utc_ms'),game_count=observed.get('game_count'),
                            ownership=[dict(socket_key=key,owner=owner) for key,owner in plan['ownership'].items()])
                    generated=observed.get('observed_utc_ms')
                    received=observed.get('_received_monotonic',0)
                    if type(generated) is not int or not 0<=time.time()*1000-generated<=6000 or time.monotonic()-received>6:
                        raise NativeCaptureError('Consulta de conexões antiga ou sem timestamp; dados atuais não confirmados.')
                    # A game's auxiliary loopback pair can be reported at both
                    # ends with conflicting ownership. Reject that pair, rather
                    # than aborting a separate, positively identified game TCP
                    # connection. Only verified pairs reach backend.start().
                    verified=[];excluded=[]
                    for key in plan['keys']:
                        owner=plan['ownership'].get(key)
                        if (not owner or type(owner.get('process_started_filetime')) is not int
                                or owner.get('invalid_connection_started_filetime')):
                            excluded.append(dict(socket_key=key,reason='conflicting-or-unconfirmed-process-identity'))
                        else:verified.append(key)
                    with self.lock:
                        self.metadata['excluded_capture_connections']=excluded
                        # Kept for diagnostics/readers of the optional legacy path.
                        if is_npcap:self.metadata['excluded_npcap_connections']=excluded
                    auxiliary_only=bool(plan['keys']) and all(
                        plan['ownership'].get(key) is None
                        and all(ipaddress.ip_address(endpoint[0]).is_loopback for endpoint in key)
                        for key in plan['keys'])
                    if plan['keys'] and not verified and not auxiliary_only:
                        raise NativeCaptureError('Não foi possível confirmar a identidade do processo do jogo. Consulte o diagnóstico automático da captura.')
                    if auxiliary_only:
                        # The main game socket can close during selection/relog.
                        # Its auxiliary loopback pair is excluded as before;
                        # retire the missing world socket and keep watching for
                        # a newly owned one. Never capture the conflicting pair.
                        self._trace_capture('native_waiting_main_connection',
                            excluded_auxiliary_pairs=len(plan['keys']))
                    plan['keys']=tuple(verified)
                    plan['ownership']={key:plan['ownership'][key] for key in verified}
                    rows=[]
                    for key,owner in plan['ownership'].items():
                        local=owner['local_endpoint'];remote=key[1] if key[0]==local else key[0]
                        rows.append(dict(LocalAddress=local[0],LocalPort=local[1],RemoteAddress=remote[0],RemotePort=remote[1]))
                    plan['filter']=capture_filter(rows)[0] if rows else None
                    plan['eligible_connections']=len(verified)
                    # Some Windows/redirected TCP rows have no CreationTime. PID
                    # and actual process start remain mandatory. The full packet
                    # stream observes SYNs and clears the framing/identity on a
                    # new handshake; disappearance retires the socket as usual.
                    # Do not fabricate an OS timestamp or grant extra freshness.
                    with self.lock:
                        self.metadata['tcp_identity_without_creation_time']=sum(
                            'connection_started_filetime' not in owner for owner in plan['ownership'].values())
                    # Never grant a fresh six seconds to an already old snapshot.
                    plan['confirmed_until']=time.monotonic()+max(0,6-(time.time()-generated/1000))
                    last_confirmed=time.monotonic()
                self._update_native_captures(captures,plan,backend_factory=backend_factory)
                with self.lock:
                    self.metadata.update(interface=interface,requested_interface=interface,
                        detected_connections=plan['detected_connections'],eligible_connections=plan['eligible_connections'],
                        skipped_non_loopback=plan['skipped_non_loopback'],native_active_sessions=len(captures),
                        connection_handover_lossless=False,unchanged_socket_capture_preserved=True)
                    self.status=(label+' · DPS disponível · aguardando combate' if captures else
                        'Abra o AION 2 e entre no personagem' if not observed.get('game_count') else
                        label+' · aguardando conexão do jogo')
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock:
                    self.error=str(exc);self.status='Não foi possível continuar a captura integrada' if is_windivert else 'Não foi possível continuar a captura Npcap' if is_npcap else 'Não foi possível continuar a captura nativa'
                self._trace_capture('native_capture_failed',error=str(exc))
                self._save_capture_fault()
        finally:
            for channel in captures.values():
                try:self._retire_native_capture(channel,'Captura nativa encerrada · aguardando novo login')
                except Exception as exc:self.logs.append('Encerramento da sessão nativa: '+str(exc))
            self._terminate(watcher)
            with self.lock:
                for flow in self.reader.directions:
                    self.reader.meter.interrupt(flow,max(self.reader.clock_us,self.display_clock_us))
                    self.reader.group.interrupted[flow]=max(self.reader.clock_us,self.display_clock_us)
                if self.pets.flow:self.pets.invalidate(self.pets.flow)
                self.running=False

    def _live(self, interface):
        self.pets.fault_sink=self._save_pet_fault
        watcher=None;captures={};plan=None
        character=self.reader.character
        try:
            if not self.tool.is_file(): raise ValueError('TShark não encontrado. Instale Wireshark com Npcap ou informe seu caminho.')
            interfaces=list_interfaces(self.tool)
            with self.lock:self.available_interfaces=[dict(r) for r in interfaces]
            self._trace_capture('capture_started',requested_interface=interface)
            shell=powershell_executable()
            watcher=self._spawn([shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'Observar-Conexoes.ps1')],env=powershell_environment(shell))
            messages=queue.Queue(maxsize=8)
            def observations():
                with watcher.stdout as stream:
                    for line in stream:
                        try: item=json.loads(line)
                        except json.JSONDecodeError: continue
                        try: messages.put(item,timeout=1)
                        except queue.Full: pass  # Only old socket observations; packet rows never discarded.
            threading.Thread(target=observations,daemon=True).start()
            keys=()
            while not self.stop_event.is_set():
                if self.error: raise ValueError(self.error)
                try: observed=messages.get(timeout=.4)
                except queue.Empty:
                    if watcher.poll() is not None: raise ValueError('Monitor de conexões encerrou. '+ ' '.join(self.logs[-3:]))
                    if plan:
                        self._update_captures(captures,plan)
                        with self.lock:
                            status=self._capture_status(captures,plan)
                            if status:self.status=status
                            self.metadata['active_capture_processes']=len(captures)
                    continue
                if not observed.get('ok'):
                    observation_error=observed.get('error','erro desconhecido')
                    self._trace_capture('connection_query_failed',error=observation_error)
                    with self.lock:self.status='Aguardando nova consulta de conexões: '+observation_error
                    continue
                connections=observed.get('connections') or []
                plan=capture_plan(connections,interface,interfaces)
                bpf,new_keys=plan['filter'],plan['keys']
                self._trace_capture('connection_observation',game_count=observed.get('game_count',0),
                    detected_connections=plan['detected_connections'],eligible_connections=plan['eligible_connections'],
                    skipped_non_loopback=plan['skipped_non_loopback'],socket_keys=new_keys,
                    socket_ownership=[dict(socket_key=key,**owner) for key,owner in plan.get('ownership',{}).items() if owner])
                self._update_captures(captures,plan);keys=new_keys
                with self.lock:
                    self.metadata.update(interface=plan['device'],filter=bpf,connection_handover_lossless=False,
                        unchanged_socket_capture_preserved=True,active_capture_processes=len(captures),
                        capture_source_ready=bool(captures))
                    self.metadata.update(requested_interface=interface,detected_connections=plan['detected_connections'],
                        eligible_connections=plan['eligible_connections'],skipped_non_loopback=plan['skipped_non_loopback'])
                    self.status=self._capture_status(captures,plan) or ('Abra o AION 2 e entre no personagem' if not observed.get('game_count') else
                        'Aguardando conexão local do ExitLag; conexões externas ainda não são capturadas' if plan['skipped_non_loopback'] else 'Aguardando conexão do jogo')
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock: self.error=str(exc); self.status='Não foi possível continuar a coleta'
                self._trace_capture('capture_failed',error=str(exc))
        finally:
            for channel in captures.values():channel['stop'].set();self._terminate(channel['process'])
            self._terminate(watcher)
            for channel in captures.values():channel['thread'].join(timeout=3)
            with self.lock:
                for flow in self.reader.directions:
                    self.reader.meter.interrupt(flow,max(self.reader.clock_us,self.display_clock_us))
                    self.reader.group.interrupted[flow]=max(self.reader.clock_us,self.display_clock_us)
                if self.pets.flow:self.pets.invalidate(self.pets.flow)
                self.running=False

    def _demo(self, speed):
        try:
            data=json.loads((ROOT/'amostra.json').read_text(encoding='utf-8'))
            flow='amostra-validada'; started=time.monotonic()
            with self.lock:
                self.metadata={'mode':'sample','source':data['source'],'target_name':data['target']['name']}
                self.reader.bindings[flow]={'actor':data['character']['id'],'character':'Zepidem','server':2405,'basis':'amostra já identificada'}
            index=0
            while not self.stop_event.is_set():
                now=min(data['encounter']['duration_us'],int((time.monotonic()-started)*1e6*speed))
                with self.lock:
                    self.reader.clock_us=now; self.last_packet=time.monotonic()
                    while index<len(data['events']) and data['events'][index]['at_us']<=now:
                        event=data['events'][index]
                        self.reader.meter.add(dict(id=event['id'], flow=flow, clock_us=event['at_us'],
                            actor=event['actor'],target=event['target'],raw_skill=event['skill'],
                            candidate_amount=event['amount'],skill_name=event['name'],
                            packet=event['packet'],screen_confirmed=event['screen_confirmed']))
                        index+=1
                    if now==data['encounter']['duration_us']:
                        self.reader.meter.close(flow,data['target']['id'],now)
                        self.status='Amostra concluída'; self.running=False; break
                self.stop_event.wait(.025)
        except Exception as exc:
            with self.lock: self.error=str(exc);self.status='Falha na amostra';self.running=False

    def snapshot(self):
        with self.lock:
            age=time.monotonic()-self.last_packet if self.last_packet is not None else None
            projected=self.reader.clock_us + (int(age*1e6) if self.mode=='live' and self.running and age is not None and age<3 else 0)
            self.display_clock_us=max(self.display_clock_us,projected)
            result=self.reader.snapshot(self.display_clock_us-self.reader.clock_us)
            stale=self.mode=='live' and self.running and age is not None and age>=3
            result.update(status=self.status,error=self.error,mode=self.mode,running=self.running,
                          capture_ready=bool(self.running and not self.error and self.metadata.get('capture_source_ready',True)),
                          stale=stale,metadata=dict(self.metadata),archive_count=len(self.archives),capture_generation=self.generation)
            result['character_selection']=self.character_selection.snapshot(
                self._od_channel(),self.mode=='live' and result['capture_ready'])
            result['pet_collection']=self.pets.snapshot()
            if self.od_energy.flow and self._od_channel()!=self.od_energy.flow:
                self.od_energy=OdEnergy(self.reader.character)
            od_available=bool(self.running and self.mode=='live' and not self.error and not stale
                and not result['character_selection']['confirmed']
                and self.od_energy.flow and self._od_channel()==self.od_energy.flow)
            result['od_energy']=self.od_energy.snapshot(od_available)
            channel=self._od_channel()
            if self.resource_gains.flow and channel!=self.resource_gains.flow:
                self.resource_gains=ResourceGains(self.reader.character)
                self.daily_duty=DailyDuty();self.weekly_orders=WeeklyOrders();self.altgard_orders=AltgardOrders();self.order_accumulator=OrderAccumulator(self.reader.character)
                self.activity_entries=ActivityEntries(self.reader.character)
            binding=self.reader.bindings.get(channel) if channel not in self.reader.meter.invalid_flows else None
            self.resource_gains.bind_captured_identity(channel,binding)
            self.activity_entries.bind_captured_identity(channel,binding)
            gains_available=bool(self.running and self.mode=='live' and not self.error and not stale
                and not result['character_selection']['confirmed']
                and self.resource_gains.flow and channel==self.resource_gains.flow)
            result['resource_gains']=self.resource_gains.snapshot(gains_available,self.display_clock_us)
            duty_available=bool(gains_available and not self.resource_gains.awaiting_identity
                and self.resource_gains.identity and self.daily_duty.flow==channel)
            result['daily_duty']=self.daily_duty.snapshot(duty_available,self.display_clock_us)
            orders_available=bool(gains_available and not self.resource_gains.awaiting_identity
                and self.resource_gains.identity and self.weekly_orders.flow==channel)
            result['weekly_orders']=dict(latesran=self.weekly_orders.snapshot(orders_available,self.display_clock_us))
            if result['weekly_orders']['latesran'].get('available'):
                result['weekly_orders']['latesran']['flow']=self.weekly_orders.flow
            entries_available=bool(gains_available and not self.resource_gains.awaiting_identity
                and self.resource_gains.identity and self.activity_entries.flow==channel)
            result['activity_entries']=self.activity_entries.snapshot(entries_available,self.display_clock_us)
            # Startup requires a login header on this owned incoming stream.
            # Cached facts and combat bindings do not confirm initial character balances.
            identities=(self.od_energy.identity,self.resource_gains.identity,self.pets.identity)
            login_confirmed=bool(gains_available and binding and channel not in self.pets.blocked
                and self.od_energy.flow==channel and self.pets.flow==channel
                and not self.od_energy.awaiting_identity and not self.od_energy.wrong_character
                and self.resource_gains.login_observed and not self.resource_gains.awaiting_identity
                and all(i and i.get('name')==self.reader.character
                    and i.get('actor')==binding.get('actor') and i.get('server')==binding.get('server') for i in identities))
            entry_confirmed=bool(gains_available and binding
                and self.resource_gains.login_observed and not self.resource_gains.awaiting_identity
                and self.resource_gains.identity
                and all(self.resource_gains.identity.get(key)==value for key,value in (
                    ('name',self.reader.character),('actor',binding.get('actor')),('server',binding.get('server')))))
            result['login_session']=dict(confirmed=login_confirmed,entry_confirmed=entry_confirmed,
                                        character=self.reader.character)
            result['order_accumulated']=self.order_accumulator.snapshot(
                login_confirmed and self.order_accumulator.flow==channel,self.display_clock_us)
            result['collection_diagnostic_active']=self.collection_diagnostic.active
            result['collection_diagnostic_remaining_seconds']=self.collection_diagnostic.remaining_seconds
            result['collection_diagnostic_transport_observed']=self.collection_diagnostic.transport_observed
            if self.mode=='live' and self.running and login_confirmed and not self.error:
                backend_label={'windivert-passive':'Captura integrada','npcap-direct':'Npcap',
                               'windows-pktmon-api':'Captura nativa'}.get(self.metadata.get('capture_backend'))
                result['status']=((backend_label+' · ') if backend_label else '')+'login confirmado · recebendo dados'
            if stale:
                result['status']='Sem novos pacotes há mais de 3 s'
                for e in result['encounters']: e['partial']=True
                for e in result['group_encounters']:
                    if e['end_us'] is None:e['partial']=True
            if self.mode=='live' and self.running and result['recognized'] and not result['identified']:
                result['status']='DPS aguardando identificação de '+self.reader.character+' · somente seu personagem e grupo confirmado'
            elif self.mode=='live' and self.running and not result['recognized'] and result['packets']>20 and result['clock_us']>5000000:
                result['status']='Tráfego observado · aguardando reconhecimento; se persistir, relogue com a captura ativa'
            if result['warnings']: result['status']='Dados parciais · consulte o diagnóstico'
            self._cache_snapshot(result)
            # Reuse appearance only after live name/server or party identity.
            # Login baselines, damage and actor attribution remain independent.
            result['remembered_player_classes']=display_classes(
                self.character_cache,self.reader.bindings,self.reader.group.rosters)
            result['resource_refresh']=self.resource_refresh.snapshot(result,self._resource_scope())
            return result

    def _remember_pet_facts(self):
        """Keep each confirmed soul before a connection transition can erase it.

        Independent of Od/other resources; never seed totals from disk or save
        unresolved species. Disk writes stay atomic and use the normal flush.
        """
        identity=self.pets.identity
        if (not self.running or self.mode!='live' or self.error
                or not identity_key(identity) or identity['name']!=self.reader.character
                or self.pets.flow!=self._od_channel() or self.pets.flow in self.pets.blocked
                or not self.pets.initial or self.pets.account_gap
                or self.resource_gains.login_observed and self.resource_gains.awaiting_identity):return
        facts={}
        for identifier in set(self.pets.initial['levels'])|{int(k) for k in self.pets.raw_souls}|{int(k) for k in self.pets.level_updates}:
            key=str(identifier);entry=self.pets.entries.get(key,{})
            if (key in self.pets.unresolved_species or entry.get('sync_pending')
                    or key in self.pets.transitions and key in self.pets.raw_souls):continue
            facts[key]=dict(level=entry.get('level',self.pets.level_updates.get(key,self.pets.initial['levels'].get(identifier))),souls=self.pets.raw_souls.get(key),
                soul_baseline_source='experimental-sparse-zero-level1' if identifier in self.pets.initial.get('soul_defaults',{}) else '0090-explicit')
        recent=sorted(self.pets.session_counts,key=lambda key:self.pets.session_counts[key]['recent'],reverse=True)
        self.character_cache.remember(identity,facts,recent=recent)

    def _cache_snapshot(self,result):
        if self.mode=='sample':return
        # Durable weekly arithmetic only consumes fresh readings confirmed on
        # the same owned login. Historical/manual values never establish a base.
        gain_identity=self.resource_gains.identity
        accumulated=result.get('order_accumulated',{})
        latesran=result.get('weekly_orders',{}).get('latesran',{})
        result.setdefault('weekly_orders',{})['altgard']=dict(available=False,automatic_pending=True,
            observed_deliveries=len(self.altgard_orders.events),
            reading_error=accumulated.get('reading_error') or latesran.get('reading_error') or self.altgard_orders.problem,
            allow_cached=accumulated.get('allow_cached',True) and latesran.get('allow_cached',True))
        if (result.get('login_session',{}).get('confirmed') and identity_key(gain_identity)
                and accumulated.get('flow')==self._od_channel()
                and (accumulated.get('invalidated') or accumulated.get('available')
                     and latesran.get('flow')==accumulated.get('flow'))):
            previous=self.character_cache.select(self.reader.character,gain_identity) or {}
            presentation,durable=reconcile_orders(accumulated,previous.get('order_accumulator'),latesran,gain_identity)
            result['weekly_orders']['altgard']=presentation
            if durable:self.character_cache.remember(gain_identity,{},order_accumulator=durable)
        if not result.get('stale'):self._remember_pet_facts()
        # A quiet socket or a login boundary must not indefinitely postpone
        # persistence of already-confirmed facts from the previous snapshot.
        self.character_cache.flush()
        # Kinah can be identified by the same captured name/server as DPS when
        # the monitor starts mid-session. Persist only this confirmed resource;
        # do not treat that evidence as an initial pet collection or Od balance.
        gain_identity=self.resource_gains.identity
        kinah=result.get('resource_gains',{}).get('kinah_daily',{})
        if (self.running and self.mode=='live' and not self.error and not result.get('stale')
                and not self.resource_gains.awaiting_identity
                and self.resource_gains.flow==self._od_channel()
                and identity_key(gain_identity) and gain_identity['name']==self.reader.character
                and kinah.get('available') and not kinah.get('cached')):
            self.character_cache.remember(gain_identity,{},resource_gains={'kinah_daily':kinah})
            self.character_cache.flush()
        duty=result.get('daily_duty',{})
        entries=result.get('activity_entries',{})
        if (any(v.get('available') and not v.get('cached') for v in entries.values())
                and self.running and self.mode=='live' and not self.error and not result.get('stale')
                and not self.activity_entries.awaiting_identity and not self.resource_gains.awaiting_identity
                and identity_key(gain_identity) and gain_identity['name']==self.reader.character
                and self.activity_entries.flow==self._od_channel() and self.resource_gains.flow==self._od_channel()):
            self.character_cache.remember(gain_identity,{},activity_entries=entries)
        order=result.get('weekly_orders',{}).get('latesran',{})
        if (order.get('available') and not order.get('cached') and not order.get('reset_pending') and not order.get('manual') and self.running and self.mode=='live' and not self.error and not result.get('stale')
                and not self.resource_gains.awaiting_identity and identity_key(gain_identity)
                and gain_identity['name']==self.reader.character and self.resource_gains.flow==self._od_channel()):
            self.character_cache.remember(gain_identity,{},latesran=order)
        if (duty.get('available') and not duty.get('reset_pending') and not duty.get('manual') and self.running and self.mode=='live' and not self.error and not result.get('stale')
                and not self.resource_gains.awaiting_identity and identity_key(gain_identity)
                and gain_identity['name']==self.reader.character and self.resource_gains.flow==self._od_channel()):
            self.character_cache.remember(gain_identity,{},daily_duty=duty)
        identity=self.pets.identity
        # A login in progress may still carry the preceding character's pet
        # identity. Keep the record on disk, but never display/save a new login
        # under that previous identity while the new header is pending.
        if (self.od_energy.awaiting_identity or self.od_energy.wrong_character or
                self.resource_gains.flow and self.resource_gains.awaiting_identity):
            self.character_cache.decorate(result,None,allow_manual=False);return
        # Name/server is our strongest durable decoded identity. No account ID
        # or ephemeral actor/process/socket identifiers are persisted.
        confirmed=bool(identity_key(identity) and identity.get('name')==self.reader.character
            and self.pets.flow==self._od_channel() and self.pets.flow not in self.pets.blocked)
        if identity_key(identity) and identity['name']!=self.reader.character:
            self.character_cache.decorate(result,None,allow_manual=False);return
        if confirmed and self.running and self.mode=='live' and not result.get('stale') and not self.error:
            facts={}
            if self.pets.initial and not self.pets.account_gap:
                identifiers=set(self.pets.initial['levels'])|{int(k) for k in self.pets.raw_souls}|{int(k) for k in self.pets.level_updates}
                for identifier in identifiers:
                    key=str(identifier);entry=self.pets.entries.get(key,{})
                    if (key in self.pets.unresolved_species or entry.get('sync_pending')
                            or key in self.pets.transitions and key in self.pets.raw_souls):continue
                    facts[key]=dict(level=entry.get('level',self.pets.level_updates.get(key,self.pets.initial['levels'].get(identifier))),
                        souls=self.pets.raw_souls.get(key),
                        soul_baseline_source='experimental-sparse-zero-level1' if identifier in self.pets.initial.get('soul_defaults',{}) else '0090-explicit')
            recent=[e['pet_id'] for e in result['pet_collection'].get('entries',[])]
            classes=self.reader.group.classes.get((self.pets.flow,identity.get('actor'),identity['name']),set())
            self.character_cache.remember(identity,facts,result['od_energy'],recent,
                next(iter(classes)) if len(classes)==1 else None,
                resource_gains=result.get('resource_gains'))
            key=identity_key(identity)
            self.character_cache.flush(force=key!=self.cached_identity)
            if not self.character_cache.error:self.cached_identity=key
        record=self.character_cache.select(self.reader.character,identity if identity_key(identity) else None)
        self.character_cache.decorate(result,record,confirmed,allow_manual=False)

    def _resource_scope(self):
        identity=self.pets.identity or {}
        return (self.generation,self.reader.character,self._od_channel(),
                identity.get('name'),identity.get('server'),identity.get('actor'),
                self.od_energy.awaiting_identity,self.od_energy.wrong_character)

    def reload_login_cache(self):
        """Restore history and check ordinary updates; never send server requests."""
        with self.lock:
            self.snapshot()
            self.character_cache.reload()
            state=self.snapshot()
            self.resource_refresh.begin(state,self._resource_scope())
            return self.snapshot()

    def close_encounter(self,key):
        with self.lock:
            if key.startswith('grupo/'):
                self.reader.group.manual_ends[key]=self.snapshot()['clock_us']
                return
            for e in self.reader.meter.encounters:
                if e.key==key and e.end_us is None:
                    now=self.snapshot()['clock_us'];self.reader.meter.close(e.flow,e.target,now,'manual')

    def reset_combat(self):
        with self.lock:
            now=self.snapshot()['clock_us']
            self.reader.reset_combat(now);self.archives=[]

    def start_collection_diagnostic(self):
        with self.lock:
            if not self.running:raise ValueError('Inicie a captura antes do registro de mascotes.')
            if self.collection_diagnostic.active:raise ValueError('Um registro de mascotes já está em andamento. Salve o diagnóstico para encerrá-lo.')
            self.collection_diagnostic.start()

    def _capture_diagnostic_state(self):
        with self.lock:
            now=time.monotonic()
            return dict(status=self.status,error=self.error,running=self.running,mode=self.mode,
                capture_generation=self.generation,reader_generation=self.reader.generation,
                metadata=dict(self.metadata),logs=list(self.logs),available_interfaces=[dict(r) for r in self.available_interfaces],
                resource_login_trace=list(self.resource_gains.login_trace),
                activity_login_trace=list(getattr(self,'activity_login_trace',())),
                activity_reading_state=dict(
                    daily=dict(received=self.daily_duty.value is not None or self.daily_duty.pending is not None,
                        problem=self.daily_duty.problem),
                    latesran=dict(received=self.weekly_orders.value is not None,problem=self.weekly_orders.problem),
                    accumulated=dict(received=self.order_accumulator.value is not None,
                        login_boundary=self.order_accumulator.login_clock_us is not None,
                        problem=self.order_accumulator.problem),
                    entries=dict(received_keys=list(self.activity_entries.values),
                        pending_keys=list(self.activity_entries.pending),problems=dict(self.activity_entries.problems))),
                connection_trace=[dict(r) for r in self.capture_trace],trace_records_replaced=self.capture_trace_replaced,
                socket_retries=[dict(socket_key=key,attempts=retry['attempts'],
                    retry_in_seconds=max(0,round(retry['next_at']-now,2)),
                    exhausted=False,slow_retry=retry['attempts']>3,error=retry['error']) for key,retry in self.capture_retries.items()],
                pet_rejections=self.pets.rejection_samples(),
                directions=[dict(flow=flow,accepted=d.accepted,failed=d.failed,
                    heartbeats=d.heartbeats,midstream_skipped_bytes=d.midstream_skipped_bytes,
                    buffered_bytes=len(d.buf),pending_segments=len(d.pending),
                    process_id=self.flow_processes.get(flow),incoming_game_socket=flow in self.incoming_flows)
                    for flow,d in self.reader.directions.items()],
                trace_scope='current monitor capture session; exact game-process sockets only')

    def confirm_activity_counts(self,counts,expected_identity=None):
        raise ValueError('Os contadores são automáticos; não exigem preenchimento manual.')

    def export_collection_diagnostic(self):
        with self.lock:
            self.collection_diagnostic.stop()
            cache_state=self.snapshot()
            return dict(product='Império AION',kind='collection-diagnostic',monitor_version='0.8.80',character=self.reader.character,
                resource_refresh=cache_state.get('resource_refresh'),
                character_cache=cache_state.get('character_cache'),od_energy=cache_state.get('od_energy'),resource_gains=cache_state.get('resource_gains'),daily_duty=cache_state.get('daily_duty'),weekly_orders=cache_state.get('weekly_orders'),pet_collection=self.pets.snapshot(),recent_pet_messages=list(self.pets.recent_messages),
                activity_entries=cache_state.get('activity_entries'),
                altgard_diagnostic=self.altgard_orders.diagnostic(),order_accumulated=cache_state.get('order_accumulated'),
                automatic_diagnostic_path=self.last_auto_fault_path,
                capture_state=self._capture_diagnostic_state(),
                bindings=self.reader.snapshot().get('bindings',[]),**self.collection_diagnostic.snapshot())

    def _export_current(self):
        state=self.snapshot()
        state['events']=[dict(e,encounter_key=fight.key) for fight in self.reader.meter.encounters for e in fight.events]
        state['observed_damage_events']=[dict(e) for e in self.reader.group.timeline if e['kind']=='damage']
        state['metadata']=dict(self.metadata)
        state['capture_state']=self._capture_diagnostic_state()
        return state

    def export(self):
        with self.lock:
            current=self._export_current()
            current.update(schema_version=1,product='Império AION',version='0.8.80-beta',
                           mode=self.mode,status=self.status,error=self.error,logs=list(self.logs),
                           previous_connections=list(self.archives),lossless_handover_validated=False)
            return current
