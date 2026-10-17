# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""SA party-player vitals, independent of names, damage and stored actors.

The observed 1b92 HP/MP/stamina profile is distinct from a generic NPC HP
record. In the local SA captures it addresses the other party players only.
The upstream protocol reference also documents party-only 1b92 player updates:
https://github.com/taengu/A2Tools-DPS-Meter/blob/babe35e6f236c0d30b1cf0bdaa476663e305e3d1/src-tauri/src/capture/stream_processor.rs
Independent strict implementation; unknown layouts are never membership proof.
"""
from inspect_wire import varint


def party_vitals_actor(body):
    if not isinstance(body,bytes) or body[:2]!=b'\x1b\x92':
        raise ValueError('Not party-player vitals')
    actor,at=varint(body,2);hp,at=varint(body,at);maximum,at=varint(body,at)
    if not 100<=actor<10_000_000 or not 0<=hp<=maximum<=50_000_000 or not maximum:
        raise ValueError('Invalid player vitals')
    tail=body[at:]
    if len(tail)!=25 or tail[8:16]!=bytes(8) or tail[-1] not in (0,1):
        raise ValueError('Unsupported party-player vitals profile')
    mana=int.from_bytes(tail[:4],'little');max_mana=int.from_bytes(tail[4:8],'little')
    stamina=int.from_bytes(tail[16:20],'little');max_stamina=int.from_bytes(tail[20:24],'little')
    if not 0<=mana<=max_mana<=1_000_000 or max_mana==0 or max_stamina!=150_000 or stamina>max_stamina:
        raise ValueError('Invalid party-player resources')
    return actor


class PartyVitalsScope:
    """Three distinct owned incoming packets within 12 s per roster epoch."""
    def __init__(self):
        self.flow=None;self.epoch=None;self.candidates={}

    def observe(self,ctx,body,owned_incoming,epoch):
        if not owned_incoming or ctx['flow']!=owned_incoming:return None
        if (self.flow,self.epoch)!=(owned_incoming,epoch):
            self.__init__();self.flow=owned_incoming;self.epoch=epoch
        try:actor=party_vitals_actor(body)
        except (ValueError,IndexError):return None
        if actor not in self.candidates and len(self.candidates)>=12:return None
        clock=ctx['clock_us'];prior=self.candidates.get(actor)
        if prior is None or not 0<=clock-prior['first_us']<=12_000_000:
            prior=dict(first_us=clock,packets=set());self.candidates[actor]=prior
        if len(prior['packets'])<3:prior['packets'].add(ctx['packet'])
        if len(prior['packets'])<3:return None
        return dict(actor=actor,epoch=epoch,first_us=prior['first_us'],confirmed_us=clock,
            basis='owned-party-vitals-sa-1b92')
