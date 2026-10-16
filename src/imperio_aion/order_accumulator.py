# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Altgard progress using the weekly reference explicitly chosen by the user.

Counter 400100400 advanced on three Altgard receipts and one isolated
Latesran receipt. The latter advanced both counters in the same packet.
Subtracting the fresh Latesran difference therefore excludes that observed
activity. Other Orders and character/server ownership remain unqualified.
The purchase quota of 12 is a display reference, never a completion cap.

The configured rule is C - 17 - Latesran for the Wednesday 04:00 Brasilia
week. Seventeen is a user-requested reference, not a server-verified reset
value. Its provenance is retained separately from actual wire timestamps.
Old partial anchors never supply that reference. A stale pre-reset counter
above 17 is rejected rather than counted as this week's progress. This
module never writes files.
"""
from datetime import datetime
import math
import time

from daily_duty import BRASILIA
from inspect_wire import varint
from weekly_orders import next_order_reset

ACCUMULATED_COUNTER = 400100400
PURCHASE_QUOTA_REFERENCE = 12
WEEK_SECONDS = 7 * 24 * 60 * 60
MAX_COUNTER = 2**32 - 1
MAX_FUTURE_SECONDS = 5
SOURCE = 'orders-accumulated-difference'
CALCULATION_RULE = 'weekly-reference-17'
WEEKLY_REFERENCE = 17
STATE_FIELDS = {'schema', 'identity', 'week_start', 'reset_at', 'base_accumulated',
                'base_latesran', 'current', 'last_latesran', 'server_stamp_ms',
                'observed_at', 'partial', 'reason', 'base_stamp_ms',
                'base_observed_at', 'base_source'}
INVALID_FIELDS = {'invalidated', 'invalid_reason', 'invalidated_at'}
RULE_FIELDS = {'calculation_rule'}


def _number(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def _now(now):
    now = time.time() if now is None else now
    if not _number(now) or not 1_500_000_000 <= now <= 2_500_000_000:
        raise ValueError('Horário de leitura inválido')
    return now


def _period(now):
    reset = next_order_reset(datetime.fromtimestamp(now, BRASILIA)).timestamp()
    return reset - WEEK_SECONDS, reset


def _identity(identity):
    if not isinstance(identity, dict) or not isinstance(identity.get('name'), str):
        return None
    try:
        size = len(identity['name'].encode('utf-8'))
    except UnicodeError:
        return None
    if not (0 < size <= 36 and not any(ord(c) < 32 for c in identity['name'])
            and type(identity.get('server')) is int and 1000 <= identity['server'] < 3000):
        return None
    return dict(name=identity['name'], server=identity['server'])


def _stamp(stamp, current, flag, now, initial=False):
    if stamp == 0:
        if not (initial and current == 0 and flag == 0):
            raise ValueError('Acumulado sem data de confirmação')
    elif not (1_500_000_000_000 <= stamp <= 2_500_000_000_000
              and stamp / 1000 <= now + MAX_FUTURE_SECONDS):
        raise ValueError('Data do acumulado inválida ou futura')


def decode_initial_accumulated(body, now=None):
    """Decode only the verified counter in the complete bounded 8356 table."""
    now = _now(now)
    if body[:2] != b'\x83\x56':
        raise ValueError('Outra lista inicial')
    count, at = varint(body, 2)
    if not 1 <= count <= 4096 or len(body) != at + count * 17 + 130:
        raise ValueError('Lista inicial de contadores fora do perfil')
    end = at + count * 17
    if body[end] != 23:
        raise ValueError('Extensão inicial fora do perfil')
    previous = 0
    matched = None
    for offset in range(at, end, 17):
        identifier = int.from_bytes(body[offset:offset + 4], 'little')
        current = int.from_bytes(body[offset + 4:offset + 8], 'little')
        stamp = int.from_bytes(body[offset + 8:offset + 16], 'little')
        flag = body[offset + 16]
        # Unknown records may use flags outside this counter's profile. Their
        # flags stay opaque; validate semantics only for the mapped record.
        if (not previous < identifier < 2**31
                or stamp and not 1_500_000_000_000 <= stamp <= 2_500_000_000_000):
            raise ValueError('Registro inicial fora do perfil')
        previous = identifier
        if identifier == ACCUMULATED_COUNTER:
            if flag > 63:
                raise ValueError('Sinalizador do acumulado fora do perfil')
            _stamp(stamp, current, flag, now, initial=True)
            matched = dict(current=current, server_stamp_ms=stamp, observed_at=now,
                           record_flag=flag, source='8356-initial-counter', experimental=True)
    if matched is None:
        raise ValueError('Acumulado de Ordens ausente')
    return matched


def decode_accumulated_update(body, now=None):
    """A 8456 absolute update is publishable only beside a success receipt."""
    now = _now(now)
    if (len(body) != 20 or body[:3] != b'\x84\x56\x01' or body[-1] > 63
            or int.from_bytes(body[3:7], 'little') != ACCUMULATED_COUNTER):
        raise ValueError('Atualização do acumulado fora do perfil')
    current = int.from_bytes(body[7:11], 'little')
    stamp = int.from_bytes(body[11:19], 'little')
    _stamp(stamp, current, body[-1], now)
    return dict(current=current, server_stamp_ms=stamp, observed_at=now,
                record_flag=body[-1], source='8456-counter-and-0f8d-receipt', experimental=True)


def _successful_receipt(body):
    if len(body) < 13 or body[:4] != b'\x0f\x8d\0\0' or body[12] != 0:
        return False
    count = int.from_bytes(body[8:12], 'little')
    instance = int.from_bytes(body[4:8], 'little')
    if count not in (1, 2, 3, 4) or len(body) != 9 + 4 * count or not 0 < instance < 10_000_000:
        return False
    indices = [int.from_bytes(body[i:i + 4], 'little') for i in range(13, len(body), 4)]
    return len(indices) == len(set(indices)) and all(i in (1, 2, 3) for i in indices)


class OrderAccumulator:
    LOGIN_WINDOW_US = 30_000_000
    PREFACE_WINDOW_US = 1_000_000
    SETTLE_US = 1_000_000

    def __init__(self, character):
        self.character = character
        self.flow = None
        self.identity = None
        self.login_clock_us = None
        self.initial_pending = None
        self.value = None
        self.pending = None
        self.batch = None
        self.candidate = None
        self.receipt = False
        self.problem = None
        self.invalidated = False

    def observe(self, ctx, body):
        op = body[:2]
        if op not in (b'\x83\x56', b'\x0b\x61', b'\x33\x36', b'\x84\x56', b'\x0f\x8d'):
            return
        flow, clock = ctx['flow'], ctx['clock_us']
        if op == b'\x83\x56':
            try:
                value = decode_initial_accumulated(body)
            except (ValueError, IndexError):
                self.initial_pending = None
                return
            self.initial_pending = dict(value, flow=flow, clock_us=clock)
            return
        if op == b'\x0b\x61':
            from activity_entries import decode_initial_entries
            try:
                decode_initial_entries(body)
            except (ValueError, IndexError):
                self.__init__(self.character)
                self.flow = flow
                self.problem = 'Login incompleto; acumulado aguardando confirmação'
                return
            pending = self.initial_pending
            self.__init__(self.character)
            self.flow = flow
            self.login_clock_us = clock
            if pending and pending['flow'] == flow and 0 <= clock - pending['clock_us'] <= self.PREFACE_WINDOW_US:
                self.initial_pending = pending
            return
        if op == b'\x33\x36':
            from live_core import self_identity
            try:
                identity = self_identity(body)
            except (ValueError, UnicodeError, IndexError):
                self.__init__(self.character)
                self.flow = flow
                self.problem = 'Identidade do acumulado não confirmada'
                return
            same = (self.flow == flow and self.identity is not None
                    and all(identity[k] == self.identity[k] for k in ('name', 'server', 'actor')))
            if same:
                return
            if self.identity is not None or self.flow != flow:
                self.__init__(self.character)
            self.flow = flow
            self.identity = identity
            if (identity['name'] != self.character or self.login_clock_us is None
                    or not 0 <= clock - self.login_clock_us <= self.LOGIN_WINDOW_US):
                self.value = self.pending = self.initial_pending = None
                self.login_clock_us = None
                self.problem = 'Acumulado aguardando login do personagem'
                return
            if self.initial_pending is not None:
                self.value = dict(self.initial_pending, identity=dict(identity), login_clock_us=self.login_clock_us)
                self.initial_pending = None
            return
        if (self.flow != flow or self.identity is None or self.identity['name'] != self.character
                or self.login_clock_us is None):
            return
        if self.invalidated:
            return  # A later larger counter does not repair a broken anchor.
        if op == b'\x84\x56' and body[3:7] != ACCUMULATED_COUNTER.to_bytes(4, 'little'):
            return
        batch = (flow, ctx['packet'], clock)
        if batch != self.batch:
            self.batch, self.candidate, self.receipt = batch, None, False
        if op == b'\x84\x56':
            try:
                self.candidate = decode_accumulated_update(body)
            except (ValueError, IndexError) as exc:
                self.problem = str(exc)
                self.candidate = None
                self.pending = None
                return
        elif _successful_receipt(body):
            self.receipt = True
        if self.candidate is None or not self.receipt:
            return
        prior = self.pending or self.value
        if prior is not None:
            if (self.candidate['current'] < prior['current']
                    or self.candidate['server_stamp_ms'] < prior['server_stamp_ms']):
                self.problem = 'Acumulado regrediu; aguardando novo login'
                self.invalidated = True
                self.pending = None
                return
            if (self.candidate['current'], self.candidate['server_stamp_ms']) == (prior['current'], prior['server_stamp_ms']):
                return
        self.pending = dict(self.candidate, flow=flow, clock_us=clock,
                            identity=dict(self.identity), login_clock_us=self.login_clock_us)
        self.problem = None

    def snapshot(self, available, clock_us, now=None):
        now = _now(now)
        identified = bool(self.identity and self.identity['name'] == self.character and self.login_clock_us is not None)
        if available and identified and self.pending is not None and clock_us - self.pending['clock_us'] >= self.SETTLE_US:
            self.value, self.pending = self.pending, None
        if (not available or not identified or self.problem or self.value is None
                or self.pending is not None
                or clock_us - self.value['clock_us'] < self.SETTLE_US):
            return dict(available=False, reading_error=self.problem,
                        flow=self.flow, identity=dict(self.identity) if self.identity else None,
                        invalidated=self.invalidated, allow_cached=not self.invalidated and (not self.flow or identified),
                        experimental=True)
        if self.value['server_stamp_ms'] / 1000 > now + MAX_FUTURE_SECONDS:
            return dict(available=False, reading_error='Data do acumulado futura', allow_cached=False, experimental=True)
        return dict(self.value, available=True, reading_error=None)


def _valid_legacy_state(value):
    """Read schema 1 for migration only; it must not become a weekly reference."""
    if (not isinstance(value, dict) or value.get('schema') != 1 or _identity(value.get('identity')) is None
            or value['identity'] != _identity(value['identity'])
            or not STATE_FIELDS <= set(value) or set(value) - STATE_FIELDS - INVALID_FIELDS):
        return False
    integers = ('base_accumulated', 'base_latesran', 'current', 'last_latesran', 'server_stamp_ms', 'base_stamp_ms')
    if any(type(value.get(k)) is not int for k in integers) or type(value.get('partial')) is not bool:
        return False
    if not (0 <= value['base_accumulated'] <= value['current'] <= MAX_COUNTER
            and 0 <= value['base_latesran'] <= value['last_latesran'] <= 5):
        return False
    if value['current'] - value['base_accumulated'] < value['last_latesran'] - value['base_latesran']:
        return False
    if not all(_number(value.get(k)) for k in ('week_start', 'reset_at', 'observed_at', 'base_observed_at')):
        return False
    if not (1_500_000_000 <= value['week_start'] <= value['base_observed_at'] <= value['observed_at']
            < value['reset_at'] <= 2_500_000_000):
        return False
    if value['reset_at'] - value['week_start'] != WEEK_SECONDS:
        return False
    if _period(value['observed_at']) != (value['week_start'], value['reset_at']):
        return False
    stamp = value['server_stamp_ms']
    if stamp == 0:
        if value['current'] != 0:
            return False
    elif not 1_500_000_000_000 <= stamp <= 2_500_000_000_000 or stamp / 1000 > value['observed_at'] + MAX_FUTURE_SECONDS:
        return False
    base_stamp = value['base_stamp_ms']
    if base_stamp == 0:
        if value['base_accumulated'] != 0:
            return False
    elif not (1_500_000_000_000 <= base_stamp <= stamp
              and base_stamp / 1000 <= value['base_observed_at'] + MAX_FUTURE_SECONDS):
        return False
    if value['base_source'] not in ('8356-initial-counter', '8456-counter-and-0f8d-receipt'):
        return False
    if value.get('reason') not in ('post-reset-unmodified-initial', 'midweek-anchor'):
        return False
    if not value['partial'] and (value['base_latesran'] != 0 or value['reason'] != 'post-reset-unmodified-initial'
            or value['base_source'] != '8356-initial-counter' or base_stamp / 1000 >= value['week_start']):
        return False
    if value['partial'] and value['reason'] != 'midweek-anchor':
        return False
    if set(value) & INVALID_FIELDS:
        if (not INVALID_FIELDS <= set(value) or value['invalidated'] is not True
                or not isinstance(value['invalid_reason'], str) or not 0 < len(value['invalid_reason']) <= 200
                or not _number(value['invalidated_at'])
                or not value['observed_at'] <= value['invalidated_at'] < value['reset_at']):
            return False
    return True


def valid_accumulator_state(value):
    """Validate ownership, period, chronology and configured-reference provenance."""
    if isinstance(value, dict) and value.get('schema') == 1:
        return _valid_legacy_state(value)
    required = STATE_FIELDS | RULE_FIELDS
    if (not isinstance(value, dict) or value.get('schema') != 2
            or not required <= set(value) or set(value) - required - INVALID_FIELDS
            or _identity(value.get('identity')) is None
            or value['identity'] != _identity(value['identity'])
            or value.get('calculation_rule') != CALCULATION_RULE):
        return False
    integers = ('base_accumulated', 'base_latesran', 'current', 'last_latesran',
                'server_stamp_ms', 'base_stamp_ms')
    if any(type(value.get(k)) is not int for k in integers):
        return False
    if (value['base_accumulated'] != WEEKLY_REFERENCE or value['base_latesran'] != 0
            or value['partial'] is not False or value['base_stamp_ms'] != 0
            or value['base_source'] != 'configured-weekly-reference'
            or value['reason'] != 'configured-weekly-reference'
            or not WEEKLY_REFERENCE <= value['current'] <= MAX_COUNTER
            or not 0 <= value['last_latesran'] <= 5
            or value['current'] - WEEKLY_REFERENCE < value['last_latesran']):
        return False
    if not all(_number(value.get(k)) for k in ('week_start', 'reset_at', 'observed_at', 'base_observed_at')):
        return False
    if not (1_500_000_000 <= value['week_start'] <= value['base_observed_at']
            <= value['observed_at'] < value['reset_at'] <= 2_500_000_000
            and value['reset_at'] - value['week_start'] == WEEK_SECONDS
            and _period(value['observed_at']) == (value['week_start'], value['reset_at'])):
        return False
    stamp = value['server_stamp_ms']
    if (not 1_500_000_000_000 <= stamp <= 2_500_000_000_000
            or stamp / 1000 > value['observed_at'] + MAX_FUTURE_SECONDS
            or value['current'] > WEEKLY_REFERENCE and stamp / 1000 < value['week_start']):
        return False
    if set(value) & INVALID_FIELDS:
        if (not INVALID_FIELDS <= set(value) or value['invalidated'] is not True
                or not isinstance(value['invalid_reason'], str) or not 0 < len(value['invalid_reason']) <= 200
                or not _number(value['invalidated_at'])
                or not value['observed_at'] <= value['invalidated_at'] < value['reset_at']):
            return False
    return True


def _presentation(value, cached=False):
    current = (value['current'] - value['base_accumulated']) - (value['last_latesran'] - value['base_latesran'])
    return dict(current=current, limit=PURCHASE_QUOTA_REFERENCE, available=True,
                partial=value['partial'], cached=cached, experimental=True,
                quota_reference=True, source=SOURCE, identity=dict(value['identity']),
                week_start=value['week_start'], reset_at=value['reset_at'], observed_at=value['observed_at'],
                reference_base=WEEKLY_REFERENCE, accumulated=value['current'],
                latesran=value['last_latesran'], calculation_rule=CALCULATION_RULE,
                detail='Semanal · Base 17 · Automático')


def historical_accumulator(value, identity, now=None):
    now = _now(now)
    if not valid_accumulator_state(value) or _identity(identity) != value['identity']:
        return dict(available=False, allow_cached=False)
    if value.get('schema') != 2:
        return dict(available=False, allow_cached=False, automatic_pending=True,
                    detail='Aguardando login para aplicar a referência semanal')
    if value.get('invalidated'):
        return dict(available=False, allow_cached=False, reading_error=value['invalid_reason'])
    if _period(now) != (value['week_start'], value['reset_at']):
        return dict(available=False, reset_due=True, detail='Semana reiniciada · aguardando leitura do jogo')
    if value['observed_at'] > now + MAX_FUTURE_SECONDS:
        return dict(available=False, allow_cached=False, reading_error='Data da base futura')
    result = _presentation(value, cached=True)
    result['detail'] = 'Semanal · Última contagem salva'
    return result


def reconcile(snapshot, previous_state, latesran, identity, now=None):
    """Apply the requested reference to fresh, matching weekly counter readings."""
    now = _now(now)
    owner = _identity(identity)
    same_prior = valid_accumulator_state(previous_state) and previous_state['identity'] == owner
    week_start, reset_at = _period(now)
    same_week = same_prior and (previous_state['week_start'], previous_state['reset_at']) == (week_start, reset_at)
    legacy = previous_state if same_week and previous_state['schema'] == 1 else None
    previous = previous_state if same_week and previous_state['schema'] == 2 else None

    def unavailable(reason, cache_allowed=True):
        return dict(available=False, automatic_pending=True, partial=False, experimental=True,
                    allow_cached=bool(cache_allowed and previous), reading_error=reason), None

    def invalidate(reason):
        if previous is None:
            return unavailable(reason, False)
        if previous.get('invalidated') and previous['invalid_reason'] == reason:
            return unavailable(reason, False)
        invalid = dict(previous, invalidated=True, invalid_reason=reason,
                       invalidated_at=previous.get('invalidated_at', now))
        if not valid_accumulator_state(invalid):
            return unavailable(reason, False)
        presentation, _ = unavailable(reason, False)
        return presentation, invalid if invalid != previous_state else None

    if owner is None or not isinstance(snapshot, dict) or _identity(snapshot.get('identity')) != owner:
        return unavailable('Identidade do acumulado não confirmada', False)
    if snapshot.get('invalidated'):
        return invalidate(snapshot.get('reading_error') or 'Acumulado regrediu; aguardando novo login')
    if not snapshot.get('available') or snapshot.get('cached') or snapshot.get('reading_error'):
        return unavailable(snapshot.get('reading_error') or 'Aguardando acumulado do login')
    if not (type(snapshot.get('current')) is int and 0 <= snapshot['current'] <= MAX_COUNTER
            and type(snapshot.get('server_stamp_ms')) is int and _number(snapshot.get('observed_at'))
            and snapshot.get('source') in ('8356-initial-counter', '8456-counter-and-0f8d-receipt')
            and isinstance(snapshot.get('flow'), str) and snapshot['flow']):
        return unavailable('Leitura do acumulado inválida', False)
    stamp = snapshot['server_stamp_ms']
    if (stamp == 0 and snapshot['current'] != 0
            or stamp and not 1_500_000_000_000 <= stamp <= 2_500_000_000_000
            or stamp / 1000 > now + MAX_FUTURE_SECONDS):
        return unavailable('Data do acumulado inválida ou futura', False)
    if not week_start <= snapshot['observed_at'] < reset_at or snapshot['observed_at'] > now + MAX_FUTURE_SECONDS:
        return unavailable('Acumulado não confirmado nesta semana', False)
    if snapshot['current'] < WEEKLY_REFERENCE:
        return invalidate('Acumulado abaixo da referência semanal; aguardando novo login')
    if snapshot['current'] > WEEKLY_REFERENCE and stamp / 1000 < week_start:
        result, _ = unavailable('Semana reiniciada; aguardando acumulado atualizado do jogo', False)
        result['reset_due'] = True
        return result, None
    if legacy is not None and snapshot['source'] != '8356-initial-counter':
        return unavailable('Aguardando login para substituir a base parcial anterior', False)
    if not (isinstance(latesran, dict) and latesran.get('available')
            and not latesran.get('cached') and not latesran.get('reset_pending')
            and not latesran.get('reset_due') and not latesran.get('reading_error')
            and type(latesran.get('current')) is int and 0 <= latesran['current'] <= 5
            and latesran.get('limit') == 5 and latesran.get('reset_at') == reset_at
            and _number(latesran.get('observed_at')) and week_start <= latesran['observed_at'] < reset_at
            and latesran['observed_at'] <= now + MAX_FUTURE_SECONDS
            and latesran.get('flow') == snapshot['flow']):
        return unavailable('Aguardando Latesran da mesma semana e captura')
    if latesran.get('identity') is not None and _identity(latesran['identity']) != owner:
        return unavailable('Identidade Latesran diferente do acumulado', False)
    # Update observed_at is its wire last-change time; an initial reading's
    # observed_at is local receipt time and cannot be compared this way.
    if (latesran.get('source') == '8456-counter-and-0f8d-receipt'
            and latesran['observed_at'] > stamp / 1000 + 0.001):
        return unavailable('Aguardando o acumulado correspondente à entrega Latesran')
    current, late = snapshot['current'], latesran['current']
    if current - WEEKLY_REFERENCE < late:
        return invalidate('Diferença de Ordens inconsistente; aguardando novo login')
    if previous is not None and previous.get('invalidated'):
        if (snapshot['source'] == '8356-initial-counter'
                and snapshot['observed_at'] > previous['invalidated_at']):
            # A fresh owned login can re-establish the configured rule. A live
            # increase alone cannot conceal a previous counter regression.
            previous = None
        else:
            return unavailable(previous['invalid_reason'], False)
    if previous is not None:
        if previous['observed_at'] > now + MAX_FUTURE_SECONDS:
            return unavailable('Base de acompanhamento futura', False)
        if (current < previous['current'] or late < previous['last_latesran']
                or stamp < previous['server_stamp_ms']):
            return invalidate('Contadores regrediram; aguardando novo login')
        if late - previous['last_latesran'] > current - previous['current']:
            return unavailable('Latesran avançou sem confirmação do acumulado')
        value = dict(previous, current=current, last_latesran=late, server_stamp_ms=stamp,
                     observed_at=max(previous['observed_at'], snapshot['observed_at'], latesran['observed_at']))
    else:
        if snapshot['source'] != '8356-initial-counter':
            return unavailable('Aguardando acumulado do login para a referência semanal', False)
        value = dict(schema=2, identity=owner, week_start=week_start, reset_at=reset_at,
                     calculation_rule=CALCULATION_RULE,
                     base_accumulated=WEEKLY_REFERENCE, base_latesran=0, current=current, last_latesran=late,
                     server_stamp_ms=stamp, observed_at=max(snapshot['observed_at'], latesran['observed_at']),
                     base_stamp_ms=0, base_observed_at=max(snapshot['observed_at'], latesran['observed_at']),
                     base_source='configured-weekly-reference',
                     partial=False, reason='configured-weekly-reference')
    if value['current'] - value['base_accumulated'] < value['last_latesran'] - value['base_latesran']:
        return invalidate('Diferença de Ordens inconsistente; aguardando novo login')
    if not valid_accumulator_state(value):
        return unavailable('Base automática inválida', False)
    return _presentation(value), value if value != previous_state else None
