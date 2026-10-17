# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Local combat actor from bounded, owned incoming self-stat records.

Protocol fact: 4a36 is the local player's stat update. Reference:
https://github.com/taengu/A2Tools-DPS-Meter/blob/babe35e6f236c0d30b1cf0bdaa476663e305e3d1/src-tauri/src/capture/stream_processor.rs
Own implementation, not imported code. The SA diagnostic of 2026-10-16 validates
the strict list profile below: count, count*(u16 key,i32 value), eight zero bytes.
Damage, class, packet frequency and saved names never choose the local actor.
"""
from inspect_wire import varint


def self_stats_actor(body):
    if not isinstance(body,bytes) or body[:2]!=b'\x4a\x36':
        raise ValueError('Not a self-stat record')
    actor,at=varint(body,2)
    if not 0<actor<10_000_000 or at>=len(body):raise ValueError('Invalid self-stat actor')
    count=body[at];at+=1
    if count>64 or len(body)!=at+count*6+8 or body[-8:]!=bytes(8):
        raise ValueError('Unsupported self-stat list')
    keys=[int.from_bytes(body[at+i*6:at+i*6+2],'little') for i in range(count)]
    if keys!=sorted(set(keys)) or any(not 0<key<=4096 for key in keys):
        raise ValueError('Invalid self-stat keys')
    return actor


class LocalCombatIdentity:
    """Three distinct packets in one verified flow, bounded to 12 seconds."""
    def __init__(self):
        self.flow=None;self.actor=None;self.candidate=None;self.packets=set()
        self.first_us=None;self.confirmed_first_us=None

    def observe(self,ctx,body,owned_incoming):
        if not owned_incoming or ctx['flow']!=owned_incoming:return None
        if self.flow!=owned_incoming:self.__init__();self.flow=owned_incoming
        try:actor=self_stats_actor(body)
        except (ValueError,IndexError):
            self.candidate=None;self.packets.clear();self.first_us=None;return None
        clock=ctx['clock_us']
        if actor!=self.candidate or self.first_us is None or not 0<=clock-self.first_us<=12_000_000:
            self.candidate=actor;self.packets={ctx['packet']};self.first_us=clock
        elif len(self.packets)<3:self.packets.add(ctx['packet'])
        if len(self.packets)>=3 and self.actor!=actor:
            self.actor=actor;self.confirmed_first_us=self.first_us
        if self.actor!=actor:return None
        return dict(actor=actor,first_us=self.confirmed_first_us,basis='owned-self-stats-4a36')


def current_party_identity(book,flow,actor):
    """A proven local actor plus a complete current roster, never damage guessing.

    A name is accepted if already uniquely mapped to that actor, or if exactly
    one roster member remains after every other member has a distinct live actor.
    No roster, multiple unknowns, aliases or same-name realms remain unnamed.
    """
    members=book.rosters.get(flow,{}).get('members',[])
    if not members or len(members)>10:return None
    if len({(m['name'],m['server']) for m in members})!=len(members):return None
    mapped=[(m,book.actor_for(flow,m)) for m in members]
    known=[a for _,a in mapped if a is not None]
    if len(known)!=len(set(known)):return None
    direct=[m for m,a in mapped if a==actor]
    unknown=[m for m,a in mapped if a is None]
    member=direct[0] if len(direct)==1 else unknown[0] if not direct and len(unknown)==1 else None
    if not member or not 1000<=member['server']<3000:return None
    existing=book.names.get((flow,actor),set())
    if any(name!=member['name'] or realm not in (None,member['server']) for name,realm in existing):return None
    return dict(member,actor=actor,basis='owned-self-stats-and-current-party')
