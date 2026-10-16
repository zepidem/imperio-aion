# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Offline, strict wire inspection. No resync guesses or DPS aggregation.

Protocol facts used for research: varint body_length+4; opcode bytes at the
start of a body; 04 38 damage/cast header. No third-party source is copied.
LZ4 blocks are decoded from the public block format specification; opaque
outbound bodies are counted, not semantically decoded.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def varint(buf, pos):
    value = 0
    for n in range(5):
        if pos + n >= len(buf):
            raise ValueError("Incomplete varint")
        b = buf[pos + n]
        value |= (b & 127) << (7 * n)
        if b < 128:
            if n == 4 and b > 15:
                raise ValueError("Varint exceeds uint32")
            return value, pos + n + 1
    raise ValueError("Varint exceeds five bytes")


def reconstruct(rows):
    # Relative positions avoid uint32 sequence wrap; this examiner rejects
    # streams spanning half the sequence space instead of guessing ordering.
    base = int(rows[0]['tcp.seq_raw'])
    pieces = sorted((((int(r['tcp.seq_raw']) - base) % 2**32,
                      bytes.fromhex(r['tcp.payload'].replace(':', '')),
                      float(r['frame.time_epoch']), int(r['frame.number']))
                     for r in rows), key=lambda p: (p[0], p[3]))
    buf = bytearray()
    spans = []
    duplicates = 0
    for start, payload, stamp, frame in pieces:
        if start > 2**31:
            raise ValueError('Sequence range unsupported')
        if start > len(buf):
            raise ValueError(f'TCP gap at offset {len(buf)}: {start-len(buf)} bytes')
        overlap = min(len(payload), len(buf) - start)
        if buf[start:start + overlap] != payload[:overlap]:
            raise ValueError('Conflicting TCP overlap')
        duplicates += overlap
        if overlap < len(payload):
            offset = len(buf)
            buf.extend(payload[overlap:])
            spans.append((offset, len(buf), stamp, frame))
    return bytes(buf), spans, duplicates


def walk(buf):
    result = []
    pos = 0
    while pos < len(buf):
        if buf[pos] == 0:
            pos += 1
            continue
        start = pos
        length, body_start = varint(buf, pos)
        if not 6 <= length <= 65539:
            raise ValueError(f'Invalid frame length {length} at {pos}')
        end = body_start + length - 4
        if end > len(buf):
            raise ValueError(f'Incomplete final frame at {pos}')
        body = buf[body_start:end]
        result.append((start, end, body))
        pos = end
    return result


def lz4_block(data, expected_size):
    if not 0 < expected_size <= 1_000_000:
        raise ValueError('LZ4 output size outside research limit')
    output = bytearray()
    pos = 0
    def read_length(initial):
        nonlocal pos
        length = initial
        if initial == 15:
            while True:
                if pos >= len(data):
                    raise ValueError('Missing LZ4 length extension')
                extra = data[pos]
                pos += 1
                length += extra
                if length > expected_size:
                    raise ValueError('LZ4 length exceeds output bound')
                if extra < 255:
                    break
        return length
    while pos < len(data):
        token = data[pos]
        pos += 1
        literals = read_length(token >> 4)
        if pos + literals > len(data) or len(output) + literals > expected_size:
            raise ValueError('LZ4 literals exceed input or output')
        output.extend(data[pos:pos+literals])
        pos += literals
        if pos == len(data):
            break
        if pos + 2 > len(data):
            raise ValueError('Missing LZ4 match offset')
        distance = int.from_bytes(data[pos:pos+2], 'little')
        pos += 2
        if distance == 0 or distance > len(output):
            raise ValueError('Invalid LZ4 match offset')
        match = read_length(token & 15) + 4
        if len(output) + match > expected_size:
            raise ValueError('LZ4 match exceeds output bound')
        for _ in range(match):
            output.append(output[-distance])
    if len(output) != expected_size:
        raise ValueError('LZ4 decoded size differs from declared size')
    return bytes(output)


def expand_bundles(frames, counters, depth=0):
    expanded = []
    for start, end, body in frames:
        if body[:2] != b'\xff\xff':
            expanded.append((start, end, body))
            continue
        if depth >= 4 or len(body) < 7:
            raise ValueError('Bundle nesting or header invalid')
        decoded = lz4_block(body[6:], int.from_bytes(body[2:6], 'little'))
        counters['bundles'] += 1
        counters['decoded_bytes'] += len(decoded)
        inner = [(start, end, inner_body) for _, _, inner_body in walk(decoded)]
        expanded.extend(expand_bundles(inner, counters, depth+1))
    return expanded


def inspect(tsv):
    with Path(tsv).open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    groups = defaultdict(list)
    for row in rows:
        if len(bytes.fromhex(row['tcp.payload'].replace(':', ''))) != int(row['tcp.len']):
            raise ValueError('Payload length mismatch')
        key = (row['tcp.stream'], row['ip.src'], row['tcp.srcport'],
               row['ip.dst'], row['tcp.dstport'])
        groups[key].append(row)
    results = []
    for key, segments in groups.items():
        item = dict(stream=int(key[0]), source=f'{key[1]}:{key[2]}',
                    destination=f'{key[3]}:{key[4]}', payload_segments=len(segments),
                    tshark_flags={field:sum(bool(r[field]) for r in segments) for field in
                                  ['tcp.analysis.retransmission', 'tcp.analysis.lost_segment',
                                   'tcp.analysis.out_of_order']})
        try:
            buf, spans, duplicates = reconstruct(segments)
            frames = walk(buf)
            opcodes = Counter(body[:2].hex(' ') for _, _, body in frames)
            item.update(reassembled_bytes=len(buf), overlap_bytes=duplicates,
                        tcp_gaps=0, strict_framing_complete=True, frames=len(frames),
                        heartbeat_frames=opcodes['00 36'],
                        compressed_bundles=opcodes['ff ff'])
            # Opcode semantics are assigned only to the heartbeat-rich direction.
            if opcodes['00 36'] >= 100:
                item['direction_inference'] = 'game_to_client_candidate'
                counters = Counter()
                outer_frames = frames
                frames = expand_bundles(frames, counters)
                item['decoded_bundles'] = counters['bundles']
                item['bundle_decoded_bytes'] = counters['decoded_bytes']
                item['logical_frames_including_bundle_contents'] = len(frames)
                opcodes = Counter(body[:2].hex(' ') for _, _, body in frames)
                item['opcode_counts'] = dict(opcodes.most_common())
                headers = []
                rejects = 0
                cast_markers = 0
                span_index = 0
                for start, end, body in frames:
                    if body[:2] != b'\x04\x38':
                        continue
                    try:
                        target, pos = varint(body, 2)
                        switch, pos = varint(body, pos)
                        layout = switch & 15
                        if layout == 0:
                            cast_markers += 1
                            continue
                        if layout not in (4, 5, 6, 7):
                            rejects += 1
                            continue
                        flag, pos = varint(body, pos)
                        actor, pos = varint(body, pos)
                        if pos + 5 > len(body):
                            raise ValueError('Short damage header')
                        skill = int.from_bytes(body[pos:pos+4], 'little')
                        uid = body[pos+4]
                        kind, pos = varint(body, pos+5)
                        if not actor or not target or not 0 < skill < 300000000:
                            raise ValueError('Implausible entity or skill')
                        while spans[span_index][1] <= start:
                            span_index += 1
                        stamp = spans[span_index][2]
                        packet = spans[span_index][3]
                        headers.append(dict(capture_packet=packet,
                                            seconds_from_first_payload=round(stamp-float(rows[0]['frame.time_epoch']), 6),
                                            actor=actor, target=target, raw_skill=skill,
                                            layout=layout, switch=switch, damage_type=kind,
                                            same_entity=actor==target, amount_decoded=False))
                    except ValueError:
                        rejects += 1
                item['damage_opcode_header_candidates'] = len(headers)
                item['same_entity_header_candidates'] = sum(h['same_entity'] for h in headers)
                item['damage_opcode_cast_markers'] = cast_markers
                item['damage_opcode_rejected_headers'] = rejects
                item['candidate_layout_counts'] = dict(Counter(h['layout'] for h in headers))
                item['distinct_candidate_actors'] = len({h['actor'] for h in headers})
                item['distinct_candidate_targets'] = len({h['target'] for h in headers})
                item['candidate_activity_per_10_seconds'] = dict(sorted(Counter(int(h['seconds_from_first_payload']//10)*10 for h in headers).items()))
                item['candidate_header_examples'] = headers[:12]
                item['amount_decoding_validated'] = False
            else:
                item['direction_inference'] = 'opaque_direction_not_decoded'
        except ValueError as exc:
            item['strict_framing_complete'] = False
            item['error'] = str(exc)
        results.append(item)
    return dict(payload_packets=len(rows), directions=results,
                limitations=['No damage amounts decoded or summed.',
                             'No identity or player-name resolution.',
                             'Only top-level bundles decoded; no speculative search for embedded records.',
                             'Capture starts mid-session; no handshake or self record guaranteed.',
                             'Candidate counts are frames/headers, not validated hits.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('tsv')
    parser.add_argument('output')
    args = parser.parse_args()
    report = inspect(args.tsv)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
