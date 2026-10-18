# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit player headers and names paired with proven effect owners.

Independent SA profiles checked against later player headers in local captures.
The caster and target of effects are NOT interchangeable. Accept only the exact
Aura Divina / Armadilha de Explosao caster profiles, or the Petrificacao target
profile. In particular, 1c effects and arbitrary model IDs are not identities.
Protocol research reference (GPL-3.0): taengu/A2Tools-DPS-Meter,
0fdd7d2, src-tauri/src/capture/stream_processor.rs (spawn owner/caster fields).
No damage, roster membership, login or resource baseline is inferred here.
"""
from collections import Counter
from inspect_wire import varint


def effect_player(body):
    if not isinstance(body,bytes) or body[:2]!=b'\x41\x36' or len(body)>300:
        raise ValueError('Not a supported named effect')
    spawned,at=varint(body,2)
    if not 100<=spawned<10_000_000 or at+4>len(body):
        raise ValueError('Invalid effect header')
    mask=int.from_bytes(body[at:at+2],'little')
    if body[at+2]!=1 or not 0<body[at+3]<=40:
        raise ValueError('Not an exact inline-name subtree')
    size=body[at+3];start=at+4;end=start+size
    if end+6>len(body):raise ValueError('Truncated effect name')
    name=body[start:end].decode('utf8','strict')
    if any(ord(c)<32 for c in name):raise ValueError('Invalid effect name')
    server=None
    if mask==0x1f:
        # Pinned short SA profiles. Longer or other effects remain undecoded.
        prefix=body[end:end+6]
        if prefix==bytes.fromhex('c8902c004002') and len(body)-end in (148,154):
            owner_at=end+126
        elif prefix==bytes.fromhex('b2902c000002') and len(body)-end==149:
            owner_at=end+127
        else:raise ValueError('Unsupported caster effect')
        if body[owner_at-3:owner_at]!=b'\x07\x02\x06':
            raise ValueError('Missing exact caster field')
        actor=int.from_bytes(body[owner_at:owner_at+4],'little')
        basis='4136-named-effect-caster'
    elif mask==0x5f:
        # Named summon owner, including its realm/guild block. These are not
        # the 1f caster records: interpreting the owner block as buff data lost
        # this Global/SA variant. Both models were matched to later 4536 names.
        prefix=body[end:end+6]
        if prefix==bytes.fromhex('c7902c004002'):
            owner_at=end+126;tail_size=42
        elif prefix==bytes.fromhex('67912c000002'):
            owner_at=end+125;tail_size=24
        else:raise ValueError('Unsupported named summon model')
        if owner_at+13>len(body) or body[owner_at-3:owner_at]!=b'\x07\x02\x06':
            raise ValueError('Missing summon owner field')
        actor=int.from_bytes(body[owner_at:owner_at+4],'little')
        if body[owner_at+8:owner_at+10]!=bytes(2):
            raise ValueError('Invalid summon owner guild block')
        server=int.from_bytes(body[owner_at+10:owner_at+12],'little')
        n=body[owner_at+12];guild_end=owner_at+13+n
        if not 1000<=server<3000 or n>40 or guild_end+tail_size!=len(body):
            raise ValueError('Invalid summon owner realm/length')
        guild=body[owner_at+13:guild_end].decode('utf8','strict')
        if any(ord(c)<32 for c in guild):raise ValueError('Invalid guild name')
        tail=body[guild_end:]
        if tail[:2]!=b'\x01\0' or tail[2]>12 or tail[3:18]!=bytes(15):
            raise ValueError('Unsupported summon owner footer')
        if (tail_size==24 and tail[18:]!=b'\0\x0a\0\0\0\0' or
                tail_size==42 and (tail[18]!=3 or tail[-5:]!=b'\x2d\0\0\0\0')):
            raise ValueError('Unsupported summon owner attributes')
        basis='4136-named-summon-owner'
    elif mask in (0x14,0x54):
        if body[end:end+6]!=bytes.fromhex('1a902c000002'):
            raise ValueError('Unsupported target effect')
        owner_at=end+159
        if owner_at+4>len(body) or body[owner_at-1]!=6:
            raise ValueError('Missing exact target field')
        actor=int.from_bytes(body[owner_at:owner_at+4],'little')
        if mask==0x14:
            if body[owner_at+4:]!=b'\0\x2d\0\0\0\0':
                raise ValueError('Unsupported guildless target footer')
        else:
            if owner_at+13>len(body) or body[owner_at+8:owner_at+10]!=bytes(2):
                raise ValueError('Invalid target guild block')
            server=int.from_bytes(body[owner_at+10:owner_at+12],'little')
            n=body[owner_at+12];guild_end=owner_at+13+n
            if not 1000<=server<3000 or n>40 or guild_end+24!=len(body):
                raise ValueError('Invalid target realm/length')
            guild=body[owner_at+13:guild_end].decode('utf8','strict')
            if any(ord(c)<32 for c in guild):raise ValueError('Invalid guild name')
            tail=body[guild_end:]
            if tail[:2]!=b'\x02\0' or tail[2]>12 or tail[3:19]!=bytes(16) or tail[19:]!=b'\x2d\0\0\0\0':
                raise ValueError('Unsupported target footer')
        basis='4136-named-effect-target'
    else:raise ValueError('Effect does not declare a supported name relationship')
    if not 100<=actor<10_000_000 or actor==spawned:
        raise ValueError('Invalid player id')
    return dict(actor=actor,name=name,server=server,basis=basis)


class CombatNames:
    """A name can label only an independently proven current self/party actor."""
    def __init__(self):
        self.scope=None;self.pending={};self.validated={};self.applied=set();self.events=[];self.counts=Counter()

    def observe(self,ctx,body,owned_flow,book):
        if not isinstance(body,bytes) or not owned_flow or ctx['flow']!=owned_flow:return
        epoch=book.roster_epochs.get(owned_flow,0)
        if self.scope!=(owned_flow,epoch):
            self.scope=(owned_flow,epoch);self.pending={};self.validated={};self.applied=set()
        if body[:2]==b'\x42\x36':
            # An exact entity-removal record ends this actor's naming evidence.
            # A later local proof must wait for a NEW explicit name, even in
            # the same flow/roster epoch. Death (flag3) is not removal (flag7).
            try:
                actor,at=varint(body,2);zero,at=varint(body,at);flag,at=varint(body,at)
            except ValueError:return
            if at==len(body) and zero==0 and flag==7:
                for entries in (self.pending,self.validated):
                    for key in list(entries):
                        if key[0]==actor:entries.pop(key)
                self.applied={key for key in self.applied if key[0]!=actor}
                self.counts['explicit_actor_removals_seen']+=1
            return
        if body[:2]==b'\x05\x8a':
            # This character/member list has no demonstrated link to the
            # active local actor. One entry is not proof that it names self.
            # Nearby 338a messages carry guild metadata, not an own-profile
            # inspection. Keep this source unbound even after self stats.
            self.counts['unbound_character_lists_ignored']+=1
            return
        try:
            if body[:2] in (b'\x33\x36',b'\x45\x36'):
                # A world player header without a realm still names the local
                # actor once self stats independently prove its id. Do not
                # wait for a complete login identity just to label combat.
                from group_core import player_info
                entry=player_info(body)
                self.counts['explicit_player_headers_seen']+=1
                entry={k:entry[k] for k in ('actor','name','server','basis')}
                entry['basis']+='-explicit-player-header'
                # GroupBook already consumes peer headers. This queue needs
                # them only until the local actor is proven, then only self.
                own=book.local_actor_epochs.get((owned_flow,epoch))
                if own and own['actor']!=entry['actor']:return
            else:
                entry=effect_player(body)
                self.counts['supported_effect_names_seen']+=1
        except (ValueError,UnicodeError,IndexError):return
        key=(entry['actor'],entry['name'],entry['server'])
        if key in self.validated:return
        if key not in self.pending and len(self.pending)>=64:return
        self.pending[key]=(dict(ctx),entry)

    def apply(self,book,owned_flow,now):
        if not owned_flow or self.scope!=(owned_flow,book.roster_epochs.get(owned_flow,0)):
            return []
        flow,epoch=self.scope;result=[]
        own=book.local_actor_epochs.get((flow,epoch))
        # A name already tied to a proven party actor is a validated binding,
        # not an unconfirmed candidate. Keep up to 64 in this flow/epoch so a
        # later self-stat proof can promote the same actor without another
        # name packet. Never carry these across a connection/roster epoch.
        candidates=dict(self.pending)
        candidates.update(self.validated)
        for key,(ctx,entry) in list(candidates.items()):
            age=now-ctx['clock_us']
            validated=key in self.validated
            if age>30_000_000 and not validated:
                self.pending.pop(key,None);continue
            local=bool(own and own['actor']==entry['actor'])
            application_key=key+(local,)
            if application_key in self.applied or age<0:continue
            proof=(own if own and own['actor']==entry['actor'] else
                   book.party_actor_proofs.get((flow,epoch,entry['actor'])))
            # An explicit name may precede the first party-vitals packet in
            # the same arrival burst. Wait for the independent proof; a name
            # never grants membership or extends the damage interval. Bound
            # this reorder allowance to 2 s within the same owned epoch.
            if (not proof or proof['first_us']>now or
                    (not validated and ctx['clock_us']+2_000_000<proof['first_us'])):
                continue
            if (book.living_npc(flow,entry['actor'],ctx['clock_us']) or
                    book.living_npc(flow,entry['actor'],now)):continue
            roster=book.rosters.get(flow,{}).get('members',[])
            matches=[m for m in roster if m['name']==entry['name'] and
                     (entry['server'] is None or m['server']==entry['server'])]
            if roster and len(matches)!=1:continue
            book.identify(flow,entry)
            self.applied.add(application_key)
            self.pending.pop(key,None)
            if key not in self.validated and len(self.validated)>=64:
                expired=next(iter(self.validated));self.validated.pop(expired)
                self.applied.discard(expired+(False,));self.applied.discard(expired+(True,))
            self.validated[key]=(ctx,entry)
            event=dict(entry,epoch=epoch,packet=ctx['packet'],source_clock_us=ctx['clock_us'],
                       applied_clock_us=now,self_identity=bool(own and own['actor']==entry['actor']))
            self.events.append(event);self.events=self.events[-64:]
            self.counts['self_names_applied' if local else 'party_names_applied']+=1
            result.append(event)
        return result

    def diagnostic(self):
        """Bounded numeric evidence counters; no raw data or player names."""
        keys=('explicit_player_headers_seen','supported_effect_names_seen',
              'unbound_character_lists_ignored','explicit_actor_removals_seen',
              'self_names_applied','party_names_applied')
        return dict(schema=1,pending_explicit_names=len(self.pending),validated_explicit_names=len(self.validated),
                    **{key:self.counts[key] for key in keys})


def explicit_local_identity(book,flow,proof):
    """Only a unique explicit name+realm can change the application identity."""
    values=book.names.get((flow,proof['actor']),set())
    names={n for n,s in values};realms={s for n,s in values if s is not None}
    if len(names)!=1 or len(realms)!=1:return None
    realm=next(iter(realms))
    if not 1000<=realm<3000:return None
    return dict(actor=proof['actor'],name=next(iter(names)),server=realm,
                basis='owned-self-stats-and-explicit-player-name')
