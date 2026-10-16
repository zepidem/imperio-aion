# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Final independent native route: PktMon CLI and real-time ETW event 160.

Only synthetic localhost traffic is generated. This explicitly uses the global
PktMon CLI under a checked, idle/empty precondition. It is not enabled in the HUD.
The test never automatically falls back to capture real game traffic with Npcap.
"""
import argparse
import ctypes
import math
import os
from pathlib import Path
import socket
import time
from datetime import datetime, timezone

from native_probe import CASES, _run_case, _write_report
from native_pktmon_cli import NativeCliCapture


def capability():
    from native_etw import probe
    result = probe()
    try:
        library = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'shell32.dll'
        admin = bool(ctypes.WinDLL(str(library), use_last_error=True).IsUserAnAdmin()) if os.name == 'nt' else False
    except (AttributeError, OSError):
        admin = False
    tool = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'pktmon.exe'
    result['administrator'] = admin
    result['pktmon_cli_present'] = tool.is_file()
    result['ready'] = bool(result.get('available') and result.get('abi', {}).get('valid') and admin and tool.is_file())
    if not admin:
        result['error'] = 'Execute esta prova como administrador; nenhum socket ou captura foi aberto.'
    return result


def _report(mode, cap):
    return dict(product='Império AION', kind='native-cli-etw-loopback-proof', schema_version=3,
        probe_revision=4, monitor_version='0.8.42', mode=mode, status='UNAVAILABLE', capability=cap,
        started_utc=datetime.now(timezone.utc).isoformat(),
        cases=[], capture_started=False, network_opened=False, experimental=True,
        native_loopback_validated=False, game_traffic_validated=False, global_cli=True,
        exclusive_ownership_guaranteed=False, packet_stream_limit_9000_applies=False,
        scope='Somente TCP localhost dos sockets criados por esta prova.',
        note='A CLI global só inicia após confirmar PktMon parado e sem filtros. Outras ferramentas administrativas podem concorrer com essa verificação.',
        planned_fallback='Npcap direto, sem Wireshark, se esta rota não for qualificada.')


def run(output=None, *, check=False, timeout=15, capability_reader=None, backend_factory=None,
        socket_factory=None, clock=None, on_case=None):
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not .5 <= timeout <= 15:
        raise ValueError('Tempo inválido; use entre 0,5 e 15 segundos por caso.')
    report = _report('check' if check else 'loopback', (capability_reader or capability)())
    if check or not report['capability'].get('ready'):
        report['status'] = 'PASS' if check and report['capability'].get('ready') else 'UNAVAILABLE'
        report['detail'] = 'Somente capacidade ETW/CLI conferida; nenhum socket ou captura aberto.' if check else report['capability'].get('error') or 'ETW/CLI indisponível.'
        return _write_report(report, output)
    blocked = False
    for family, name, size in CASES:
        if blocked:
            result = dict(case=name, family='IPv4' if family == socket.AF_INET else 'IPv6',
                status='UNAVAILABLE', expected_bytes=2*size, captured_bytes=0, capture_started=False,
                network_opened=False, error='Não executado após falha de limpeza/ativação global anterior.')
        else:
            result = _run_case(family, name, size, backend_factory=backend_factory or NativeCliCapture,
                socket_factory=socket_factory or socket.socket, clock=clock or time.monotonic, timeout=timeout)
            # Do not repeat unsupported state or attempt further global mutations.
            blocked = bool(result.get('close_incomplete') or result.get('cleanup_errors') or not result['capture_started'])
        report['cases'].append(result)
        report['network_opened'] |= result['network_opened']
        report['capture_started'] |= result['capture_started']
        if on_case:
            on_case(result)
    states = {result['status'] for result in report['cases']}
    report['status'] = 'FAIL' if 'FAIL' in states else 'UNAVAILABLE' if 'UNAVAILABLE' in states else 'PASS'
    report['native_loopback_validated'] = report['status'] == 'PASS'
    report['detail'] = ('Os quatro casos capturaram todos os bytes com hashes corretos, latência até 1 segundo e limpeza completa. Ainda falta validar o jogo.') if report['status'] == 'PASS' else 'A rota CLI/ETW não foi qualificada. O próximo caminho autorizado é Npcap direto, sem Wireshark.'
    if any(result.get('close_incomplete') for result in report['cases']):
        report['detail'] += ' Houve estado global alterado/desconhecido: por segurança, a prova deixou esse estado intacto. Consulte cleanup_errors antes de outro teste.'
    return _write_report(report, output)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prova PktMon CLI + ETW: TCP localhost próprio, sem driver externo.')
    parser.add_argument('--check', action='store_true', help='Somente metadados, sem captura/CLI ou sockets.')
    parser.add_argument('--output', help='Arquivo JSON ou pasta do relatório.')
    parser.add_argument('--timeout', type=float, default=15)
    args = parser.parse_args(argv)
    report = run(args.output, check=args.check, timeout=args.timeout,
        on_case=lambda row: print(f"{row['case']}: {row['status']} · {row['captured_bytes']}/{row['expected_bytes']} bytes", flush=True))
    print(report['status'] + ' · ' + report['detail'], flush=True)
    for row in report['cases']:
        if row.get('error'):
            print(row['case'] + ': ' + row['error'], flush=True)
    print('Relatório: ' + report['report_path'], flush=True)
    return {'PASS': 0, 'FAIL': 2, 'UNAVAILABLE': 3}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
