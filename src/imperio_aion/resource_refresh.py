# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Observe ordinary server updates. No requests, input automation or parser seeding."""
import time

NAMES = {'od_energy': 'Energia Od', 'kinah_daily': 'Kinah diário',
         'abyss_weekly': 'Abismo semanal'}


def readings(snapshot):
    return dict(od_energy=snapshot.get('od_energy', {}),
                **{key: snapshot.get('resource_gains', {}).get(key, {})
                   for key in ('kinah_daily', 'abyss_weekly')})


def token(value):
    if value.get('available') and not value.get('cached'):
        return value.get('clock_us')
    return None


class ResourceRefresh:
    """A bounded check helps validate whether opening the game's panel refreshes it.

    Expiry means no new confirmation, not proof of desynchronization. Repeated
    reloads do not postpone expiry. Ordinary updates remain active after expiry.
    """
    DURATION = 45

    def __init__(self):
        self.started = None
        self.scope = None
        self.baseline = {}
        self.received = set()
        self.status = 'idle'

    def begin(self, snapshot, scope, now=None):
        if self.status == 'watching':
            return
        if snapshot.get('mode') != 'live' or not snapshot.get('running'):
            return
        self.started = time.monotonic() if now is None else now
        self.scope = scope
        self.baseline = {key: token(value) for key, value in readings(snapshot).items()}
        self.received = set()
        self.status = 'watching'

    def snapshot(self, state, scope, now=None):
        now = time.monotonic() if now is None else now
        current = readings(state)
        if self.status == 'watching':
            if scope != self.scope or not state.get('running'):
                self.status = 'interrupted'
            else:
                for key, value in current.items():
                    observed = token(value)
                    if observed is not None and observed != self.baseline[key]:
                        self.received.add(key)
                    if not value.get('available') or value.get('cached'):
                        self.received.discard(key)
                if len(self.received) == len(NAMES):
                    self.status = 'confirmed'
                elif now - self.started >= self.DURATION:
                    self.status = 'unconfirmed'
        pending = [key for key in NAMES if key not in self.received]
        seconds = max(0, int(self.DURATION - (now - self.started))) if self.status == 'watching' else 0
        if self.status == 'watching':
            detail = ('A leitura continua durante o farm. Abra a janela de recursos no jogo se necessário. '
                      f'Esta verificação manual termina em {seconds} s; a leitura permanece ativa.')
        elif self.status == 'confirmed':
            detail = 'Od, Kinah e Abismo receberam novas leituras do jogo, sem relog. A leitura contínua permanece ativa.'
        elif self.status == 'unconfirmed':
            detail = ('Sem confirmação nova de: ' + ', '.join(NAMES[key] for key in pending) +
                      '. Os últimos valores foram preservados e a leitura continua durante o farm. Se estiverem dessincronizados, '
                      'relogue o personagem com a captura ativa.')
        elif self.status == 'interrupted':
            detail = 'A verificação terminou porque a captura ou o personagem mudou. O cache foi preservado.'
        else:
            detail = ('Enquanto a captura está ativa, os ganhos são lidos continuamente, sem precisar usar Reload. '
                      '~ indica um valor salvo, ainda não confirmado. '
                      'Se houver dessincronização, use Recarregar dados; se persistir, relogue com a captura ativa.')
        return dict(status=self.status,active=self.status == 'watching',remaining_seconds=seconds,
                    received=[key for key in NAMES if key in self.received],pending=pending,
                    detail=detail,server_requests_sent=0)
