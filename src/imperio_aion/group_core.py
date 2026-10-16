# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Independent experimental group metadata and shared-target arithmetic.

Protocol facts are referenced in FONTES-PROTOCOLO.txt. No third-party code.
Only explicit roster messages establish membership; nearby names do not.
"""
from dataclasses import dataclass, field
from collections import deque
import math
import struct
from inspect_wire import varint

# Observed trailing impacts arrived 0–151 ms after kill credit. Keep a bounded
# grace interval; an observed spawn always starts a different entity lifetime.
POST_DEATH_GRACE_US = 500_000


def trailing_impact(end_reason, end_us, clock, birth):
    return (end_reason == 'abate' and 0 <= clock-end_us <= POST_DEATH_GRACE_US
            and not (birth is not None and end_us <= birth <= clock))


def text_field(raw, limit=40):
    name=raw.decode('utf-8','strict')
    if not raw or len(raw)>limit or any(ord(c)<32 for c in name):
        raise ValueError('Nome inválido')
    return name


def player_info(body):
    if body[:2] not in (b'\x33\x36',b'\x45\x36'):
        raise ValueError('Opcode de jogador inválido')
    actor,at=varint(body,2);at+=4
    if not 0<actor<10_000_000 or at+2>len(body) or not body[at]&1:
        raise ValueError('Cabeçalho de jogador não reconhecido')
    size=body[at+1];at+=2
    if not 0<size<=40 or at+size+4>len(body):raise ValueError('Nome de jogador truncado')
    name=text_field(body[at:at+size]);at+=size
    cls=None
    if body[:2]==b'\x33\x36':
        server=int.from_bytes(body[at:at+2],'little')
        if not 1<=server<=9999:raise ValueError('Servidor inválido')
        if at+6<=len(body):
            candidate=int.from_bytes(body[at+2:at+6],'little')
            if 1<=candidate<=64:cls=candidate
    else:
        # Observed Global player headers carry class u32 immediately after name.
        cls=int.from_bytes(body[at:at+4],'little')
        if not 1<=cls<=64:raise ValueError('Classe fora do perfil de jogador')
        server=None
    return dict(actor=actor,name=name,server=server,class_id=cls,basis=body[:2].hex())


def party_roster(body):
    if body[:2]!=b'\x02\x97' or not 25<=len(body)<=8192:
        raise ValueError('Lista do grupo fora do perfil')
    at=6;party_key=int.from_bytes(body[2:6],'little');size=body[at];at+=1
    if size>64 or at+size+18>len(body):raise ValueError('Cabeçalho de grupo truncado')
    if size:text_field(body[at:at+size],64)
    at+=size;capacity=body[at];at+=18
    count,at=varint(body,at)
    if capacity>12 or count>12 or count>capacity:raise ValueError('Tamanho do grupo inválido')
    if count==0:
        if len(body)-at>32:raise ValueError('Saída do grupo fora do perfil')
        return dict(key=party_key,members=[])

    def header(pos,expected=None):
        if pos+11>len(body):return None
        mask,slot=body[pos:pos+2];dbid=int.from_bytes(body[pos+2:pos+10],'little')
        server=dbid>>48;n=body[pos+10]
        if slot>12 or expected is not None and slot!=expected:return None
        if not 1<=server<=9999 or n>40:return None
        if n==0:return dict(vacant=True,end=pos+11,slot=slot)
        end=pos+11+n
        if end+12>len(body):return None
        try:name=text_field(body[pos+11:end])
        except (ValueError,UnicodeDecodeError):return None
        cls,level,gear=struct.unpack_from('<III',body,end)
        if not 1<=cls<=64 or not 1<=level<=1000:return None
        return dict(name=name,server=server,dbid=dbid,slot=slot,mask=mask,
                    class_id=cls,end=end+12,vacant=False)

    # Enumerate bounded structural paths, accepting only a unique full list.
    # Variable tails are never interpreted as names without a matching header.
    paths=[(at,[],None)];solutions=[];steps=0
    while paths:
        pos,members,expected=paths.pop();steps+=1
        if steps>256:raise ValueError('Lista de grupo ambígua')
        h=header(pos,expected)
        if h is None:continue
        if h['vacant']:
            if len(body)-h['end']<=256:solutions.append(members)
            continue
        new=members+[dict(name=h['name'],server=h['server'],dbid=h['dbid'],class_id=h['class_id'])]
        if len(new)==count:
            if len(body)-h['end']<=128:solutions.append(new)
            continue
        for nxt in range(h['end'],min(len(body)-10,h['end']+80)):
            if header(nxt,h['slot']+1) is not None:paths.append((nxt,new,h['slot']+1))
    unique={tuple((m['dbid'],m['name']) for m in result):result for result in solutions}
    if len(unique)!=1:raise ValueError('Lista completa do grupo não reconhecida')
    members=next(iter(unique.values()))
    if len({(m['name'],m['server']) for m in members})!=len(members):
        raise ValueError('Nomes duplicados no grupo')
    return dict(key=party_key,members=members)


def hp_info(body):
    if body[:2]!=b'\x1b\x92':raise ValueError('Opcode de vida inválido')
    actor,at=varint(body,2);cur,at=varint(body,at);maximum,at=varint(body,at)
    if at!=len(body) or not 0<actor<10_000_000 or not 0<=cur<=maximum<=10_000_000_000 or maximum==0:
        raise ValueError('Vida fora do perfil estrito')
    return dict(target=actor,current=cur,maximum=maximum,basis='1b92',experimental=True)


def current_hp_info(body):
    """Strict NPC remaining-HP record. It does not carry a maximum."""
    if body[:2]!=b'\x00\x8d':raise ValueError('Opcode de vida atual inválido')
    actor,at=varint(body,2);kind,at=varint(body,at)
    _,at=varint(body,at);_,at=varint(body,at)
    if kind!=2 or not 0<actor<10_000_000 or len(body)-at!=8:
        raise ValueError('Vida atual de NPC fora do perfil')
    current=int.from_bytes(body[at:],'little')
    if current>10_000_000_000:raise ValueError('Vida atual fora do limite')
    return dict(target=actor,current=current,basis='008d-u64',experimental=True)


def spawn_hp(body):
    """Unique NPC coordinate/HP tuple within a bounded spawn header.

    A candidate needs the NPC template marker, finite XYZ coordinates and a
    plausible current/max varint pair. Ambiguity yields no HP denominator.
    """
    if body[:2]!=b'\x41\x36':raise ValueError('Opcode de nascimento inválido')
    raw,at=varint(body,2)
    if not 0<raw<2**32 or at+4>len(body):raise ValueError('Entidade inválida')
    actor=((raw&0x3fff)|0x4000) if raw>1_000_000 else raw
    # Summon/effect forms are outside this NPC health profile.
    if body[at] in (0x11,0x40):raise ValueError('Entidade não é mob no perfil')
    candidates=set()
    for mark in range(at+3,min(len(body)-15,at+128)):
        if body[mark:mark+3] not in (b'\x00\x00\x02',b'\x00\x40\x02'):continue
        code=int.from_bytes(body[mark-3:mark],'little')
        xyz=struct.unpack_from('<fff',body,mark+3)
        if not code or not all(math.isfinite(n) and abs(n)<1_000_000 for n in xyz):continue
        for tag in range(mark+15,min(len(body)-2,mark+79)):
            if body[tag]!=1:continue
            try:cur,end=varint(body,tag+1);maximum,end=varint(body,end)
            except ValueError:continue
            if 0<cur<=maximum<=10_000_000_000 and maximum>=100:
                candidates.add((cur,maximum,code))
    if len(candidates)!=1:raise ValueError('Vida máxima do mob ausente ou ambígua')
    cur,maximum,code=candidates.pop()
    return dict(target=actor,current=cur,maximum=maximum,npc_code=code,basis='4136-unique-npc-tuple',experimental=True)


def metrics(events,start,end):
    elapsed=max(0,end-start);damage=sum(e['candidate_amount'] for e in events)
    window=min(5_000_000,elapsed)
    recent=sum(e['candidate_amount'] for e in events if e['clock_us']<=end and
               (e['clock_us']>end-window or elapsed<5_000_000 and e['clock_us']==start))
    return dict(damage=damage,dps_average=damage/(elapsed/1e6) if elapsed else None,
                dps_current=recent/(window/1e6) if window else None,duration_us=elapsed)


@dataclass
class GroupFight:
    key:str
    flow:str
    target:int
    revision:int
    attempt:int
    members:list
    start_us:int
    events:list=field(default_factory=list)
    end_us:int|None=None
    end_reason:str|None=None
    partial:bool=False


class GroupBook:
    def __init__(self):
        self.names={};self.rosters={};self.timeline=[];self.health={};self.ids=set()
        self.manual_ends={};self.interrupted={};self.diagnostics=[];self.roster_errors=0
        self.lifetimes={};self.hp_anchors={}
        self.current_health=deque(maxlen=4000);self.current_health_dropped=0
        self.current_health_losses={};self.current_health_loss_scope_complete=True
        self.health_revisions={};self.damage_revisions={};self.hp_intervals_cache={}
        self.uncertain_damage={};self.uncertain_revision=0
        self.hp_closed={}
        self.classes={}

    def uncertain(self,ctx,target=None):
        if sum(len(v) for v in self.uncertain_damage.values())>=20000:
            raise ValueError('Limite de danos ambíguos atingido')
        self.uncertain_damage.setdefault(ctx['flow'],[]).append((ctx['clock_us'],target))
        self.uncertain_revision+=1

    def identify(self,flow,entry):
        key=(flow,entry['actor']);values=self.names.setdefault(key,set())
        values.add((entry['name'],entry.get('server')))
        if entry.get('class_id') is not None:
            self.classes.setdefault((flow,entry['actor'],entry['name']),set()).add(entry['class_id'])
        if len(self.names)>4000:raise ValueError('Limite de identidades do grupo atingido')

    def actor_for(self,flow,member):
        matches=[]
        roster=self.rosters.get(flow,{}).get('members',[])
        same_name=sum(m['name']==member['name'] for m in roster)
        for (f,actor),values in self.names.items():
            if f!=flow or {v[0] for v in values}!={member['name']}:continue
            servers={v[1] for v in values if v[1] is not None}
            if len(servers)>1:continue
            if servers=={member['server']} or not servers and same_name<=1:matches.append(actor)
        return matches[0] if len(matches)==1 else None

    def observe(self,ctx,body):
        op=body[:2];flow=ctx['flow'];birth=None
        if op in (b'\x02\x97',b'\x1b\x92',b'\x45\x36',b'\x41\x36',b'\x00\x8d'):
            if sum(d['opcode']==op.hex() for d in self.diagnostics)<(64 if op==b'\x00\x8d' else 16):
                self.diagnostics.append(dict(opcode=op.hex(),packet=ctx['packet'],flow=flow,
                                             clock_us=ctx['clock_us'],bytes=len(body),body_hex=body[:8192].hex()))
        try:
            if op==b'\x02\x97':
                roster=party_roster(body)
                previous=self.rosters.get(flow)
                def membership(value):
                    return (value['key'],[(m['name'],m['server'],m['dbid']) for m in value['members']]) if value else None
                self.rosters[flow]=roster
                if membership(roster)!=membership(previous):
                    self.timeline.append(dict(kind='roster',**ctx,roster=roster))
            elif op in (b'\x33\x36',b'\x45\x36'):self.identify(flow,player_info(body))
            elif op==b'\x00\x8d':
                hp=current_hp_info(body)
                if len(self.current_health)==self.current_health.maxlen:
                    removed=self.current_health[0];self.current_health_dropped+=1
                    removed_pair=(removed['flow'],removed['target'])
                    if removed_pair in self.current_health_losses or len(self.current_health_losses)<4000:
                        self.current_health_losses[removed_pair]=max(removed['clock_us'],self.current_health_losses.get(removed_pair,-1))
                    else:self.current_health_loss_scope_complete=False
                self.current_health.append(dict(**hp,flow=flow,clock_us=ctx['clock_us'],packet=ctx['packet']))
                pair=(flow,hp['target']);self.health_revisions[pair]=self.health_revisions.get(pair,0)+1
                # Zero is an explicit HP observation, independent of kill
                # credit. Require an NPC spawn so player HP cannot end a boss.
                if hp['current']==0 and self.living_npc(flow,hp['target'],ctx['clock_us']):
                    self.death(ctx,hp['target']);birth=dict(hp,death_confirmed=True)
            elif op in (b'\x1b\x92',b'\x41\x36'):
                hp=hp_info(body) if op==b'\x1b\x92' else spawn_hp(body)
                self.health.setdefault((flow,hp['target']),[]).append(dict(**hp,clock_us=ctx['clock_us']))
                pair=(flow,hp['target']);self.health_revisions[pair]=self.health_revisions.get(pair,0)+1
                if op==b'\x41\x36':
                    self.lifetimes.setdefault((flow,hp['target']),[]).append(('spawn',ctx['clock_us']))
                    self.timeline.append(dict(**ctx,kind='spawn',target=hp['target']))
                    birth=hp
        except (ValueError,UnicodeDecodeError,struct.error):
            if op==b'\x02\x97':
                self.roster_errors+=1
                if flow in self.rosters:
                    self.rosters.pop(flow,None)
                    self.timeline.append(dict(kind='roster',**ctx,roster=dict(key=None,members=[])))
        if len(self.timeline)>24000 or sum(len(v) for v in self.health.values())>4000:
            raise ValueError('Limite de metadados do grupo atingido')
        return birth

    def add(self,event):
        if event['actor']==event['target'] or event['id'] in self.ids:return
        if len(self.ids)>=20000:raise ValueError('Limite de 20.000 eventos de grupo atingido')
        self.ids.add(event['id']);self.timeline.append(dict(event,kind='damage'))
        pair=(event['flow'],event['target']);self.damage_revisions[pair]=self.damage_revisions.get(pair,0)+1

    def death(self,ctx,target):
        self.timeline.append(dict(**ctx,kind='death',target=target))
        self.lifetimes.setdefault((ctx['flow'],target),[]).append(('death',ctx['clock_us']))

    def living_npc(self,flow,target,clock):
        life=[(kind,stamp) for kind,stamp in self.lifetimes.get((flow,target),[]) if stamp<=clock]
        if not life or life[-1][0]!='spawn':return False
        born=life[-1][1]
        return any(h['basis']=='4136-unique-npc-tuple' and h['clock_us']==born
                   for h in self.health.get((flow,target),[]))

    def hp_reference(self,key,flow,target,start,end):
        """Pin one maximum per fight and reject conflicting denominators.

        Entity lifetimes exclude HP belonging to a previous mob reusing an ID.
        Receiving current=0 never clears or rewrites an established maximum.
        """
        floor=-1
        for kind,clock in self.lifetimes.get((flow,target),[]):
            if kind=='spawn' and clock<=start:floor=max(floor,clock)
            elif kind=='death' and clock<start:floor=max(floor,clock+1)
        values=sorted((h for h in self.health.get((flow,target),[]) if floor<=h['clock_us']<=end),key=lambda h:h['clock_us'])
        anchor_key=(key,start,floor)
        if anchor_key not in self.hp_anchors and values:
            before=[h for h in values if h['clock_us']<=start]
            # Prefer the strict explicit HP record if it was available at start.
            explicit=[h for h in before if h['basis']=='1b92']
            self.hp_anchors[anchor_key]=dict((explicit or before)[-1] if before else values[0])
        anchor=self.hp_anchors.get(anchor_key)
        maximum=anchor['maximum'] if anchor else None
        conflict=bool(anchor and any(h['maximum']!=maximum for h in values if h['clock_us']>=anchor['clock_us']))
        return dict(max_hp=maximum,hp_basis=anchor['basis'] if anchor else None,
                    hp_status='conflict' if conflict else 'candidate' if anchor else 'pending',
                    hp_reference_us=anchor['clock_us'] if anchor else None,
                    hp_percent_available=bool(anchor and not conflict))

    def hp_contribution(self,key,flow,target,start,end,actor,closed=False):
        """Attribute observed HP loss only to an unambiguous attack source.

        Displayed damage and server HP have different units in the SA capture.
        Never divide displayed damage by server HP, infer a universal scale,
        allocate mixed sources proportionally, or turn kill credit into HP=0.
        """
        hp=self.hp_reference(key,flow,target,start,end)
        unavailable=dict(hp_percent=None,hp_percent_basis='observed-hp-loss',
                         hp_damage=None,hp_unattributed=None,hp_percent_status='pending')
        if actor is None or not hp['hp_percent_available']:return unavailable
        frozen_key=(key,start,end,actor)
        if closed and frozen_key in self.hp_closed:return dict(self.hp_closed[frozen_key])
        ref=hp['hp_reference_us']
        anchor=next((h for h in self.health.get((flow,target),[])
                     if h['clock_us']==ref and h['maximum']==hp['max_hp'] and h['basis']==hp['hp_basis']),None)
        if anchor is None or ref>start:return unavailable
        cache_key=(flow,target,ref,hp['max_hp'],anchor['current'])
        revision=(self.health_revisions.get((flow,target),0),self.damage_revisions.get((flow,target),0),self.uncertain_revision)
        cached=self.hp_intervals_cache.get(cache_key)
        if cached is None or cached[0]!=revision:
            # Coalesce updates sharing a capture timestamp. All damage sources,
            # including non-party players, participate in ambiguity detection.
            points={ref:anchor['current']}
            for h in self.health.get((flow,target),[]):
                if h['clock_us']>ref:points[h['clock_us']]=h['current']
            for h in self.current_health:
                if h['flow']==flow and h['target']==target and h['clock_us']>=ref:
                    points[h['clock_us']]=h['current']
            attacks=sorted((e for e in self.timeline if e['kind']=='damage' and
                            e['flow']==flow and e['target']==target and e['clock_us']>=ref),key=lambda e:e['clock_us'])
            intervals=[];at=0;previous=anchor['current'];previous_clock=ref
            for clock,current in sorted(points.items()):
                if clock==ref:continue
                sources=set();damage_count=0;first_damage=None
                while at<len(attacks) and attacks[at]['clock_us']<=clock:
                    e=attacks[at];at+=1
                    if e['clock_us']>previous_clock or previous_clock==ref and e['clock_us']==ref:
                        sources.add(e['actor']);damage_count+=1
                        if first_damage is None:first_damage=e['clock_us']
                drop=max(0,previous-current)
                uncertain=any((t is None or t==target) and previous_clock<c<=clock
                              for c,t in self.uncertain_damage.get(flow,[]))
                intervals.append(dict(clock_us=clock,previous_us=previous_clock,
                                      drop=drop,actor=next(iter(sources)) if len(sources)==1 and not uncertain else None,
                                      first_damage_us=first_damage,records=damage_count,regenerated=max(0,current-previous)))
                previous=current;previous_clock=clock
            self.hp_intervals_cache[cache_key]=(revision,intervals)
        else:intervals=cached[1]
        relevant=[dict(i,actor=None) if i['first_damage_us'] is not None and i['first_damage_us']<start else i
                  for i in intervals if start<=i['clock_us']<=end]
        # The bounded diagnostic ring is shared by every entity. Losing an
        # old player's/NPC's HP must not invalidate another complete fight.
        # A lost point at/after this reference still makes its intervals unsafe.
        loss=self.current_health_losses.get((flow,target),-1)
        unknown_loss=self.current_health_dropped and (not self.current_health_losses or not self.current_health_loss_scope_complete)
        if loss>=ref or unknown_loss:return dict(unavailable,hp_percent_status='history-incomplete')
        if not relevant:return unavailable
        observed=sum(i['drop'] for i in relevant if i['actor']==actor)
        unknown=sum(i['drop'] for i in relevant if i['actor'] is None)
        own_events=any(e['kind']=='damage' and e['flow']==flow and e['target']==target and
                       e['actor']==actor and start<=e['clock_us']<=end for e in self.timeline)
        if own_events and not any(i['actor']==actor for i in relevant):
            return dict(unavailable,hp_unattributed=unknown,hp_percent_status='ambiguous' if unknown else 'pending')
        result=dict(hp_percent=observed/hp['max_hp']*100,hp_percent_basis='observed-hp-loss',
                    hp_damage=observed,hp_unattributed=unknown,
                    hp_percent_status='partial' if unknown else 'observed')
        if closed:self.hp_closed[frozen_key]=dict(result)
        return result

    def snapshot(self,now):
        rosters={};revisions={};current={};attempts={};fights=[];active=None
        actor_cache={};indexes={};births={}
        def actor_for(member,flow):
            key=(flow,member['name'],member['server'])
            if key not in actor_cache:actor_cache[key]=self.actor_for(flow,member)
            return actor_cache[key]
        for item in sorted(enumerate(self.timeline),key=lambda pair:(pair[1]['clock_us'],pair[0])):
            e=item[1];flow=e['flow'];clock=e['clock_us']
            if e['kind']=='roster':
                for f in current.values():
                    if f.flow==flow and f.end_us is None:
                        f.end_us=clock;f.end_reason='grupo alterado';f.partial=True
                rosters[flow]=e['roster']['members'];revisions[flow]=revisions.get(flow,0)+1
                indexes[flow]={actor_for(m,flow):m for m in rosters[flow] if actor_for(m,flow) is not None}
                continue
            pair=(flow,e['target']);f=current.get(pair)
            if e['kind']=='spawn':
                births[pair]=clock
                if f and f.end_us is None and clock>f.start_us:
                    f.end_us=clock;f.end_reason='novo nascimento';f.partial=True
                continue
            if f and f.end_us is None and f.key in self.manual_ends and clock>self.manual_ends[f.key]:
                f.end_us=self.manual_ends[f.key];f.end_reason='manual'
            if e['kind']=='death':
                if f and f.end_us is None:f.end_us=max(clock,f.start_us);f.end_reason='abate'
                continue
            members=rosters.get(flow,[])
            member=indexes.get(flow,{}).get(e['actor'])
            if member is None:continue
            if f and f.end_us is not None and trailing_impact(f.end_reason,f.end_us,clock,births.get(pair)):
                if f.revision==revisions[flow]:
                    f.events.append(dict(e,actor_name=member['name'],late_after_death=True))
                continue
            unconfirmed_life=bool(f and f.end_reason=='abate' and
                                  not (births.get(pair) is not None and births[pair]>=f.end_us))
            if f is None or f.end_us is not None:
                revision=revisions[flow];key=(flow,revision,e['target'])
                attempts[key]=attempts.get(key,0)+1
                f=GroupFight(f"grupo/{flow}/{revision}/{e['target']}/{attempts[key]}",flow,e['target'],revision,
                             attempts[key],[dict(m) for m in members],clock)
                current[pair]=f;fights.append(f)
                if unconfirmed_life:f.partial=True
            f.events.append(dict(e,actor_name=member['name']))
            active=f.key
        result=[];valid_events=[]
        for f in fights:
            if f.end_us is None and f.key in self.manual_ends:
                f.end_us=self.manual_ends[f.key];f.end_reason='manual'
            if f.end_us is None and f.flow in self.interrupted:
                f.end_us=max(f.start_us,self.interrupted[f.flow]);f.end_reason='interrompido';f.partial=True
            end=f.end_us if f.end_us is not None else now
            hp=self.hp_reference(f.key,f.flow,f.target,f.start_us,end)
            rows=[]
            for member in f.members:
                actor=actor_for(member,f.flow);events=[e for e in f.events if e['actor_name']==member['name'] and e['actor']==actor]
                stats=metrics(events,f.start_us,end) if actor is not None else dict(damage=None,dps_average=None,dps_current=None,duration_us=max(0,end-f.start_us))
                skills={}
                for e in events:
                    skill=skills.setdefault(e['raw_skill'],dict(code=e['raw_skill'],name=e['skill_name'],damage=0,records=0))
                    skill['damage']+=e['candidate_amount'];skill['records']+=1
                rows.append(dict(**member,actor=actor,identified=actor is not None,**stats,
                                 **self.hp_contribution(f.key,f.flow,f.target,f.start_us,end,actor,f.end_us is not None),
                                 skills=sorted(skills.values(),key=lambda s:-s['damage'])))
            rows.sort(key=lambda r:(r['damage'] is None,-(r['damage'] or 0),r['name']))
            stats=metrics(f.events,f.start_us,end)
            for row in rows:
                damage=row.get('damage')
                row['damage_share_percent']=(damage/stats['damage']*100
                    if type(damage) in (int,float) and stats['damage']>0 else 0.0
                    if damage==0 else None)
                row['damage_share_basis']='recorded-group-damage-on-this-target'
            result.append(dict(key=f.key,flow=f.flow,target=f.target,revision=f.revision,attempt=f.attempt,
                               start_us=f.start_us,end_us=f.end_us,end_reason=f.end_reason,partial=f.partial,
                               **stats,dps=stats['dps_average'],records=len(f.events),members=rows,
                               **hp,
                               experimental=True,skills=[],dps_screen_validated=False))
            valid_events.extend(dict(e,encounter_key=f.key) for e in f.events)
        return dict(group_encounters=result,group_events=valid_events,
                    player_classes=[dict(flow=f,actor=a,name=n,class_id=next(iter(c))) for (f,a,n),c in self.classes.items() if len(c)==1],
                    group_active_key=active,
                    group_rosters=[dict(flow=flow,**r) for flow,r in self.rosters.items()],
                    group_roster_errors=self.roster_errors,group_diagnostics=list(self.diagnostics),
                    mob_health_history=[dict(flow=flow,**h) for (flow,target),values in self.health.items() for h in values],
                    mob_current_hp_history=list(self.current_health),
                    mob_current_hp_history_dropped=self.current_health_dropped,
                    mob_current_hp_history_loss=[dict(flow=flow,target=target,last_dropped_us=clock)
                        for (flow,target),clock in self.current_health_losses.items()],
                    mob_current_hp_loss_scope_complete=self.current_health_loss_scope_complete,
                    group_automatic_validated=False,mob_max_hp_validated=False)
