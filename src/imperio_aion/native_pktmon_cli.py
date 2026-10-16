# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Isolated PktMon CLI experiment, guarded against pre-existing global state.

The CLI is global. The checks below are conservative, not an OS ownership lock:
another administrator can race them. This is a controlled proof, not a public
capture backend. No unknown session/filter is stopped or removed deliberately.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid

from native_pktmon import NativeCaptureError


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ''


def normalize(text):
    return ' '.join(text.replace('\ufeff', '').split())


IDLE = ('Packet Monitor is not running.', 'O Monitor de Pacotes não está em execução.')
EMPTY = ('No packet filters specified.', 'Nenhum filtro de pacote foi especificado.')
HEADERS = ('Packet Filters:', 'Filtros de Pacote:')
LOG_LINE = re.compile(r'^\s*(?:Log file|Arquivo de log):\s*(.*?)\s*$', re.MULTILINE)


def classify_status(result):
    if result.returncode != 0 or result.stderr.strip():
        return {'state': 'unknown', 'log_path': None}
    if normalize(result.stdout) in IDLE:
        return {'state': 'idle', 'log_path': None}
    matches = LOG_LINE.findall(result.stdout)
    if len(matches) == 1 and matches[0] and Path(matches[0].strip('"')).is_absolute():
        return {'state': 'active', 'log_path': matches[0].strip('"')}
    return {'state': 'unknown', 'log_path': None}


def parse_filter_names(result):
    """Only recognize the documented PT/EN CLI table; unknown means refuse."""
    if result.returncode != 0 or result.stderr.strip():
        return None
    text = normalize(result.stdout)
    if text in EMPTY or any(text == header + ' ' + empty for header in HEADERS for empty in EMPTY):
        return ()
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines or lines[0] not in HEADERS:
        return None
    if len(lines) < 3 or not re.match(r'^#\s+(?:Name|Nome)(?:\s|$)', lines[1]):
        return None
    names = []
    for line in lines[2:]:
        if re.fullmatch(r'[-\s]+', line):
            continue
        match = re.match(r'^(\d+)\s+(\S+)(?:\s|$)', line)
        if not match:
            return None
        names.append(match[2])
    return tuple(names) if names else None


def _decode(data):
    # Native redirected output uses an OEM code page on some Windows builds.
    options = []
    for codec in ('utf-8', 'oem', 'cp1252'):
        try:
            value = data.decode(codec)
        except (UnicodeError, LookupError):
            continue
        options.append(value)
        if any(token in value for token in (*IDLE, *EMPTY, *HEADERS, 'Arquivo de log:')):
            return value
    return options[0] if options else data.decode('utf-8', errors='replace')


def run_command(argv):
    """Launch just the system tool; no shell, automatic elevation or helper."""
    completed = subprocess.run(list(argv), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), shell=False)
    if len(completed.stdout) + len(completed.stderr) > 262144:
        raise NativeCaptureError('Resposta do PktMon excedeu o limite da prova.')
    return CommandResult(completed.returncode, _decode(completed.stdout), _decode(completed.stderr))


_PROOF_MUTEX = threading.Lock()


class NativeCliCapture:
    def __init__(self, endpoints, *, runner=None, etw_factory=None, root=None):
        if len(endpoints) != 1 or len(endpoints[0]) != 2:
            raise NativeCaptureError('Esta prova aceita somente um par TCP próprio por sessão.')
        pair = []
        for address, port in endpoints[0]:
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback or type(port) is not int or not 1 <= port <= 65535:
                raise NativeCaptureError('A prova aceita somente endpoints localhost exatos.')
            pair.append((str(ip), port))
        if ipaddress.ip_address(pair[0][0]).version != ipaddress.ip_address(pair[1][0]).version or pair[0] == pair[1]:
            raise NativeCaptureError('O par TCP próprio é inválido.')
        self.endpoints = [tuple(sorted(pair))]
        self.runner = runner or run_command
        if etw_factory is None:
            from native_etw import EtwConsumer
            etw_factory = EtwConsumer
        self.etw_factory = etw_factory
        self.root = Path(root) if root is not None else Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'ImperioAION' / 'diagnosticos-nativos'
        self.token = uuid.uuid4().hex
        self.filter_name = 'iap' + self.token[:12]
        self.directory = self.root / ('etw-prova-' + self.token)
        self.log_path = self.directory / 'prova.etl'
        self.executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'pktmon.exe')
        self.listener = None
        self.snapshot = None
        self.filter_attempted = self.start_attempted = self.started = False
        self.locked = self.closed = False
        self.meta = dict(backend='pktmon-cli-etw160', max_packet_bytes=65831,
            global_cli=True, exclusive_ownership_guaranteed=False, data_callbacks=0,
            queue_dropped=0, event_warnings=0, missed_write=0, missed_read=0,
            cleanup_errors=[], close_incomplete=False, error=None, commands=[])

    def _command(self, *args):
        result = self.runner((self.executable, *map(str, args)))
        self.meta['commands'].append(dict(command=list(map(str, args)), returncode=result.returncode,
            stdout_bytes=len(result.stdout.encode('utf-8')), stderr_bytes=len(result.stderr.encode('utf-8')),
            stdout_sha256=hashlib.sha256(result.stdout.encode('utf-8')).hexdigest()))
        return result

    def _filters_match(self, result):
        return self.snapshot is not None and parse_filter_names(result) == (self.filter_name,) and normalize(result.stdout) == self.snapshot

    def _owns_log(self, status):
        return status['state'] == 'active' and os.path.normcase(os.path.abspath(status['log_path'])) == os.path.normcase(os.path.abspath(self.log_path))

    def start(self):
        if self.closed or self.locked or self.started:
            raise NativeCaptureError('A sessão da prova não pode ser reutilizada.')
        if not _PROOF_MUTEX.acquire(blocking=False):
            raise NativeCaptureError('Outra prova deste processo está em andamento.')
        self.locked = True
        try:
            if classify_status(self._command('status'))['state'] != 'idle':
                raise NativeCaptureError('PktMon ativo, sem permissão ou estado desconhecido; nenhuma captura global foi alterada.')
            if parse_filter_names(self._command('filter', 'list')) != ():
                raise NativeCaptureError('PktMon já tem filtros ou respondeu em formato desconhecido; nenhum filtro foi alterado.')
            self.directory.mkdir(parents=True, exist_ok=False)
            self.listener = self.etw_factory(accepted_pairs=self.endpoints)
            self.listener.start()
            # Recheck after the ETW setup before the first global mutation.
            if classify_status(self._command('status'))['state'] != 'idle' or parse_filter_names(self._command('filter', 'list')) != ():
                raise NativeCaptureError('O estado global do PktMon mudou antes da prova.')
            first, second = self.endpoints[0]
            self.filter_attempted = True
            added = self._command('filter', 'add', self.filter_name, '-t', 'TCP', '-i', first[0], second[0], '-p', first[1], second[1])
            current = self._command('filter', 'list')
            if parse_filter_names(current) == (self.filter_name,):
                self.snapshot = normalize(current.stdout)
            if added.returncode != 0 or added.stderr.strip() or self.snapshot is None:
                raise NativeCaptureError('O PktMon não confirmou o filtro TCP exato da prova.')
            if classify_status(self._command('status'))['state'] != 'idle' or not self._filters_match(self._command('filter', 'list')):
                raise NativeCaptureError('O estado global do PktMon mudou antes de iniciar.')
            self.start_attempted = True
            result = self._command('start', '--capture', '--comp', 'all', '--pkt-size', '0', '--flags', '0x11',
                '--file-name', self.log_path, '--file-size', '32', '--log-mode', 'circular')
            status = classify_status(self._command('status'))
            if result.returncode != 0 or result.stderr.strip() or not self._owns_log(status) or not self._filters_match(self._command('filter', 'list')):
                raise NativeCaptureError('O PktMon não confirmou a captura com o arquivo exclusivo da prova.')
            self.started = True
            self.meta['capture_started'] = True
            return self
        except Exception as exc:
            self.meta['error'] = str(exc)
            raise NativeCaptureError(str(exc)) from exc

    def read(self, timeout=.1):
        if not self.started or self.closed:
            return None
        return self.listener.read(timeout=timeout)

    def _cleanup_error(self, text):
        self.meta['cleanup_errors'].append(text)
        self.meta['close_incomplete'] = True

    def close(self):
        if self.closed:
            return self.meta
        try:
            if self.filter_attempted:
                filters = self._command('filter', 'list')
                status = classify_status(self._command('status'))
                if not self._filters_match(filters):
                    self._cleanup_error('Filtros mudaram ou não foram reconhecidos; nenhuma captura/filtro global foi removido.')
                else:
                    if self.start_attempted and self._owns_log(status):
                        # Final recheck immediately before the global stop.
                        if not self._filters_match(self._command('filter', 'list')) or not self._owns_log(classify_status(self._command('status'))):
                            raise NativeCaptureError('A identidade da captura mudou antes do encerramento.')
                        stopped = self._command('stop')
                        if stopped.returncode or stopped.stderr.strip():
                            raise NativeCaptureError('PktMon não confirmou o encerramento da prova.')
                        status = classify_status(self._command('status'))
                    if status['state'] != 'idle':
                        self._cleanup_error('Outra captura ou estado desconhecido está ativo; nenhum filtro foi removido.')
                    elif self._filters_match(self._command('filter', 'list')) and classify_status(self._command('status'))['state'] == 'idle':
                        removed = self._command('filter', 'remove')
                        if removed.returncode or removed.stderr.strip() or parse_filter_names(self._command('filter', 'list')) != ():
                            self._cleanup_error('O Windows não confirmou a remoção do filtro próprio da prova.')
                    else:
                        self._cleanup_error('Filtros/estado mudaram antes da remoção; nenhuma remoção executada.')
        except Exception as exc:
            self._cleanup_error(str(exc))
        finally:
            if self.listener is not None:
                try:
                    report = self.listener.close()
                    if report is None:
                        report = getattr(self.listener, 'meta', {})
                    self.meta['etw'] = report
                    for target, origin in (('data_callbacks','packet_events'), ('queue_dropped','queue_dropped'),
                            ('missed_read','events_lost')):
                        self.meta[target] = report.get(origin, 0)
                    self.meta['missed_write'] = report.get('buffers_lost', report.get('log_buffers_lost', 0) + report.get('realtime_buffers_lost', 0))
                    self.meta['event_warnings'] = sum(report.get(key, 0) for key in ('event_errors', 'tdh_errors', 'parse_errors', 'truncated_events', 'packet_drop_events'))
                    if report.get('error'):
                        self.meta['error'] = report['error']
                    if report.get('truncated_packets') and 'truncated_events' not in report:
                        self.meta['event_warnings'] += report['truncated_packets']
                    if report.get('foreign_payload_events'):
                        self.meta['event_warnings'] += report['foreign_payload_events']
                    if report.get('close_incomplete') or report.get('cleanup_errors'):
                        self._cleanup_error('O consumidor ETW próprio não encerrou completamente.')
                except Exception as exc:
                    self._cleanup_error('Encerramento ETW: ' + str(exc))
            if not self.meta['close_incomplete'] and self.directory.is_dir():
                try:
                    # Fixed file in a newly created UUID directory, never recursive.
                    self.log_path.unlink(missing_ok=True)
                    self.directory.rmdir()
                except OSError as exc:
                    self._cleanup_error('Não foi possível apagar o arquivo ETL próprio: ' + str(exc))
            if self.locked:
                _PROOF_MUTEX.release()
                self.locked = False
            self.closed = True
        return self.meta
