# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Isolated native capture proof using only this process's localhost TCP bytes.

--check reads API capability without opening sockets or initializing capture.
--loopback (default) creates exact IPv4/IPv6 endpoint pairs before activation.
It never discovers AION/ExitLag, starts a global pktmon command, installs drivers,
changes network settings, or requests elevation. Its report contains counts and
SHA-256 values, never packet payloads. Passing is evidence for these synthetic
cases only; it does not validate game traffic or every possible coalesced packet.
If a standard case receives no packet observations, a separate, bounded control
uses exact localhost ports with identical high/low bytes. Controls never qualify
the standard cases, and no new session starts after incomplete cleanup.
"""
import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import random
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone

from native_packets import PacketParseError, parse_packet
from native_pktmon import NativeCapture, NativeCaptureError, probe as _native_capability

CASES = ((socket.AF_INET, 'ipv4-small', 4096),
         (socket.AF_INET, 'ipv4-large', 32768),
         (socket.AF_INET6, 'ipv6-small', 4096),
         (socket.AF_INET6, 'ipv6-large', 32768))
FILETIME_EPOCH = 116444736000000000
LOSS_FIELDS = ('missed_write', 'missed_read', 'queue_dropped', 'event_warnings')
IPV6_UNAVAILABLE = {getattr(socket, 'EAI_FAMILY', -6), 10047, 10049, 10043, 10042, 10051,
                    97, 99, 93, 92, 101}
ADDRESS_IN_USE = {10048, 98}
SYMMETRIC_PORT_ATTEMPTS = 8


def _payload(case, direction, size):
    marker = ('IMPERIO-NATIVE-PROBE:v1:' + case + ':' + direction + ':').encode('ascii')
    result = bytearray(marker)
    block = 0
    while len(result) < size:
        result.extend(hashlib.sha256(marker + block.to_bytes(4, 'big')).digest())
        block += 1
    return bytes(result[:size])


class PayloadLedger:
    """Coverage of known probe bytes with real TCP sequence wrap/reordering.

    The baseline can move backwards when an earlier segment arrives later.
    No bytes are invented for holes; conflicting overlaps always fail the proof.
    This is bounded by the expected probe size and is not the game reassembler.
    """
    def __init__(self, expected):
        if not isinstance(expected, bytes) or not 1 <= len(expected) <= 65536:
            raise ValueError('Payload de prova fora do limite.')
        self.expected = expected
        self.base = None
        self.bytes = {}
        self.observed = self.duplicates = self.duplicate_packets = self.conflicts = 0
        self.unexpected = 0
        self._match_dirty = True
        self._matches = False

    def feed(self, seq, payload):
        if type(seq) is not int or not 0 <= seq < 2**32 or not isinstance(payload, bytes):
            raise PacketParseError('Segmento de prova inválido.')
        if not payload:
            return
        if self.base is None:
            self.base = seq
        offset = (seq - self.base + 2**31) % 2**32 - 2**31
        if abs(offset) > len(self.expected) or len(payload) > len(self.expected):
            raise PacketParseError('Sequência/tamanho fora da janela da prova.')
        if offset < 0:
            self.bytes = {position - offset: value for position, value in self.bytes.items()}
            self.base = seq
            offset = 0
            self._match_dirty = True
        self.observed += len(payload)
        new = 0
        for index, value in enumerate(payload, offset):
            if index >= len(self.expected):
                self.unexpected += 1
                continue
            previous = self.bytes.get(index)
            if previous is None:
                self.bytes[index] = value
                new += 1
                self._match_dirty = True
            elif previous == value:
                self.duplicates += 1
            else:
                self.conflicts += 1
                self._match_dirty = True
        if not new:
            self.duplicate_packets += 1

    def matches(self):
        """Cheap coverage check; compare complete bytes only when they change.

        Repeated component observations do not rescan/hash both full streams on
        every event. Detailed gaps and hashes are computed once for the report.
        """
        if self.conflicts or self.unexpected or len(self.bytes) != len(self.expected):
            return False
        if self._match_dirty:
            self._matches = all(self.bytes.get(index) == value for index, value in enumerate(self.expected))
            self._match_dirty = False
        return self._matches

    def summary(self):
        holes = []
        start = None
        gap_count = 0
        for position in range(len(self.expected) + 1):
            missing = position < len(self.expected) and position not in self.bytes
            if missing and start is None:
                start = position
            elif not missing and start is not None:
                gap_count += 1
                if len(holes) < 16:
                    holes.append(dict(start=start, end=position, bytes=position-start))
                start = None
        complete = len(self.bytes) == len(self.expected) and not gap_count
        captured = bytes(self.bytes[index] for index in range(len(self.expected))) if complete else None
        match = complete and self.matches()
        return dict(expected_bytes=len(self.expected), captured_bytes=len(self.bytes),
            observed_payload_bytes=self.observed, duplicate_bytes=self.duplicates,
            duplicate_packets=self.duplicate_packets, conflicting_bytes=self.conflicts,
            unexpected_bytes=self.unexpected, gap_count=gap_count, gaps=holes,
            tcp_base_sequence=self.base, complete=complete, match=match,
            expected_sha256=hashlib.sha256(self.expected).hexdigest(),
            captured_sha256=hashlib.sha256(captured).hexdigest() if captured is not None else None)


def _base_report(mode, capability):
    return dict(product='Império AION', kind='native-loopback-proof', schema_version=2,
        probe_revision=3,
        monitor_version='0.8.42', mode=mode, started_utc=datetime.now(timezone.utc).isoformat(),
        status='UNAVAILABLE', capability=capability, cases=[], diagnostic_controls=[], capture_started=False,
        network_opened=False, native_loopback_validated=False, game_traffic_validated=False,
        coalescing_above_9000_validated=False, experimental=True,
        scope='Somente capacidade da API e tráfego TCP localhost criado por esta prova.',
        note='Nenhuma captura global, elevação, instalação ou configuração de rede é executada.')


def _write_report(report, output=None):
    filename = 'prova-nativa-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '.json'
    if output is None:
        root = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())
        path = root / 'ImperioAION' / 'diagnosticos-nativos' / filename
    else:
        path = Path(output)
        if path.suffix.lower() != '.json':
            if path.suffix and not path.is_dir():
                raise ValueError('--output deve ser um arquivo JSON ou uma pasta.')
            path = path / filename
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    report['report_path'] = str(path)
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    descriptor, temporary_name = tempfile.mkstemp(suffix='.tmp', prefix='prova-nativa-', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def probe(output=None, *, capability_reader=None):
    """Read-only capability proof; no socket or capture handle is created."""
    capability = (capability_reader or _native_capability)()
    report = _base_report('check', capability)
    report['status'] = 'PASS' if capability.get('ready') else 'UNAVAILABLE'
    report['detail'] = 'API disponível com administrador; localhost ainda não testado.' if capability.get('ready') else capability.get('error') or 'API indisponível ou sem administrador.'
    return _write_report(report, output)


def _endpoint(value):
    address, port = value[:2]
    ip = ipaddress.ip_address(address)
    if not ip.is_loopback or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('A prova aceita somente os próprios endpoints localhost.')
    return str(ip), port


def _bind_owned_socket(sock, address, *, symmetric, excluded, deadline, clock, attempts):
    """Bind only localhost; diagnostic ports have identical high/low bytes.

    Failed EADDRINUSE bindings leave this socket unbound and can be retried.
    At most eight distinct candidates are tried. No address reuse is enabled.
    """
    candidates = [0]
    if symmetric:
        candidates = [byte * 257 for byte in range(0xC0, 0xFF)
                      if byte * 257 not in excluded]
        random.SystemRandom().shuffle(candidates)
        candidates = candidates[:SYMMETRIC_PORT_ATTEMPTS]
    last_collision = None
    for port in candidates:
        if clock() >= deadline:
            raise TimeoutError('Tempo esgotado ao reservar as portas próprias da prova.')
        attempt = dict(requested_port=port, bound=False)
        attempts.append(attempt)
        try:
            sock.bind((address, port))
        except OSError as exc:
            code = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
            attempt['error_code'] = code
            if symmetric and code in ADDRESS_IN_USE:
                last_collision = exc
                continue
            raise
        endpoint = _endpoint(sock.getsockname())
        if endpoint[0] != address or symmetric and endpoint[1] != port:
            raise ValueError('O socket não reservou o endpoint localhost solicitado.')
        attempt['bound'] = True
        attempt['actual_port'] = endpoint[1]
        return endpoint
    raise OSError(getattr(last_collision, 'errno', 10048),
                  'Nenhuma porta própria com bytes iguais ficou livre nas oito tentativas.')


def _receive(sock, expected, deadline, clock):
    data = bytearray()
    while len(data) < len(expected):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('Tempo esgotado no tráfego da prova.')
        sock.settimeout(min(.5, remaining))
        try:
            chunk = sock.recv(min(65536, len(expected) - len(data)))
        except socket.timeout:
            continue
        if not chunk:
            raise OSError('Socket próprio encerrou antes do payload completo.')
        data.extend(chunk)
    if bytes(data) != expected:
        raise ValueError('O receptor localhost não recebeu o payload esperado.')
    return len(data)


def _run_case(family, case, size, *, backend_factory, socket_factory, clock, timeout,
              symmetric_ports=False):
    address = '127.0.0.1' if family == socket.AF_INET else '::1'
    request = _payload(case, 'client-to-server', size)
    response = _payload(case, 'server-to-client', size)
    ledgers = {'client-to-server': PayloadLedger(request), 'server-to-client': PayloadLedger(response)}
    result = dict(case=case, family='IPv4' if family == socket.AF_INET else 'IPv6',
        application_write_bytes=size, status='FAIL', expected_bytes=2 * size,
        captured_bytes=0, capture_started=False, network_opened=False, error=None,
        endpoints=None, symmetric_ports=symmetric_ports, bind_attempts=[],
        observed_packets=0, ignored_non_tcp=0, max_packet_bytes_observed=0,
        ignored_unsupported_types=0,
        unique_pkt_groups=0, repeated_pkt_group_observations=0,
        packet_size_gt_9000_observed=False, latency_ms=None, first_payload_ms=None,
        latency_valid_samples=0, latency_missing_samples=0, latency_invalid_samples=0,
        latency_late_samples=0, latency_sample_errors=[], realtime_within_one_second=False,
        traffic_received_bytes={'client-to-server': 0, 'server-to-client': 0},
        loss={}, streams={})
    start = clock()
    deadline = start + timeout - min(.4, timeout / 4)
    sockets = []
    workers = []
    backend = None
    traffic_errors = []
    traffic_lock = threading.Lock()
    client_done = threading.Event()
    peer_done = threading.Event()
    stop = threading.Event()
    receive_latency = []
    drain_until = None
    pair = None
    groups = set()
    try:
        listener = socket_factory(family, socket.SOCK_STREAM)
        sockets.append(listener); result['network_opened'] = True
        listener.settimeout(min(1.0, max(.01, deadline - clock())))
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        server_endpoint = _bind_owned_socket(listener, address, symmetric=symmetric_ports,
            excluded=(), deadline=deadline, clock=clock, attempts=result['bind_attempts'])
        listener.listen(1)
        client = socket_factory(family, socket.SOCK_STREAM)
        sockets.append(client)
        client.settimeout(min(1.0, max(.01, deadline - clock())))
        client_endpoint = _bind_owned_socket(client, address, symmetric=symmetric_ports,
            excluded=(server_endpoint[1],), deadline=deadline, clock=clock, attempts=result['bind_attempts'])
        client.connect(server_endpoint)
        peer, peer_address = listener.accept()
        sockets.append(peer)
        client_endpoint = _endpoint(client.getsockname())
        if _endpoint(peer_address) != client_endpoint or _endpoint(peer.getsockname()) != server_endpoint:
            raise ValueError('A conexão aceita não corresponde aos sockets desta prova.')
        if _endpoint(client.getpeername()) != server_endpoint:
            raise ValueError('Destino do cliente fora da conexão própria.')
        pair = tuple(sorted((client_endpoint, server_endpoint)))
        result['endpoints'] = [list(endpoint) for endpoint in pair]
        listener.close()  # The established connection remains owned by peer/client.
        for sock in (client, peer):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(min(.5, max(.01, deadline - clock())))
        backend = backend_factory([pair])
        backend.start()
        result['capture_started'] = True
        if clock() >= deadline:
            raise TimeoutError('A ativação da API excedeu o tempo da prova.')

        def peer_traffic():
            try:
                count = _receive(peer, request, deadline, clock)
                with traffic_lock:result['traffic_received_bytes']['client-to-server'] = count
                if not stop.is_set():peer.sendall(response)
            except Exception as exc:
                with traffic_lock:traffic_errors.append('Servidor próprio: ' + str(exc))
            finally:peer_done.set()

        def client_traffic():
            try:
                client.sendall(request)
                count = _receive(client, response, deadline, clock)
                with traffic_lock:result['traffic_received_bytes']['server-to-client'] = count
            except Exception as exc:
                with traffic_lock:traffic_errors.append('Cliente próprio: ' + str(exc))
            finally:client_done.set()

        workers = [threading.Thread(target=peer_traffic, daemon=True), threading.Thread(target=client_traffic, daemon=True)]
        for worker in workers:worker.start()
        while clock() < deadline:
            record = backend.read(timeout=min(.1, max(0, deadline - clock())))
            now = clock()
            if record is not None:
                if record.get('drop_reason'):
                    raise NativeCaptureError('Pktmon reportou descarte durante o tráfego próprio.')
                raw = record['data']
                result['observed_packets'] += 1
                group = record.get('pkt_group_id')
                if type(group) is int and group > 0:
                    if group in groups:result['repeated_pkt_group_observations'] += 1
                    else:groups.add(group)
                result['max_packet_bytes_observed'] = max(result['max_packet_bytes_observed'], len(raw))
                result['packet_size_gt_9000_observed'] |= len(raw) > 9000
                if type(record.get('packet_type')) is not int or record['packet_type'] not in (1, 3):
                    result['ignored_unsupported_types'] += 1
                    continue
                packet = parse_packet(raw, record['packet_type'])
                if packet is None:
                    result['ignored_non_tcp'] += 1
                    continue
                source = (packet['source'], packet['source_port'])
                destination = (packet['destination'], packet['destination_port'])
                if tuple(sorted((source, destination))) != pair:
                    raise NativeCaptureError('O filtro nativo recebeu uma conexão fora dos sockets da prova.')
                direction = 'client-to-server' if source == client_endpoint else 'server-to-client'
                if packet['payload']:
                    ledgers[direction].feed(packet['tcp_seq_raw'], packet['payload'])
                    if result['first_payload_ms'] is None:result['first_payload_ms'] = round((now - start) * 1000, 3)
                    timestamp = record.get('timestamp_filetime')
                    sample_error = None
                    if timestamp is None:
                        result['latency_missing_samples'] += 1
                        sample_error = 'FILETIME ausente no payload capturado.'
                    elif type(timestamp) is not int or not FILETIME_EPOCH < timestamp < 2**63:
                        result['latency_invalid_samples'] += 1
                        sample_error = 'Timestamp do payload fora do formato FILETIME.'
                    else:
                        latency = (time.time_ns() - (timestamp - FILETIME_EPOCH) * 100) / 1e6
                        if not 0 <= latency <= 60000:
                            result['latency_invalid_samples'] += 1
                            sample_error = 'FILETIME futuro ou anterior à janela desta prova.'
                        else:
                            receive_latency.append(latency)
                            if latency > 1000:result['latency_late_samples'] += 1
                    if sample_error and len(result['latency_sample_errors']) < 8:
                        result['latency_sample_errors'].append(sample_error)
            with traffic_lock:errors = list(traffic_errors)
            if errors:raise OSError('; '.join(errors))
            complete = all(ledger.matches() for ledger in ledgers.values())
            if complete and client_done.is_set() and peer_done.is_set():
                if drain_until is None:drain_until = now + .2
                if now >= drain_until:break
        if not all(ledger.matches() for ledger in ledgers.values()):
            summaries = [ledger.summary() for ledger in ledgers.values()]
            if any(state['conflicting_bytes'] or state['unexpected_bytes'] for state in summaries):
                raise PacketParseError('Sobreposição conflitante ou bytes fora do payload da prova.')
            if all(state['complete'] for state in summaries):
                raise PacketParseError('A quantidade capturada confere, mas o SHA-256 do payload diverge.')
            raise TimeoutError('Captura não reconstruiu todos os bytes de localhost no prazo.')
        if not client_done.is_set() or not peer_done.is_set():
            raise TimeoutError('O tráfego próprio não terminou no prazo.')
        with traffic_lock:
            errors = list(traffic_errors)
            received = dict(result['traffic_received_bytes'])
        if errors:raise OSError('; '.join(errors))
        if any(count != size for count in received.values()):
            raise PacketParseError('Os bytes recebidos pelos sockets próprios diferem do payload esperado.')
        if not receive_latency or result['latency_missing_samples'] or result['latency_invalid_samples']:
            raise NativeCaptureError('Não há FILETIME válido para medir a latência de todos os payloads capturados.')
        if result['latency_late_samples']:
            raise NativeCaptureError('Latência nativa observada ultrapassou 1000 ms; tempo real não foi qualificado.')
        result['realtime_within_one_second'] = True
        result['status'] = 'PASS'
    except Exception as exc:
        result['error'] = str(exc)
        code = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
        if not result['capture_started'] and (isinstance(exc, NativeCaptureError) or
                family == socket.AF_INET6 and code in IPV6_UNAVAILABLE):
            result['status'] = 'UNAVAILABLE'
            result['unavailable_reason'] = 'IPv6 localhost indisponível.' if code in IPV6_UNAVAILABLE else 'A API não conseguiu iniciar a sessão própria.'
    finally:
        stop.set()
        for sock in reversed(sockets):
            try:sock.close()
            except OSError:pass
        for worker in workers:worker.join(timeout=.15)
        if backend is not None:
            try:backend.close()
            except Exception as exc:
                result['status'] = 'FAIL';result['error'] = 'Falha ao fechar a sessão própria: ' + str(exc)
            meta = backend.meta
            result['backend_state'] = meta
            result['loss'] = {key:meta.get(key, 0) for key in LOSS_FIELDS}
            result['cleanup_errors'] = list(meta.get('cleanup_errors') or [])
            result['close_incomplete'] = bool(meta.get('close_incomplete'))
            if any(result['loss'].values()) or meta.get('error') and result['capture_started']:
                result['status'] = 'FAIL';result['error'] = meta.get('error') or 'A captura informou perda de eventos.'
            if result['cleanup_errors'] or result['close_incomplete']:
                result['status'] = 'FAIL'
                detail = 'Encerramento incompleto da sessão nativa própria.'
                if result['cleanup_errors']:detail += ' ' + '; '.join(result['cleanup_errors'])
                result['error'] = result['error'] + '; ' + detail if result['error'] else detail
        result['streams'] = {key:ledger.summary() for key, ledger in ledgers.items()}
        result['unique_pkt_groups'] = len(groups)
        result['captured_bytes'] = sum(stream['captured_bytes'] for stream in result['streams'].values())
        result['duration_seconds'] = round(max(0, clock() - start), 3)
        result['latency_valid_samples'] = len(receive_latency)
        if receive_latency:
            result['latency_ms'] = dict(samples=len(receive_latency), minimum=round(min(receive_latency), 3),
                mean=round(sum(receive_latency)/len(receive_latency), 3), maximum=round(max(receive_latency), 3))
    return result


def run_loopback_probe(output=None, *, timeout=12.0, backend_factory=None,
                       socket_factory=None, capability_reader=None, clock=None, on_case=None):
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not .5 <= timeout <= 15:
        raise ValueError('O prazo da prova deve estar entre 0,5 e 15 segundos por caso.')
    capability = (capability_reader or _native_capability)()
    report = _base_report('loopback', capability)
    if not capability.get('ready'):
        report['detail'] = capability.get('error') or 'API indisponível ou sem administrador; nenhum socket foi aberto.'
        return _write_report(report, output)
    cleanup_failed = False
    for family, case, size in CASES:
        if cleanup_failed:
            result = dict(case=case, family='IPv4' if family == socket.AF_INET else 'IPv6',
                application_write_bytes=size, status='UNAVAILABLE', expected_bytes=2 * size,
                captured_bytes=0, capture_started=False, network_opened=False,
                error='Caso não executado após falha de encerramento da sessão anterior.',
                unavailable_reason='Falha no encerramento da sessão anterior.', streams={}, loss={})
        else:
            result = _run_case(family, case, size, backend_factory=backend_factory or NativeCapture,
                socket_factory=socket_factory or socket.socket, clock=clock or time.monotonic, timeout=timeout)
            cleanup_failed = bool(result.get('cleanup_errors') or result.get('close_incomplete'))
        report['cases'].append(result)
        report['capture_started'] |= result['capture_started']
        report['network_opened'] |= result['network_opened']
        if on_case:on_case(result)
    statuses = {case['status'] for case in report['cases']}
    report['status'] = 'FAIL' if 'FAIL' in statuses else 'UNAVAILABLE' if 'UNAVAILABLE' in statuses else 'PASS'
    report['native_loopback_validated'] = report['status'] == 'PASS'
    # Controls diagnose a standard case that received no native observations.
    # Their result never qualifies the four standard cases or game capture.
    if not cleanup_failed:
        for family, label in ((socket.AF_INET, 'IPv4'), (socket.AF_INET6, 'IPv6')):
            affected = any(case['family'] == label and case['capture_started']
                and case.get('observed_packets') == 0 and not case.get('cleanup_errors')
                and not case.get('close_incomplete') for case in report['cases'])
            if not affected:
                continue
            control = _run_case(family, label.lower() + '-symmetric-ports', 4096,
                backend_factory=backend_factory or NativeCapture,
                socket_factory=socket_factory or socket.socket, clock=clock or time.monotonic,
                timeout=timeout, symmetric_ports=True)
            control['diagnostic_only'] = True
            control['purpose'] = 'Portas TCP exatas com bytes iguais; mesmos IPs localhost e protocolo, sem ampliar o filtro.'
            report['diagnostic_controls'].append(control)
            if on_case:on_case(control)
            if control.get('cleanup_errors') or control.get('close_incomplete'):
                cleanup_failed = True
                break
    report['detail'] = ('Os quatro fluxos próprios foram reconstruídos sem perda detectada. '
        'Pacotes de jogo e coalescência acima de 9000 bytes ainda não foram validados.') if report['status'] == 'PASS' else 'A prova não qualificou todos os casos; consulte bytes, lacunas e erros de cada caso.'
    empty = [case for case in report['cases'] if case['capture_started'] and case.get('observed_packets') == 0]
    if empty and all(all(count == case['application_write_bytes'] for count in
            case.get('traffic_received_bytes', {}).values()) for case in empty):
        report['detail'] += ' Os sockets próprios receberam os bytes esperados, mas nesses casos não chegou nenhum callback de pacote. Consulte diagnostic_controls para o controle separado de portas com bytes iguais.'
    return _write_report(report, output)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prova isolada de captura nativa com TCP localhost próprio.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Somente ler API/admin; sem sockets ou captura.')
    mode.add_argument('--loopback', action='store_true', help='Testar IPv4/IPv6 próprios; padrão ao omitir modo.')
    parser.add_argument('--output', help='Arquivo JSON ou pasta para o relatório.')
    parser.add_argument('--timeout', type=float, default=12.0, help='Prazo de 0,5 a 15 segundos por caso; padrão 12.')
    args = parser.parse_args(argv)
    try:
        report = probe(args.output) if args.check else run_loopback_probe(args.output, timeout=args.timeout,
            on_case=lambda case:print(f"{case['case']}: {case['status']} · {case['captured_bytes']}/{case['expected_bytes']} bytes", flush=True))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(report['status'] + ' · ' + report['detail'])
    print('Relatório: ' + report['report_path'])
    return {'PASS':0, 'FAIL':2, 'UNAVAILABLE':3}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
