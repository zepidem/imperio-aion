# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Provisional offline value extraction, bounded by frame and record tails.

This researches public wire-format facts; it is not a production parser or a
validated meter. It accepts a record only if a single layout interpretation
ends at a recognized record boundary. It never guesses past malformed data.
"""
import argparse
import csv
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from inspect_wire import varint, reconstruct, walk, expand_bundles


def boundary(body, at):
    return at == len(body) or (at+1 < len(body) and 1 <= body[at] <= 7 and body[at+1] == 0)


def read_tail(body, at, layout, switch, amount):
    # The advertised amount is the total. Extra-hit fields are subdivisions,
    # and must never be added to it a second time.
    if layout == 4 and not (body[at:] in (b'\x01\x00', b'\x02\x00') and not switch & 32):
        field, at = varint(body, at)
        if not 1 <= field <= 25:
            raise ValueError('Unrecognized layout-4 tail')
    extra = []
    auxiliary = None
    if switch & 32:
        count, at = varint(body, at)
        if not 1 <= count <= 25:
            raise ValueError('Invalid additional-hit count')
        for _ in range(count):
            value, at = varint(body, at)
            extra.append(value)
        if sum(extra) >= amount:
            raise ValueError('Additional-hit fields inconsistent with total')
    if body[at:at+2] == b'\x03\x00':
        at += 2
        # Observed terminal suffix has no following amount. If a value exists,
        # require it to end at a known boundary; never consume a chained marker.
        if not boundary(body, at):
            _, at = varint(body, at)
    # Observed terminal 01 00 + one bounded varint is auxiliary data, not a
    # complete chained attack (which needs its own actor/skill/value header).
    # Require exact EOF; never add this opaque value to damage.
    if body[at:at+2] == b'\x01\x00' and at+2<len(body):
        try:
            value,finish=varint(body,at+2)
            if finish==len(body) and 0<=value<=100000:
                auxiliary=value;at=finish
        except ValueError:pass
    if not boundary(body, at):
        raise ValueError('Record does not end at an accepted boundary')
    return at, extra, auxiliary


def record(body, begin):
    target, at = varint(body, begin)
    switch, at = varint(body, at)
    layout = switch & 15
    if layout == 0:
        return None
    if layout not in (4, 6):
        raise ValueError(f'Unsupported layout {layout}')
    _, at = varint(body, at)
    actor, at = varint(body, at)
    if at+5 > len(body):
        raise ValueError('Truncated header')
    skill = int.from_bytes(body[at:at+4], 'little')
    uid = body[at+4]
    kind, at = varint(body, at+5)
    if not target or not actor or not 0 < skill < 300000000:
        raise ValueError('Implausible header')
    interpretations = []
    for opaque_size in ({4:(8,), 6:(11,10)}[layout]):
        try:
            v1, p1 = varint(body, at+opaque_size)
            v2, p2 = varint(body, p1)
            if v1 == 0:
                v1, p1 = v2, p2
                v2, p2 = varint(body, p1)
            options = [(v1, v2, p2)]
            if layout == 6 and kind == 3 and 1000 <= v1 <= 5000000 and v2 <= 25:
                options.append((None, v1, p1))
            for scalar, amount, tail_start in options:
                if not 0 < amount < 100000000:
                    continue
                if scalar is not None and scalar != 0 and not 1000 <= scalar <= 200000:
                    continue
                try:
                    end, extra, auxiliary = read_tail(body, tail_start, layout, switch, amount)
                    interpretations.append(dict(actor=actor, target=target, raw_skill=skill,
                                                uid=uid, layout=layout, damage_type=kind,
                                                scalar=scalar, candidate_amount=amount,
                                                additional_hit_fields=extra,
                                                terminal_auxiliary_field=auxiliary,
                                                same_entity=actor==target,
                                                body_end=end, opaque_size=opaque_size))
                except ValueError:
                    pass
        except ValueError:
            pass
    # A zero pad can make both adjacent opaque widths yield exactly the same
    # value fields. Treat that as one value interpretation, while retaining
    # the observed offset alternatives in the research evidence.
    distinct = {}
    for candidate in interpretations:
        width = candidate.pop('opaque_size')
        key = json.dumps(candidate, sort_keys=True)
        if key not in distinct:
            distinct[key] = dict(candidate, opaque_widths_consistent=[])
        distinct[key]['opaque_widths_consistent'].append(width)
    if len(distinct) != 1:
        raise ValueError(f'Expected one strict interpretation; found {len(distinct)}')
    return next(iter(distinct.values()))


def probe(tsv):
    with Path(tsv).open(encoding='utf-8-sig', newline='') as file:
        all_rows = list(csv.DictReader(file, delimiter='\t'))
    first_time = min(float(r['frame.time_epoch']) for r in all_rows)
    groups = defaultdict(list)
    for row in all_rows:
        groups[(row['tcp.stream'], row['tcp.srcport'], row['tcp.dstport'])].append(row)
    events = []
    rejected = []
    identity_observations = []
    for (stream, source, dest), rows in groups.items():
        buf, spans, _ = reconstruct(rows)
        frames = walk(buf)
        if sum(body[:2] == b'\x00\x36' for _, _, body in frames) < 20:
            continue
        frames = expand_bundles(frames, Counter())
        starts = [s[0] for s in spans]
        for offset, _, body in frames:
            span = spans[bisect_right(starts, offset)-1]
            context = dict(capture_packet=span[3],
                           seconds=round(span[2]-first_time, 6), stream=int(stream),
                           source_port=int(source), destination_port=int(dest))
            # Only report occurrence of the supplied character name. It does
            # not by itself establish ownership of an entity ID.
            if b'Zepidem' in body:
                identity_observations.append(dict(**context, opcode=body[:2].hex(' '),
                                                 character_name_present=True))
            if body[:2] != b'\x04\x38':
                continue
            at = 2
            while at < len(body):
                if body[at:at+2] == b'\x01\x00':
                    at += 2
                    if at == len(body):
                        break
                try:
                    parsed = record(body, at)
                    if parsed is None:
                        break
                    events.append(dict(**context, **parsed,
                                       status='provisional_not_screen_validated'))
                    at = parsed['body_end']
                    if at < len(body) and body[at:at+2] != b'\x01\x00':
                        # Other sub-record types are valid boundaries for the
                        # first value, but their contents remain undecoded.
                        break
                except ValueError as exc:
                    rejected.append(dict(**context, body_offset=at, reason=str(exc),
                                         opcode_body_hex=body.hex()))
                    break
    aggregate = defaultdict(list)
    for event in events:
        aggregate[(event['actor'], event['raw_skill'], event['same_entity'])].append(event)
    groups = [dict(actor=actor, raw_skill=skill, same_entity=same, records=len(items),
                   candidate_amounts=[item['candidate_amount'] for item in items],
                   target_ids=sorted({item['target'] for item in items}))
              for (actor,skill,same),items in sorted(aggregate.items())]
    return dict(events=events, rejected=rejected,
                identity_observations=identity_observations,
                records_by_actor_and_raw_skill=groups,
                damage_values_screen_validated=False,
                limitations=['Only strict layouts 4 and 6 are supported.',
                             '01..07 00 are accepted suffix boundaries; only 01 00 damage chains are inspected.',
                             'Rejected/ambiguous records must not be summed.',
                             'No DoT amounts or player skill-name mapping.',
                             'No DPS computed; no user-entity attribution inferred.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('tsv')
    parser.add_argument('output')
    args = parser.parse_args()
    report = probe(args.tsv)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('events','rejected')}, indent=2))
    print('Provisional records:', len(report['events']), 'rejected frames:', len(report['rejected']))
