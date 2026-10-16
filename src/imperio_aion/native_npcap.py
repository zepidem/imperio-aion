# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Direct, read-only Npcap backend; no Wireshark, installer or bundled driver.

Loads only System32/Npcap/wpcap.dll, with dependency lookup restricted to that
directory and System32. Uses libpcap's cdecl ABI, exact localhost endpoint BPF,
and validates the same pair again before queueing. pcap_setfilter success does
not prove kernel placement: libpcap can fall back to a userspace filter. No
packet is read before the exact filter is installed. Reports contain counts,
never packet data. --check reads DLL exports/version/registry only.

Primary sources reviewed 2026-10-14:
https://npcap.com/guide/npcap-devguide.html (DLL path, NULL IPv4=2/IPv6=24)
https://npcap.com/guide/npcap-api.html (event waits, six-field Windows stats)
https://npcap.com/guide/wpcap/pcap_next_ex.html
https://npcap.com/guide/wpcap/pcap_setnonblock.html
https://npcap.com/guide/wpcap/pcap_activate.html
https://raw.githubusercontent.com/the-tcpdump-group/libpcap/master/pcap/pcap.h
https://raw.githubusercontent.com/the-tcpdump-group/libpcap/master/pcap-npf.c

Capture is limited to DLT_NULL on NPF_Loopback. Fragments/unsupported IP shapes
remain explicit errors in native_packets. Caller-side close is bounded; if an
SDK call or worker hangs, no concurrent pcap_close is attempted and the owner
is strongly retained. No injection, remote capture API, elevation, adapter
changes, service start/stop, or installation is performed.
"""
from collections import deque
import argparse
import ctypes as ct
import ipaddress
import json
import math
import os
from pathlib import Path
import threading
import time

from native_packets import MAX_PACKET_BYTES, PacketParseError, parse_packet

SNAPLEN = 262144
BUFFER_BYTES = 4 * 1024 * 1024
MAX_FRAME_BYTES = MAX_PACKET_BYTES + 4
LOOPBACK_DEVICE = r'\Device\NPF_Loopback'
FILETIME_EPOCH = 116444736000000000
U8, U16, U32, I32, PTR = ct.c_uint8, ct.c_uint16, ct.c_uint32, ct.c_int32, ct.c_void_p
_OWNED_CAPTURES = set()
_OWNERS_LOCK = threading.Lock()


class NpcapError(RuntimeError):
    """Unavailable or incomplete capture, never silently replayed/fallback."""


NativeCaptureError = NpcapError


class NpcapTruncationError(NpcapError):
    """Incomplete packet from the SDK, before any bytes are queued."""


class Timeval(ct.Structure):
    _fields_ = [('tv_sec', I32), ('tv_usec', I32)]


class PcapHeader(ct.Structure):
    _fields_ = [('ts', Timeval), ('caplen', U32), ('len', U32)]


class PcapInterface(ct.Structure):
    pass


PcapInterface._fields_ = [('next', ct.POINTER(PcapInterface)), ('name', ct.c_char_p),
    ('description', ct.c_char_p), ('addresses', PTR), ('flags', U32)]


class BpfProgram(ct.Structure):
    _fields_ = [('bf_len', U32), ('bf_insns', PTR)]


class PcapStats(ct.Structure):
    # _WIN32 fields include ps_sent/ps_netdrop even though unused by Npcap.
    _fields_ = [(name, U32) for name in ('ps_recv', 'ps_drop', 'ps_ifdrop', 'ps_capt', 'ps_sent', 'ps_netdrop')]


def abi_report():
    sizes = dict(timeval=ct.sizeof(Timeval), pcap_pkthdr=ct.sizeof(PcapHeader),
        pcap_if_t=ct.sizeof(PcapInterface), bpf_program=ct.sizeof(BpfProgram), pcap_stat=ct.sizeof(PcapStats))
    expected = dict(timeval=8, pcap_pkthdr=16, pcap_if_t=40, bpf_program=16, pcap_stat=24)
    return dict(pointer_bits=ct.sizeof(PTR)*8, sizes=sizes,
        valid=ct.sizeof(PTR)==8 and sizes==expected and BpfProgram.bf_insns.offset==8)


def normalize_pairs(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 32:
        raise ValueError('São necessários 1 a 32 pares TCP localhost exatos.')
    result = set()
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('Par TCP inválido.')
        endpoints = []
        for endpoint in pair:
            if not isinstance(endpoint, (tuple, list)) or len(endpoint) != 2:
                raise ValueError('Endpoint TCP inválido.')
            address, port = endpoint
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback or '%' in str(ip) or type(port) is not int or not 1 <= port <= 65535:
                raise ValueError('A captura aceita somente endpoints localhost com portas exatas.')
            endpoints.append((str(ip), port))
        if endpoints[0] == endpoints[1] or ipaddress.ip_address(endpoints[0][0]).version != ipaddress.ip_address(endpoints[1][0]).version:
            raise ValueError('Par TCP localhost incompatível.')
        result.add(tuple(sorted(endpoints)))
    return tuple(sorted(result))


def exact_filter(keys):
    parts = []
    for (source, source_port), (destination, destination_port) in normalize_pairs(keys):
        family = 'ip' if ipaddress.ip_address(source).version == 4 else 'ip6'
        parts.append(f'({family} and ((src host {source} and src port {source_port} and dst host {destination} and dst port {destination_port}) or (src host {destination} and src port {destination_port} and dst host {source} and dst port {source_port})))')
    return 'tcp and (' + ' or '.join(parts) + ')'


class WindowsNpcapApi:
    """Safe DLL/SDK bindings; constructor creates no pcap handle."""
    def __init__(self):
        if os.name != 'nt' or not abi_report()['valid']:
            raise NpcapError('Npcap direto requer Windows x64 e ABI compatível.')
        self.kernel = ct.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        self.kernel.GetSystemDirectoryW.argtypes = [ct.c_wchar_p, U32]
        self.kernel.GetSystemDirectoryW.restype = U32
        directory = ct.create_unicode_buffer(32768)
        size = self.kernel.GetSystemDirectoryW(directory, len(directory))
        if not 0 < size < len(directory):raise NpcapError('Pasta de sistema não encontrada.')
        self.dll_path = str(Path(directory.value) / 'Npcap' / 'wpcap.dll')
        if not Path(self.dll_path).is_file():
            raise NpcapError('Npcap não encontrado; instale-o pelo site oficial.')
        # CDLL = cdecl. LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | SYSTEM32 prevents
        # dependency resolution from cwd/PATH. Keep this library object alive.
        self.library = ct.CDLL(self.dll_path, use_last_error=True, winmode=0x100 | 0x800)
        definitions = [
            ('pcap_lib_version', ct.c_char_p, []),
            ('pcap_findalldevs', I32, [ct.POINTER(ct.POINTER(PcapInterface)), ct.c_char_p]),
            ('pcap_freealldevs', None, [ct.POINTER(PcapInterface)]),
            ('pcap_create', PTR, [ct.c_char_p, ct.c_char_p]),
            ('pcap_set_snaplen', I32, [PTR, I32]), ('pcap_set_promisc', I32, [PTR, I32]),
            ('pcap_set_timeout', I32, [PTR, I32]), ('pcap_set_buffer_size', I32, [PTR, I32]),
            ('pcap_activate', I32, [PTR]), ('pcap_datalink', I32, [PTR]),
            ('pcap_compile', I32, [PTR, ct.POINTER(BpfProgram), ct.c_char_p, I32, U32]),
            ('pcap_setfilter', I32, [PTR, ct.POINTER(BpfProgram)]),
            ('pcap_freecode', None, [ct.POINTER(BpfProgram)]),
            ('pcap_setnonblock', I32, [PTR, I32, ct.c_char_p]),
            ('pcap_getevent', PTR, [PTR]),
            ('pcap_next_ex', I32, [PTR, ct.POINTER(ct.POINTER(PcapHeader)), ct.POINTER(ct.POINTER(U8))]),
            ('pcap_stats', I32, [PTR, ct.POINTER(PcapStats)]),
            ('pcap_geterr', ct.c_char_p, [PTR]), ('pcap_close', None, [PTR]),
        ]
        self.exports = []
        for name, result, arguments in definitions:
            try:function = getattr(self.library, name)
            except AttributeError as exc:raise NpcapError('Export Npcap ausente: ' + name) from exc
            function.restype, function.argtypes = result, arguments
            setattr(self, name, function);self.exports.append(name)
        self.immediate = getattr(self.library, 'pcap_set_immediate_mode', None)
        if self.immediate is not None:
            self.immediate.argtypes, self.immediate.restype = [PTR, I32], I32
        self.precision = getattr(self.library, 'pcap_get_tstamp_precision', None)
        if self.precision is not None:
            self.precision.argtypes, self.precision.restype = [PTR], I32
        self.kernel.WaitForSingleObject.argtypes = [PTR, U32]
        self.kernel.WaitForSingleObject.restype = U32
        raw_version = self.pcap_lib_version()
        self.version = raw_version.decode('utf-8', 'replace')[:512] if raw_version else ''
        if not self.version.lower().startswith('npcap'):
            raise NpcapError('A biblioteca encontrada não se identifica como Npcap.')
        shell = ct.WinDLL('shell32.dll', winmode=0x800)
        shell.IsUserAnAdmin.argtypes, shell.IsUserAnAdmin.restype = [], I32
        self.administrator = bool(shell.IsUserAnAdmin())
        self.admin_only = None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Services\npcap\Parameters') as key:
                try:value, kind = winreg.QueryValueEx(key, 'AdminOnly')
                except FileNotFoundError:value, kind = 0, winreg.REG_DWORD
                if kind == winreg.REG_DWORD and value in (0, 1):self.admin_only = bool(value)
        except OSError:pass

    def error(self, handle):
        raw = self.pcap_geterr(handle)
        return raw.decode('utf-8', 'replace')[:512] if raw else 'erro sem descrição'

    def devices(self):
        devices = ct.POINTER(PcapInterface)()
        message = ct.create_string_buffer(256)
        code = int(self.pcap_findalldevs(ct.byref(devices), message))
        if code != 0:
            detail = message.value.decode('utf-8', 'replace')[:256]
            raise NpcapError('Não foi possível enumerar as interfaces Npcap (' + str(code) + '): ' + detail)
        result, seen = [], set()
        try:
            current = devices
            while current:
                address = ct.addressof(current.contents)
                if address in seen or len(seen) >= 2048:
                    raise NpcapError('Lista de interfaces Npcap inválida.')
                seen.add(address)
                if current.contents.name:
                    result.append(current.contents.name.decode('utf-8', 'replace'))
                current = current.contents.next
        finally:self.pcap_freealldevs(devices)
        return result

    def create(self, device):
        message = ct.create_string_buffer(256)
        handle = self.pcap_create(device.encode('ascii'), message)
        if not handle:
            detail = message.value.decode('utf-8', 'replace')[:256]
            raise NpcapError('Não foi possível abrir o loopback Npcap: ' + detail)
        return handle

    def configure(self, handle):
        for name, value in (('pcap_set_snaplen', SNAPLEN), ('pcap_set_promisc', 0),
                            ('pcap_set_timeout', 100), ('pcap_set_buffer_size', BUFFER_BYTES)):
            if getattr(self, name)(handle, value) != 0:raise NpcapError(name + ': ' + self.error(handle))
        if self.immediate is not None and self.immediate(handle, 1) != 0:
            raise NpcapError('pcap_set_immediate_mode: ' + self.error(handle))
        return self.immediate is not None

    def activate(self, handle):return int(self.pcap_activate(handle))
    def datalink(self, handle):return int(self.pcap_datalink(handle))
    def timestamp_precision(self, handle):return int(self.precision(handle)) if self.precision is not None else 0

    def filter(self, handle, expression):
        program = BpfProgram()
        if self.pcap_compile(handle, ct.byref(program), expression.encode('ascii'), 1, 0xffffffff) != 0:
            raise NpcapError('O Npcap não compilou o filtro exato: ' + self.error(handle))
        try:
            if self.pcap_setfilter(handle, ct.byref(program)) != 0:
                raise NpcapError('O Npcap não aplicou o filtro exato: ' + self.error(handle))
        finally:self.pcap_freecode(ct.byref(program))

    def nonblock(self, handle):
        message = ct.create_string_buffer(256)
        if self.pcap_setnonblock(handle, 1, message) != 0:
            raise NpcapError('Não foi possível ativar leitura Npcap não bloqueante.')

    def event(self, handle):
        result = self.pcap_getevent(handle)
        if not result:raise NpcapError('Npcap não retornou evento de leitura.')
        return result

    def wait(self, handle, milliseconds):
        result = int(self.kernel.WaitForSingleObject(handle, milliseconds))
        if result not in (0, 258):raise NpcapError('WaitForSingleObject falhou: ' + str(result))
        return result == 0

    def next_packet(self, handle):
        header = ct.POINTER(PcapHeader)();data = ct.POINTER(U8)()
        code = int(self.pcap_next_ex(handle, ct.byref(header), ct.byref(data)))
        if code == 0:return None
        if code != 1:raise NpcapError('pcap_next_ex: ' + self.error(handle))
        if not header or not data:raise NpcapError('Npcap retornou ponteiro de pacote vazio.')
        h = header.contents
        if not 4 < h.caplen <= MAX_FRAME_BYTES or h.caplen != h.len:
            raise NpcapTruncationError('Pacote Npcap truncado ou fora do limite.')
        # Copy before the next SDK call can reuse its packet buffer.
        return dict(data=ct.string_at(data, h.caplen), caplen=int(h.caplen), original_length=int(h.len),
                    seconds=int(h.ts.tv_sec), microseconds=int(h.ts.tv_usec))

    def statistics(self, handle):
        result = PcapStats()
        if self.pcap_stats(handle, ct.byref(result)) != 0:
            raise NpcapError('Não foi possível verificar perdas Npcap: ' + self.error(handle))
        return {name:int(getattr(result, name)) for name, _ in result._fields_}

    def close(self, handle):self.pcap_close(handle)


def probe(api_factory=None):
    """Only DLL/version/export/registry metadata. No adapter enumeration/open."""
    result = dict(backend='npcap-direct', available=False, ready=False, experimental=True,
        live_validated=False, capture_started=False, abi=abi_report(), error=None,
        requires_external_driver=True, external_driver_bundled=False, max_packet_bytes=MAX_PACKET_BYTES)
    try:
        api = (api_factory or WindowsNpcapApi)()
        if not api.version.lower().startswith('npcap'):raise NpcapError('Biblioteca não identifica Npcap.')
        result.update(available=True, dll_path=getattr(api, 'dll_path', None), version=api.version,
            exports=list(api.exports), admin_only=api.admin_only, administrator=api.administrator)
        result['ready'] = bool(api.administrator or api.admin_only is False)
        if not result['ready']:result['error'] = 'A instalação Npcap restringe acesso ou não permitiu conferir permissões; abra como administrador.'
    except Exception as exc:result['error'] = str(exc)
    return result


class NativeCapture:
    def __init__(self, exact_endpoint_keys, on_error=None, *, api=None, queue_capacity=512):
        self.keys = normalize_pairs(exact_endpoint_keys)
        self.filter_text = exact_filter(self.keys)
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 8192:
            raise ValueError('Capacidade da fila Npcap fora do limite.')
        self.api, self.on_error, self.queue_capacity = api, on_error, queue_capacity
        self._condition = threading.Condition(threading.RLock())
        self._queue = deque();self._stop = threading.Event();self._start_finished = threading.Event()
        self._start_finished.set()
        self._handle = None;self._worker = self._closer = None;self._state = 'initialized'
        self._active = False;self._error = None;self._cleanup_errors = [];self._warnings = []
        self._counts = dict(received=0, delivered=0, queue_dropped=0, missed_read=0,
            missed_write=0, event_warnings=0, foreign_payload_events=0,
            ignored_non_tcp=0, parse_errors=0, read_errors=0, truncated_packets=0,
            max_packet_bytes_observed=0)
        self._statistics = {name:0 for name, _ in PcapStats._fields_}
        self.immediate = False

    def _fail(self, message):
        notify = False
        with self._condition:
            if self._error is None:self._error = message;notify = True
            self._stop.set();self._queue.clear();self._condition.notify_all()
        if notify and self.on_error is not None:
            try:self.on_error(message)
            except Exception:pass

    def start(self):
        with self._condition:
            if self._state != 'initialized':raise NpcapError('A sessão Npcap não pode ser reutilizada.')
            self._state = 'starting';self._start_finished.clear()
        with _OWNERS_LOCK:_OWNED_CAPTURES.add(self)
        failure = None
        try:
            if self.api is None:self.api = WindowsNpcapApi()
            if not self.api.version.lower().startswith('npcap'):
                raise NpcapError('A biblioteca carregada não é Npcap.')
            # pcap_create/enumeration can open devices internally. Refuse before
            # either operation to avoid NpcapHelper/UAC being launched silently.
            if not self.api.administrator and self.api.admin_only is not False:
                raise NpcapError('Npcap exige acesso de administrador; nenhuma elevação automática foi realizada.')
            devices = self.api.devices()
            if devices.count(LOOPBACK_DEVICE) != 1:
                raise NpcapError('NPF_Loopback não foi identificado de forma única no Npcap.')
            self._handle = self.api.create(LOOPBACK_DEVICE)
            if not self._handle:raise NpcapError('Npcap retornou handle vazio.')
            self.immediate = self.api.configure(self._handle)
            status = self.api.activate(self._handle)
            if status < 0:
                raise NpcapError('Npcap não ativou o loopback (' + str(status) + '): ' + self.api.error(self._handle))
            self._active = True
            if status > 0:
                self._warnings.append('pcap_activate (' + str(status) + '): ' + self.api.error(self._handle))
                self._counts['event_warnings'] += 1
            if self.api.datalink(self._handle) != 0:
                raise NpcapError('Npcap loopback retornou um link type não qualificado.')
            if self.api.timestamp_precision(self._handle) != 0:
                raise NpcapError('Precisão de timestamp Npcap não qualificada.')
            self.api.filter(self._handle, self.filter_text)
            self.api.nonblock(self._handle)
            self._event = self.api.event(self._handle)
            self._check_statistics()
            if self._stop.is_set():raise NpcapError(self._error or 'Ativação Npcap interrompida.')
            self._state = 'active'
            self._worker = threading.Thread(target=self._read_packets, name='ImperioAION-Npcap', daemon=True)
            self._worker.start()
        except Exception as exc:failure = exc;self._fail(str(exc))
        finally:self._start_finished.set()
        if failure is not None:
            self.close();raise NpcapError(str(failure)) from failure
        return self

    def _check_statistics(self):
        result = self.api.statistics(self._handle)
        with self._condition:
            self._statistics = dict(result)
            self._counts['missed_write'] = int(result.get('ps_drop', 0))
        if result.get('ps_drop', 0) or result.get('ps_ifdrop', 0):
            raise NpcapError('Npcap informou perda de pacotes; leitura interrompida.')

    def _accept(self, record):
        data = record['data']
        if not isinstance(data, bytes) or not 4 < len(data) <= MAX_FRAME_BYTES:
            raise NpcapError('Quadro Npcap inválido ou fora do limite.')
        if record['caplen'] != record['original_length'] or record['caplen'] != len(data):
            raise NpcapTruncationError('Quadro Npcap truncado.')
        family = int.from_bytes(data[:4], 'little')
        raw = data[4:]
        if family not in (2, 24) or raw[0] >> 4 != (4 if family == 2 else 6):
            raise NpcapError('Cabeçalho DLT_NULL Npcap incompatível.')
        try:packet = parse_packet(raw, 3)
        except PacketParseError:
            with self._condition:self._counts['parse_errors'] += 1
            raise
        if packet is None:
            with self._condition:self._counts['ignored_non_tcp'] += 1
            return
        key = tuple(sorted(((packet['source'], packet['source_port']), (packet['destination'], packet['destination_port']))))
        if key not in self.keys:
            with self._condition:self._counts['foreign_payload_events'] += 1
            raise NpcapError('O filtro Npcap recebeu uma conexão fora dos pares próprios.')
        seconds, micros = record['seconds'], record['microseconds']
        if type(seconds) is not int or seconds <= 0 or type(micros) is not int or not 0 <= micros < 1000000:
            raise NpcapError('Timestamp Npcap inválido.')
        stamp = FILETIME_EPOCH + seconds * 10000000 + micros * 10
        if not FILETIME_EPOCH < stamp < 2**63:raise NpcapError('Timestamp Npcap fora do limite FILETIME.')
        result = dict(data=raw, packet_type=3, timestamp_filetime=stamp, packet=packet,
                      drop_reason=0, drop_location=0)
        with self._condition:
            if self._stop.is_set():return
            self._counts['received'] += 1
            self._counts['max_packet_bytes_observed'] = max(self._counts['max_packet_bytes_observed'], len(raw))
            if len(self._queue) >= self.queue_capacity:
                self._counts['queue_dropped'] += 1
                raise NpcapError('Fila Npcap cheia; continuidade perdida.')
            self._queue.append(result);self._condition.notify_all()

    def _read_packets(self):
        try:
            while not self._stop.is_set():
                consumed = 0
                for _ in range(256):
                    if self._stop.is_set():break
                    record = self.api.next_packet(self._handle)
                    if record is None:break
                    self._accept(record);consumed += 1
                self._check_statistics()
                if consumed < 256 and not self._stop.is_set():
                    # SDK event wait has an explicit 100ms bound. A signalled
                    # but drained event still yields briefly to avoid busy spin.
                    signalled = self.api.wait(self._event, 100)
                    if signalled and consumed == 0:self._stop.wait(.005)
        except Exception as exc:
            with self._condition:
                self._counts['read_errors'] += 1
                if isinstance(exc, NpcapTruncationError):self._counts['truncated_packets'] += 1
            self._fail(str(exc))
        finally:
            if self._active:
                try:self._check_statistics()
                except Exception as exc:self._fail(str(exc))
            with self._condition:self._condition.notify_all()

    def read(self, timeout=.1):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 <= timeout <= 60:
            raise ValueError('Prazo de leitura Npcap fora do limite.')
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._queue and self._error is None and not self._stop.is_set():
                remaining = deadline-time.monotonic()
                if remaining <= 0:break
                self._condition.wait(remaining)
            if self._error:raise NpcapError(self._error)
            if not self._queue:return None
            self._counts['delivered'] += 1
            return self._queue.popleft()

    def _close_owned(self):
        self._start_finished.wait()
        if self._worker is not None and self._worker is not threading.current_thread():self._worker.join()
        if self._handle:
            try:
                self.api.close(self._handle)
                self._handle = None;self._active = False
            except Exception as exc:self._cleanup_errors.append('pcap_close: ' + str(exc))

    def close(self, timeout=2.0):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not .01 <= timeout <= 5:
            raise ValueError('Prazo de encerramento Npcap fora do limite.')
        with self._condition:
            self._stop.set();self._queue.clear();self._condition.notify_all()
            if self._state == 'initialized':self._state = 'closed';return self.meta
            self._state = 'stopping'
            if self._closer is None or not self._closer.is_alive():
                self._closer = threading.Thread(target=self._close_owned, name='ImperioAION-Npcap-close', daemon=True)
                self._closer.start()
        if self._closer is not threading.current_thread():self._closer.join(timeout=timeout)
        incomplete = bool(self._handle or self._closer.is_alive() or self._worker is not None and self._worker.is_alive())
        with self._condition:self._state = 'close_incomplete' if incomplete else 'closed'
        if not incomplete:
            with _OWNERS_LOCK:_OWNED_CAPTURES.discard(self)
        return self.meta

    @property
    def meta(self):
        with self._condition:
            return dict(backend='npcap-direct', state=self._state, experimental=True, live_validated=False,
                version=getattr(self.api, 'version', None), interface=LOOPBACK_DEVICE, datalink=0,
                filter=self.filter_text, exact_pair_count=len(self.keys), kernel_filter_verified=False,
                snaplen=SNAPLEN, immediate_mode=self.immediate, queue_capacity=self.queue_capacity,
                queued=len(self._queue), error=self._error, cleanup_errors=list(self._cleanup_errors),
                close_incomplete=self._state == 'close_incomplete', handle_open=bool(self._handle),
                activation_warnings=list(self._warnings), npcap_statistics=dict(self._statistics),
                max_packet_bytes=MAX_PACKET_BYTES, **self._counts)


NpcapCapture = NativeCapture


def main(argv=None):
    parser = argparse.ArgumentParser(description='Conferir Npcap instalado; não inicia captura.')
    parser.add_argument('--check', action='store_true', help='Somente metadados de DLL/exports/registro; padrão.')
    parser.parse_args(argv)
    result = probe();print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 3


if __name__ == '__main__':raise SystemExit(main())
