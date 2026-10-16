# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded 8356 login counter table. Only Latesran is mapped here.

The table contains sorted 17-byte counter records after a varint count,
followed by an opaque 130-byte extension in both recorded login profiles.
The known Latesran counter is 5 before weekly rollover and explicitly 0
in the new login. No counter has been validated as Altgard weekly completions.
"""
from datetime import datetime
import time
from inspect_wire import varint
from daily_duty import BRASILIA
from weekly_orders import LATESRAN_COUNTER,next_order_reset


def decode_login_latesran(body,now=None):
    now=time.time() if now is None else now
    if body[:2]!=b'\x83\x56':raise ValueError('Outra lista inicial')
    count,at=varint(body,2)
    if not 1<=count<=4096 or len(body)!=at+17*count+130:
        raise ValueError('Lista inicial de contadores fora do perfil')
    end=at+17*count
    if body[end]!=23:raise ValueError('Extensão inicial fora do perfil')
    previous=0;matched=None
    for offset in range(at,end,17):
        identifier=int.from_bytes(body[offset:offset+4],'little')
        current=int.from_bytes(body[offset+4:offset+8],'little')
        stamp=int.from_bytes(body[offset+8:offset+16],'little')
        flag=body[offset+16]
        # Unmapped counters carry their own flag semantics (observed 0x40 on
        # unrelated counters). Keep that byte opaque; apply the validated flag
        # profile only to the Latesran record actually interpreted here.
        if not previous<identifier<2**31 or stamp and not 1_500_000_000_000<=stamp<=2_500_000_000_000:
            raise ValueError('Registro inicial fora do perfil')
        previous=identifier
        if identifier==LATESRAN_COUNTER:
            if current>5 or flag not in (0,1) or stamp==0 and (current!=0 or flag!=0):
                raise ValueError('Saldo inicial Latesran fora do perfil')
            reference=stamp/1000 if stamp else now
            reset=next_order_reset(datetime.fromtimestamp(reference,BRASILIA)).timestamp()
            if now>=reset:raise ValueError('Contador inicial pertence à semana anterior')
            matched=dict(current=current,limit=5,reset_at=reset,observed_at=now,
                source='8356-initial-counter',experimental=True)
    if matched is None:raise ValueError('Contador Latesran ausente')
    return matched
