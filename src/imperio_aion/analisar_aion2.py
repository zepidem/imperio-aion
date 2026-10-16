# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Independent, offline AION 2 research reader. Never computes validated DPS."""
import argparse
import csv
import hashlib
import json
import subprocess
import sys
import uuid
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from inspect_wire import varint, reconstruct, walk, expand_bundles
from probe_amounts import record

ROOT = Path(__file__).resolve().parent
FIELDS = ['frame.number', 'frame.time_epoch', 'tcp.stream', 'ip.src', 'ip.dst',
          'ipv6.src', 'ipv6.dst', 'tcp.srcport', 'tcp.dstport', 'tcp.seq_raw',
          'tcp.len', 'tcp.payload', 'tcp.analysis.retransmission',
          'tcp.analysis.lost_segment', 'tcp.analysis.out_of_order']


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def capture_inputs(session):
    session = Path(session).resolve(strict=True)
    manifest = json.loads((session / 'manifest.json').read_text(encoding='utf-8-sig'))
    version = manifest.get('schema_version')
    if version == 1:
        entries = [dict(path='combate.pcapng', sha256=manifest.get('sha256'))]
    elif version == 2:
        entries = manifest.get('capture_files', [])
    else:
        raise ValueError('Versao de manifesto nao suportada.')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise ValueError('A sessao deve conter de 1 a 8 arquivos de captura.')
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('Entrada de captura invalida no manifesto.')
        # Resolve the current basename so the kit works after moving its folder.
        # Absolute old paths are metadata, never instructions to read elsewhere.
        filename = Path(entry['path']).name
        if Path(filename).suffix.lower() != '.pcapng':
            raise ValueError('Extensao de captura inesperada.')
        path = (session / filename).resolve(strict=True)
        if not path.is_relative_to(session) or path in seen:
            raise ValueError('Arquivo fora da sessao ou duplicado.')
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Captura excede o limite experimental de 64 MiB.')
        expected = entry.get('sha256')
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError('Manifesto sem hash SHA256 utilizavel.')
        actual = digest(path)
        if actual.lower() != expected.lower():
            raise ValueError('SHA256 divergente: ' + filename)
        result.append((path, actual))
        seen.add(path)
    return manifest, result


def export_packets(tshark, pcap, output):
    command = [str(tshark), '-n', '-r', str(pcap), '-Y', 'tcp.len > 0',
               '-T', 'fields', '-E', 'header=y', '-E', 'occurrence=f']
    for field in FIELDS:
        command.extend(['-e', field])
    with output.open('wb') as file:
        process = subprocess.run(command, stdout=file, stderr=subprocess.PIPE,
                                 timeout=60, check=False)
    if process.returncode:
        raise ValueError('TShark falhou: ' + process.stderr.decode('utf-8', errors='replace')[:1000])


def identity(body):
    if body[:2] != b'\x04\x8d':
        raise ValueError('Opcode de identidade inesperado')
    victim, at = varint(body, 2)
    if at + 4 > len(body):
        raise ValueError('Codigo de credito ausente')
    code = int.from_bytes(body[at:at+4], 'little')
    actor, at = varint(body, at+4)
    if at + 3 > len(body):
        raise ValueError('Cabecalho de nome ausente')
    server = int.from_bytes(body[at:at+2], 'little')
    length = body[at+2]
    at += 3
    if not 0 < actor < 10000000 or not 0 < length <= 36 or at+length > len(body):
        raise ValueError('Identidade truncada ou invalida')
    name = body[at:at+length].decode('utf-8', errors='strict')
    if any(ord(ch) < 32 for ch in name):
        raise ValueError('Nome invalido')
    return dict(actor=actor, victim=victim, credit_raw_code=code, server=server, name=name,
                trailing_bytes=len(body)-(at+length))


def resolve_identity(evidence, character, server=None):
    matched = [e for e in evidence if e['name'] == character and
               (server is None or e['server'] == server)]
    candidates = {(e['actor'], e['server']) for e in matched}
    if len(candidates) != 1:
        return None
    actor, found_server = next(iter(candidates))
    # Entity reuse or a same-name collision inside this flow is ambiguous.
    if any(e['actor'] == actor and (e['name'], e['server']) != (character, found_server)
           for e in evidence):
        return None
    return dict(actor=actor, server=found_server, character=character,
                evidence_count=len(matched), basis='04 8d kill-credit name field',
                scope='this capture file and TCP direction only')


def skill_label(raw, profile):
    names = profile['skill_names']
    key = raw
    if str(key) not in names:
        if 10000000 <= raw < 20000000:
            key = raw // 10000 * 10000
        elif 3000000 <= raw < 3100000:
            key = raw * 10 + 1
    return names.get(str(key), 'Codigo ' + str(raw))


def relation(event, binding):
    if event['actor'] == event['target']:
        return 'self_effect_excluded'
    if binding is None:
        return 'unattributed'
    if event['actor'] == binding['actor']:
        return 'caused'
    if event['target'] == binding['actor']:
        return 'received'
    return 'other'


def periodic(body, profile):
    target, at = varint(body, 2)
    if at >= len(body):
        raise ValueError('Efeito periodico truncado')
    effect = body[at]
    if effect == 0:
        return dict(kind='status_only', effect=effect, target=target)
    actor, at = varint(body, at+1)
    _, at = varint(body, at)
    if at+4 > len(body):
        raise ValueError('Habilidade periodica ausente')
    raw = int.from_bytes(body[at:at+4], 'little') // 100
    amount, end = varint(body, at+4)
    if not actor or not target or not 0 < amount < 100000000:
        raise ValueError('Valor periodico invalido')
    if effect in (1, 9, 11):
        # Preserve recovery observations, without summing overlapping effect types.
        return dict(kind='recovery_excluded', actor=actor, target=target,
                    effect=effect, raw_skill=raw, candidate_amount=amount)
    if effect not in (2, 10) or raw not in profile['known_damage_dot_ids'] or len(body)-end != 4:
        raise ValueError('Formato periodico de dano fora do perfil observado')
    return dict(kind='periodic_damage', actor=actor, target=target, effect=effect,
                raw_skill=raw, candidate_amount=amount,
                origin_raw_skill=int.from_bytes(body[end:], 'little'))


def summarize(events):
    caused = [e for e in events if e['relation'] == 'caused']
    per_skill = defaultdict(list)
    for item in caused:
        per_skill[(item['raw_skill'], item['skill_name'])].append(item)
    return dict(caused_records=len(caused),
                partial_experimental_caused_total=sum(e['candidate_amount'] for e in caused),
                by_relation=dict(Counter(e['relation'] for e in events)),
                caused_skills=[dict(raw_skill=raw, name=name, records=len(items),
                                    partial_experimental_total=sum(e['candidate_amount'] for e in items))
                               for (raw, name), items in sorted(per_skill.items())],
                damage_values_screen_validated=False, dps=None,
                records_are_skill_activations=False)


def analyze_tsv(tsv, character, profile, server=None):
    with Path(tsv).open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file, delimiter='\t'))
    if not rows:
        return dict(directions=[], events=[], rejected=[], effects=[], summary=summarize([]))
    first = min(float(r['frame.time_epoch']) for r in rows)
    grouped = defaultdict(list)
    for row in rows:
        source = row.get('ip.src') or row.get('ipv6.src') or ''
        destination = row.get('ip.dst') or row.get('ipv6.dst') or ''
        grouped[(row['tcp.stream'], source, row['tcp.srcport'], destination, row['tcp.dstport'])].append(row)
    directions, events, rejected, effects = [], [], [], []
    for key, packets in grouped.items():
        flow = '|'.join(key)
        info = dict(flow=flow, payload_packets=len(packets))
        directions.append(info)
        try:
            if any(len(bytes.fromhex(r['tcp.payload'].replace(':', ''))) != int(r['tcp.len']) for r in packets):
                raise ValueError('Tamanho de payload inconsistente')
            data, spans, duplicate = reconstruct(packets)
            outer = walk(data)
            info.update(strict_transport_complete=True, duplicate_tcp_bytes_removed=duplicate,
                        payload_bytes=len(data), heartbeat_count=sum(b[:2] == b'\x00\x36' for _, _, b in outer))
            # A conservative, explicitly recorded heuristic, not a server-port assumption.
            if info['heartbeat_count'] < 20:
                info['classification'] = 'opaque_direction_excluded'
                continue
            stats = Counter()
            frames = expand_bundles(outer, stats)
            info.update(classification='game_to_client_candidate', bundles=stats['bundles'],
                        logical_frames=len(frames))
            starts = [s[0] for s in spans]
            contextual = []
            evidence = []
            for index, (offset, _, body) in enumerate(frames):
                span = spans[bisect_right(starts, offset)-1]
                context = dict(flow=flow, message_index=index, capture_packet=span[3],
                               seconds_from_first_payload=round(span[2]-first, 6))
                contextual.append((context, body))
                if body[:2] == b'\x04\x8d':
                    try:
                        evidence.append(dict(**context, **identity(body)))
                    except ValueError as exc:
                        rejected.append(dict(**context, opcode='04 8d', reason=str(exc), body_hex=body.hex()))
            binding = resolve_identity(evidence, character, server)
            info.update(identity=binding, identity_evidence=evidence,
                        opcodes=dict(Counter(b[:2].hex(' ') for _, _, b in frames)))
            for context, body in contextual:
                if body[:2] == b'\x05\x38':
                    try:
                        item = periodic(body, profile)
                        if item['kind'] != 'periodic_damage':
                            effects.append(dict(**context, **item))
                            continue
                        item.update(relation=relation(item, binding), skill_name=skill_label(item['raw_skill'], profile))
                        events.append(dict(**context, **item, status='experimental_not_screen_validated'))
                    except ValueError as exc:
                        rejected.append(dict(**context, opcode='05 38', reason=str(exc), body_hex=body.hex()))
                elif body[:2] == b'\x04\x38':
                    at = 2
                    while at < len(body):
                        if body[at:at+2] == b'\x01\x00':
                            at += 2
                            if at == len(body):
                                break
                        try:
                            item = record(body, at)
                            if item is None:
                                break
                            item.update(kind='direct_damage_candidate', relation=relation(item, binding),
                                        skill_name=skill_label(item['raw_skill'], profile))
                            events.append(dict(**context, body_offset=at, **item,
                                               status='experimental_not_screen_validated'))
                            at = item['body_end']
                            if at < len(body) and body[at:at+2] != b'\x01\x00':
                                break
                        except ValueError as exc:
                            rejected.append(dict(**context, opcode='04 38', body_offset=at,
                                                 reason=str(exc), body_hex=body.hex()))
                            break
        except ValueError as exc:
            info.update(strict_transport_complete=False, error=str(exc), classification='flow_rejected')
    return dict(directions=directions, events=events, rejected=rejected, effects=effects,
                summary=summarize(events))


def write_outputs(destination, report):
    (destination / 'analise.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    columns = ['capture_file', 'flow', 'capture_packet', 'seconds_from_first_payload', 'message_index',
               'kind', 'relation', 'actor', 'target', 'raw_skill', 'skill_name', 'candidate_amount', 'status']
    with (destination / 'eventos.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction='ignore', delimiter=';')
        writer.writeheader()
        for sample in report['files']:
            for event in sample['analysis']['events']:
                writer.writerow(dict(**event, capture_file=sample['capture_file']))
    lines = ['IMPERIO AION - LEITURA OFFLINE EXPERIMENTAL',
             'Personagem informado: ' + report['character'],
             'Danos ainda nao comparados com a tela. Nenhum DPS calculado.',
             'Registros de dano nao equivalem a numero de ativacoes de habilidade.',
             'Totais parciais: excluem registros rejeitados, efeitos proprios e fluxos sem identidade.',
             'Arquivos sao analisados separadamente; nao somamos capturas potencialmente sobrepostas.', '']
    for sample in report['files']:
        analysis = sample['analysis']
        summary = analysis['summary']
        lines.extend(['Arquivo: ' + sample['capture_file'], 'SHA256 conferido: ' + sample['sha256']])
        if not any(direction.get('identity') for direction in analysis['directions']):
            lines.append('Identidade nao localizada ou ambigua. Zero atribuido nao significa ausencia de dano.')
        for direction in analysis['directions']:
            binding = direction.get('identity')
            if binding:
                lines.append(f"Identidade nesta conexao: {binding['character']} / ID {binding['actor']} / servidor {binding['server']}")
            if direction.get('error'):
                lines.append('Fluxo excluido: ' + direction['error'])
        lines.extend([f"Registros atribuidos de dano causado: {summary['caused_records']}",
                      f"Soma experimental parcial: {summary['partial_experimental_caused_total']}"])
        for skill in summary['caused_skills']:
            lines.append(f"  {skill['name']}: {skill['records']} registros / soma parcial {skill['partial_experimental_total']}")
        lines.extend([f"Registros quantificados (todas as origens): {len(analysis['events'])}",
                      f"Registros/mensagens rejeitados: {len(analysis['rejected'])}",
                      'Distribuicao: ' + json.dumps(summary['by_relation'], ensure_ascii=False), ''])
    lines.extend(['Detalhes e evidencias: analise.json. Eventos: eventos.csv.',
                  'Perfil pequeno de nomes/DoT: perfil-sa-experimental.json; fontes incluidas no JSON.'])
    (destination / 'resultado.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')


def main():
    if sys.version_info < (3, 11):
        raise ValueError('Use Python 3.11 ou superior.')
    parser = argparse.ArgumentParser(description='Analise offline experimental de capturas AION 2.')
    parser.add_argument('--sessao', required=True, type=Path)
    parser.add_argument('--personagem', required=True)
    parser.add_argument('--servidor', type=int)
    parser.add_argument('--tshark', default=r'C:\Program Files\Wireshark\tshark.exe', type=Path)
    parser.add_argument('--saida', default=ROOT / 'resultados', type=Path)
    args = parser.parse_args()
    manifest, captures = capture_inputs(args.sessao)
    if not args.tshark.is_file():
        raise ValueError('TShark nao encontrado. Informe --tshark.')
    profile = json.loads((ROOT / 'perfil-sa-experimental.json').read_text(encoding='utf-8-sig'))
    destination = args.saida.resolve() / (args.sessao.name + '-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    destination.mkdir(parents=True, exist_ok=False)
    report = dict(reader_version='0.1-experimental', character=args.personagem,
                  region=manifest.get('region'), screen_damage_validated=False, dps=None,
                  profile=profile, files=[], limitations=[
                      'Identity is resolved per capture file and TCP direction, never hardcoded.',
                      'Only strict observed direct layouts and whitelisted periodic damage are summed.',
                      'No resynchronization after gaps or incomplete frames; affected flows are excluded.',
                      'Capture handover can miss initial packets. Real reconnection remains unvalidated.',
                      'No sum across separate PCAP files; overlapping captures may duplicate events.',
                      'Name evidence may occur after earlier events; conflicting identities exclude attribution.',
                      'Partial totals are protocol hypotheses, not screen-validated damage or DPS.'])
    for index, (path, sha) in enumerate(captures, 1):
        tsv = destination / f'pacotes-{index:03d}.tsv'
        export_packets(args.tshark, path, tsv)
        report['files'].append(dict(capture_file=path.name, sha256=sha,
                                    analysis=analyze_tsv(tsv, args.personagem, profile, args.servidor)))
    write_outputs(destination, report)
    print('Leitura experimental salva em: ' + str(destination / 'resultado.txt'))
    print('Sem DPS validado. Consulte os registros rejeitados em analise.json.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print('Analise interrompida: ' + str(exc), file=sys.stderr)
        sys.exit(1)
