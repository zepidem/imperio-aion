# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Experimental, handle-owned Windows Packet Monitor API 1.0 capture.

Primary references (consulted 2026-10-14):
https://learn.microsoft.com/en-us/windows/win32/pktmon/pktmon
https://learn.microsoft.com/en-us/windows/win32/pktmon/pktmon-reference
https://learn.microsoft.com/en-us/windows/win32/pktmon/packetmonitor/nf-packetmonitor-packetmonitoraddcaptureconstraint
https://learn.microsoft.com/en-us/windows/win32/pktmon/packetmonitor/nf-packetmonitor-packetmonitorcreaterealtimestream
https://learn.microsoft.com/en-us/windows/win32/pktmon/packetmonitor/nf-packetmonitor-packetmonitorenumdatasources
https://learn.microsoft.com/en-us/windows/win32/pktmon/packetmonitor/nf-packetmonitor-packetmonitoraddsingledatasourcetosession

Microsoft publishes the declarations, but explicitly supplies no SDK header or
import library. Its client minimum-version field is blank; do not advertise
Windows 10/11 support from the existence of pktmon.exe. Capability is probed by
System32 DLL exports, not by running the global pktmon command. API 1.0 requires
administrator access; this module never elevates or changes driver settings.

ABI limitation: the published constraint declaration omits the numeric value
of PACKETMONITOR_MAX_NAME_LENGTH. The local Microsoft PktMonApi.dll version
10.0.26100.9549 (x64), PacketMonitorAddCaptureConstraint RVA 0x8b90, copies 0xD8
bytes of that declaration to its request. The prototype assumes Name[64]
(UTF-16), flags at 128, IPs at 152/168 and ports at 186/188. That copy size alone
does not prove Name[64]: alignment also permits other name lengths/flag offsets.
This remains an unqualified ABI assumption, not an SDK guarantee or live proof.
Real administrator capture, loopback/coalescing and other Windows builds still
require validation before this experimental backend becomes the default.

Each object creates a unique independent live session; it installs complete TCP
endpoint constraints BEFORE activation. No global CLI, filter deletion, foreign
session lookup, network transmission or packet-buffer modification is used.
Every enumerated source is explicitly selected; an empty source list is an
error. The source specifications are passed back as opaque pointers into their
original retained buffer. No undocumented MAX_STRING_LENGTH is assumed and no
source names, descriptions, addresses or GUIDs enter diagnostics. Explicit
selection does not itself prove that Windows exposes the localhost data path.
The first UINT32 of a returned spec is counted numerically, never interpreted
as a closed enum. A real r2 enumeration returned a value outside the documented
0/1 list; the Windows API, rather than our whitelist, validates the roundtrip.
The ring buffer truncates at 9000 bytes even at its maximum setting. Coalesced
packets can exceed this: reported losses, warnings, truncation and queue overflow
invalidate the capture. Callbacks copy bounded bytes and signal only; read()
closes owned handles and raises on loss, so the caller must not retain continuity.
Unsupported payload types remain visible in records for the caller to count and
ignore; only Ethernet/IP records can be checked against an IP declared length.
"""
from collections import deque
import ctypes as ct
import ipaddress
import math
import os
from pathlib import Path
import sys
import threading
import time
import uuid


API_VERSION = 0x00010000
MAX_PACKET_BYTES = 9000
MAX_DESCRIPTOR_BYTES = 65536
MAX_ENDPOINT_PAIRS = 32
MAX_SOURCE_BUFFER_BYTES = 4 * 1024 * 1024
MAX_DATA_SOURCES = 2048
SOURCE_ENUM_ATTEMPTS = 3
NAME_LENGTH = 64
_CALLBACK = getattr(ct, 'WINFUNCTYPE', ct.CFUNCTYPE)
U8, U16, U32, U64 = ct.c_uint8, ct.c_uint16, ct.c_uint32, ct.c_uint64
HANDLE = ct.c_void_p
HRESULT = ct.c_int32
# A native callback pointer is not a Python GC root. Retain every object with
# open handles, including failed cleanup, until its own handles are closed.
_OWNED_CAPTURES = set()
_OWNERS_LOCK = threading.Lock()


class NativeCaptureError(RuntimeError):
    """An unavailable or discontinuous capture; never silently fall back."""


class IPAddress(ct.Union):
    _fields_ = [('IPv4', U32), ('IPv4_bytes', U8 * 4),
                ('IPv6', U64 * 2), ('IPv6_bytes', U8 * 16)]


class ProtocolConstraint(ct.Structure):
    _fields_ = [('Name', U16 * NAME_LENGTH), ('IsPresentValue', U32),
                ('Mac1', U8 * 6), ('Mac2', U8 * 6),
                ('VlanId', U16), ('EtherType', U16), ('DSCP', U16),
                ('TransportProtocol', U8), ('Ip1', IPAddress), ('Ip2', IPAddress),
                ('PrefixLength1', U8), ('PrefixLength2', U8),
                ('Port1', U16), ('Port2', U16), ('TCPFlags', U8),
                ('EncapType', U32), ('VxLanPort', U16), ('Packets', U64), ('Bytes', U64)]


class StreamMetadata(ct.Structure):
    _fields_ = [('PktGroupId', U64), ('PktCount', U16), ('AppearanceCount', U16),
                ('DirectionName', U16), ('PacketType', U16), ('ComponentId', U16),
                ('EdgeId', U16), ('Reserved', U16), ('DropReason', U32),
                ('DropLocation', U32), ('Processor', U16), ('TimeStamp', ct.c_int64)]


class DataDescriptor(ct.Structure):
    _fields_ = [('Data', HANDLE), ('DataSize', U32), ('MetadataOffset', U32),
                ('PacketOffset', U32), ('PacketLength', U32),
                ('MissedPacketWriteCount', U32), ('MissedPacketReadCount', U32)]


class StreamStartInfo(ct.Structure):
    _fields_ = [('PacketBufferSizeInBytes', U32), ('TruncationSize', U16)]


class StreamStopInfo(ct.Structure):
    _fields_ = [('IsFatalError', U8), ('Reason', U32)]


class StreamProcessInfo(ct.Structure):
    _fields_ = [('IsWarning', U8), ('Reason', U32), ('PacketLength', U64)]


class StreamEventInfo(ct.Union):
    _fields_ = [('StreamStartInfo', StreamStartInfo), ('StreamStopInfo', StreamStopInfo),
                ('StreamProcessInfo', StreamProcessInfo)]


EVENT_CALLBACK = _CALLBACK(None, HANDLE, ct.POINTER(StreamEventInfo), ct.c_int32)
DATA_CALLBACK = _CALLBACK(None, HANDLE, ct.POINTER(DataDescriptor))


class StreamConfiguration(ct.Structure):
    _fields_ = [('UserContext', HANDLE), ('EventCallback', EVENT_CALLBACK),
                ('DataCallback', DATA_CALLBACK), ('BufferSizeMultiplier', U16),
                ('TruncationSize', U16)]


class DataSourceList(ct.Structure):
    # Only the documented list header is decoded. Specifications stay opaque.
    _fields_ = [('NumDataSources', U32), ('DataSources', HANDLE * 1)]


# A lower bound, NOT sizeof(PACKETMONITOR_DATA_SOURCE_SPECIFICATION): its
# documented fields include Kind, Name[64], Description[at least one WCHAR],
# four UINT32 values, and a 16-byte Detail union aligned like IPAddress. The
# unknown description capacity is deliberately not reconstructed or decoded.
SOURCE_SPEC_ALIGNMENT = ct.alignment(IPAddress)
MIN_SOURCE_SPEC_BYTES = 168


EXPORTS = (
    'PacketMonitorInitialize', 'PacketMonitorUninitialize',
    'PacketMonitorCreateLiveSession', 'PacketMonitorCloseSessionHandle',
    'PacketMonitorAddCaptureConstraint', 'PacketMonitorCreateRealtimeStream',
    'PacketMonitorCloseRealtimeStream', 'PacketMonitorAttachOutputToSession',
    'PacketMonitorSetSessionActive',
    'PacketMonitorEnumDataSources', 'PacketMonitorAddSingleDataSourceToSession',
)


def _abi_valid():
    return (ct.sizeof(HANDLE) == 8 and ct.sizeof(ProtocolConstraint) == 216
            and ProtocolConstraint.IsPresentValue.offset == 128
            and ProtocolConstraint.Ip1.offset == 152 and ProtocolConstraint.Ip2.offset == 168
            and ProtocolConstraint.Port1.offset == 186 and ProtocolConstraint.Port2.offset == 188
            and ct.sizeof(StreamMetadata) == 48 and StreamMetadata.TimeStamp.offset == 40
            and ct.sizeof(DataDescriptor) == 32 and ct.sizeof(StreamConfiguration) == 32
            and ct.sizeof(StreamEventInfo) == 16 and ct.sizeof(ct.c_size_t) == 8
            and DataSourceList.DataSources.offset == 8)


def _system_directory():
    kernel = ct.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
    fn = kernel.GetSystemDirectoryW
    fn.argtypes, fn.restype = [ct.c_wchar_p, U32], U32
    buf = ct.create_unicode_buffer(32768)
    count = fn(buf, len(buf))
    if not 0 < count < len(buf):
        raise OSError('Não foi possível localizar a pasta System32 do Windows.')
    return Path(buf.value)


def _load_system_dll():
    if os.name != 'nt':
        raise OSError('Captura nativa disponível somente no Windows.')
    path = _system_directory() / 'PktMonApi.dll'
    # Absolute OS-provided path, with dependencies restricted to System32.
    return ct.WinDLL(str(path), use_last_error=True, winmode=0x800), path


def _file_version(path):
    version = ct.WinDLL('version.dll', use_last_error=True, winmode=0x800)
    size_fn = version.GetFileVersionInfoSizeW
    size_fn.argtypes, size_fn.restype = [ct.c_wchar_p, ct.POINTER(U32)], U32
    read_fn = version.GetFileVersionInfoW
    read_fn.argtypes, read_fn.restype = [ct.c_wchar_p, U32, U32, HANDLE], ct.c_int32
    query = version.VerQueryValueW
    query.argtypes, query.restype = [HANDLE, ct.c_wchar_p, ct.POINTER(HANDLE), ct.POINTER(U32)], ct.c_int32
    ignored = U32()
    size = size_fn(str(path), ct.byref(ignored))
    if not 0 < size <= 1024 * 1024:
        return None
    buf = ct.create_string_buffer(size)
    if not read_fn(str(path), 0, size, buf):
        return None
    value, length = HANDLE(), U32()
    if not query(buf, '\\', ct.byref(value), ct.byref(length)) or length.value < 52:
        return None
    fixed = (U32 * 13).from_buffer_copy(ct.string_at(value, 52))
    if fixed[0] != 0xFEEF04BD:
        return None
    return '.'.join(str(n) for n in (fixed[2] >> 16, fixed[2] & 65535,
                                   fixed[3] >> 16, fixed[3] & 65535))


def probe():
    """Read DLL exports/version and token membership; never initialize Pktmon."""
    result = dict(backend='windows-pktmon', available=False, ready=False, admin=False,
                  elevation_required=True, api_version=API_VERSION, dll_path=None,
                  dll_version=None, missing_exports=[], pointer_bits=ct.sizeof(HANDLE) * 8,
                  abi_valid=_abi_valid(), client_minimum_build=None,
                  client_minimum_note='Microsoft não especifica o cliente mínimo nesta API.',
                  max_packet_bytes=MAX_PACKET_BYTES, live_validated=False,
                  experimental=True, error=None)
    if os.name != 'nt':
        result['error'] = 'Captura nativa disponível somente no Windows.'
        result.update(administrator=False, reason=result['error'])
        return result
    result['windows_build'] = sys.getwindowsversion().build
    try:
        shell = ct.WinDLL('shell32.dll', use_last_error=True, winmode=0x800)
        admin = shell.IsUserAnAdmin
        admin.argtypes, admin.restype = [], ct.c_int32
        result['admin'] = bool(admin())
        result['elevation_required'] = not result['admin']
        dll, path = _load_system_dll()
        result['dll_path'] = str(path)
        result['dll_version'] = _file_version(path)
        result['missing_exports'] = [name for name in EXPORTS if not hasattr(dll, name)]
        result['available'] = not result['missing_exports'] and result['abi_valid']
        result['ready'] = result['available'] and result['admin']
        if result['missing_exports']:
            result['error'] = 'Este Windows não fornece todos os exports da API de captura nativa.'
        elif not result['abi_valid']:
            result['error'] = 'ABI da captura nativa não suportada; é necessário Python Windows de 64 bits.'
        elif not result['admin']:
            result['error'] = 'A captura nativa requer administrador; nenhuma elevação foi solicitada.'
    except (OSError, AttributeError) as exc:
        result['error'] = 'API de captura nativa indisponível: ' + str(exc)
    result.update(administrator=result['admin'], reason=result['error'])
    return result


def _check_hresult(value, operation):
    code = int(value) & 0xFFFFFFFF
    if code & 0x80000000:
        suffix = ' Acesso negado; a API requer administrador.' if code == 0x80070005 else ''
        raise NativeCaptureError(f'{operation}: HRESULT 0x{code:08X}.{suffix}')


def _source_pointers(buffer, size):
    """Validate the entire pointer table before passing any opaque spec back.

    The list is a UINT32 count followed by a pointer-aligned variable array.
    Only the first UINT32 of each returned specification is counted numerically.
    It never selects/rejects sources or alters their specifications.
    Reject incomplete known prefixes/tails using the documented minimum size;
    the full sizeof remains unknown and is entrusted to the Windows-owned list.
    Undocumented string capacities and the remaining layout are never guessed.
    """
    offset = DataSourceList.DataSources.offset
    if not offset <= size <= min(ct.sizeof(buffer), MAX_SOURCE_BUFFER_BYTES):
        raise NativeCaptureError('Lista de fontes nativas truncada ou fora do limite.')
    count = U32.from_buffer_copy(buffer.raw[:ct.sizeof(U32)]).value
    if not 1 <= count <= MAX_DATA_SOURCES:
        raise NativeCaptureError('Lista de fontes nativas vazia ou excede 2048 fontes.')
    table_end = offset + count * ct.sizeof(HANDLE)
    if table_end > size:
        raise NativeCaptureError('Tabela de ponteiros das fontes nativas truncada.')
    base = ct.addressof(buffer)
    pointers = []
    seen = set()
    for index in range(count):
        start = offset + index * ct.sizeof(HANDLE)
        pointer = HANDLE.from_buffer_copy(ct.string_at(base + start, ct.sizeof(HANDLE))).value
        if (pointer is None or pointer < base + table_end
                or pointer + MIN_SOURCE_SPEC_BYTES > base + size or pointer % SOURCE_SPEC_ALIGNMENT):
            raise NativeCaptureError('Ponteiro de fonte nativa fora do buffer retornado.')
        if pointer in seen:
            continue
        prefix = U32.from_buffer_copy(ct.string_at(pointer, ct.sizeof(U32))).value
        seen.add(pointer)
        pointers.append((pointer, prefix))
    addresses = sorted(seen)
    if any(right - left < MIN_SOURCE_SPEC_BYTES for left, right in zip(addresses, addresses[1:])):
        raise NativeCaptureError('Ponteiros de fontes nativas sobrepostos no buffer retornado.')
    return pointers, count


class _WindowsAPI:
    """Only these handle-scoped entrypoints are bound; no CLI fallback."""
    def __init__(self):
        self.dll, self.path = _load_system_dll()
        self._source_buffers = {}
        self.source_selection_diagnostics = {}
        signatures = {
            'PacketMonitorInitialize': (HRESULT, [U32, HANDLE, ct.POINTER(HANDLE)]),
            'PacketMonitorUninitialize': (None, [HANDLE]),
            'PacketMonitorCreateLiveSession': (HRESULT, [HANDLE, ct.c_wchar_p, ct.POINTER(HANDLE)]),
            'PacketMonitorCloseSessionHandle': (None, [HANDLE]),
            'PacketMonitorAddCaptureConstraint': (HRESULT, [HANDLE, ct.POINTER(ProtocolConstraint)]),
            'PacketMonitorCreateRealtimeStream': (HRESULT, [HANDLE, ct.POINTER(StreamConfiguration), ct.POINTER(HANDLE)]),
            'PacketMonitorCloseRealtimeStream': (None, [HANDLE]),
            'PacketMonitorAttachOutputToSession': (HRESULT, [HANDLE, HANDLE]),
            'PacketMonitorSetSessionActive': (HRESULT, [HANDLE, U8]),
            'PacketMonitorEnumDataSources': (HRESULT, [HANDLE, ct.c_int32, U8,
                ct.c_size_t, ct.POINTER(ct.c_size_t), HANDLE]),
            'PacketMonitorAddSingleDataSourceToSession': (HRESULT, [HANDLE, HANDLE]),
        }
        for name, (restype, argtypes) in signatures.items():
            fn = getattr(self.dll, name)
            fn.restype, fn.argtypes = restype, argtypes

    def initialize(self):
        handle = HANDLE()
        _check_hresult(self.dll.PacketMonitorInitialize(API_VERSION, None, ct.byref(handle)), 'Inicializar Pktmon')
        return self._handle(handle, 'Inicializar Pktmon')

    @staticmethod
    def _handle(handle, operation):
        if not handle.value:
            raise NativeCaptureError(operation + ': handle nulo retornado pela API.')
        return handle

    def create_session(self, handle, name):
        session = HANDLE()
        _check_hresult(self.dll.PacketMonitorCreateLiveSession(handle, name, ct.byref(session)), 'Criar sessão própria')
        return self._handle(session, 'Criar sessão própria')

    def add_constraint(self, session, constraint):
        _check_hresult(self.dll.PacketMonitorAddCaptureConstraint(session, ct.byref(constraint)), 'Restringir conexão TCP')

    def select_data_sources(self, handle, session):
        """Enumerate All/non-hidden sources and select each on this session only.

        Retry a bounded number of times if the Windows component list grows.
        Retain the original storage until the owning session has been closed.
        """
        diagnostic = self.source_selection_diagnostics
        diagnostic.clear()
        diagnostic.update(source_selection_stage='enumerating-sources',
                          data_sources_count=0, data_sources_reported_count=0,
                          source_spec_prefix_u32_counts={}, source_spec_passthrough=True)
        def record_status(status):
            diagnostic['source_selection_last_hresult'] = f'0x{int(status) & 0xFFFFFFFF:08X}'
        needed = ct.c_size_t()
        status = self.dll.PacketMonitorEnumDataSources(handle, 0, False, 0, ct.byref(needed), None)
        record_status(status)
        resize_errors = (0x8007007A, 0x800700EA)  # insufficient buffer / more data
        if status != 0 and (int(status) & 0xFFFFFFFF) not in resize_errors:
            _check_hresult(status, 'Enumerar fontes nativas')
            raise NativeCaptureError('Enumeração de fontes nativas não confirmou sucesso.')
        for _ in range(SOURCE_ENUM_ATTEMPTS):
            capacity = needed.value
            if capacity == 0:
                raise NativeCaptureError('Nenhuma fonte nativa foi retornada pelo Windows.')
            if not DataSourceList.DataSources.offset <= capacity <= MAX_SOURCE_BUFFER_BYTES:
                raise NativeCaptureError('Tamanho da lista de fontes nativas fora do limite de 4 MiB.')
            buffer = ct.create_string_buffer(capacity)
            needed.value = 0
            status = self.dll.PacketMonitorEnumDataSources(handle, 0, False, capacity,
                                                           ct.byref(needed), buffer)
            record_status(status)
            if (int(status) & 0xFFFFFFFF) in resize_errors:
                if needed.value <= capacity:
                    raise NativeCaptureError('A enumeração de fontes falhou sem informar um buffer maior.')
                continue
            _check_hresult(status, 'Enumerar fontes nativas')
            if status != 0:
                raise NativeCaptureError('Enumeração de fontes nativas não confirmou sucesso.')
            diagnostic.update(source_selection_stage='validating-source-buffer',
                              data_source_buffer_bytes=needed.value)
            pointers, reported_count = _source_pointers(buffer, needed.value)
            key = session.value if isinstance(session, HANDLE) else session
            self._source_buffers[key] = buffer
            prefixes = {}
            for _, prefix in pointers:
                label = str(prefix)
                prefixes[label] = prefixes.get(label, 0) + 1
            diagnostic.update(source_selection_stage='selecting-sources',
                              data_sources_reported_count=reported_count,
                              data_sources_unique_count=len(pointers),
                              source_spec_prefix_u32_counts=prefixes)
            for index, (pointer, prefix) in enumerate(pointers, 1):
                diagnostic['source_selection_index'] = index
                status = self.dll.PacketMonitorAddSingleDataSourceToSession(session, pointer)
                record_status(status)
                _check_hresult(status, f'Selecionar fonte nativa {index}/{len(pointers)} '
                               f'(prefixo bruto {prefix}) da sessão própria')
                if status != 0:
                    raise NativeCaptureError('A API não confirmou a seleção da fonte nativa.')
                diagnostic['data_sources_count'] = index
            diagnostic['source_selection_stage'] = 'selected'
            return dict(diagnostic, source_selection='explicit-all-enumerated')
        raise NativeCaptureError('A lista de fontes nativas mudou repetidamente; reinicie a prova.')

    def create_stream(self, handle, config):
        stream = HANDLE()
        _check_hresult(self.dll.PacketMonitorCreateRealtimeStream(handle, ct.byref(config), ct.byref(stream)), 'Criar stream próprio')
        return self._handle(stream, 'Criar stream próprio')

    def attach(self, session, stream):
        _check_hresult(self.dll.PacketMonitorAttachOutputToSession(session, stream), 'Anexar stream próprio')

    def set_active(self, session, active):
        _check_hresult(self.dll.PacketMonitorSetSessionActive(session, bool(active)), 'Ativar sessão' if active else 'Parar sessão própria')

    def close_stream(self, stream):
        self.dll.PacketMonitorCloseRealtimeStream(stream)

    def close_session(self, session):
        self.dll.PacketMonitorCloseSessionHandle(session)
        key = session.value if isinstance(session, HANDLE) else session
        self._source_buffers.pop(key, None)

    def uninitialize(self, handle):
        self.dll.PacketMonitorUninitialize(handle)


def normalize_endpoint_keys(keys):
    """Canonical exact pairs only. Empty sets must never become catch-all filters."""
    normalized = set()
    if keys is None:
        raise ValueError('Informe pelo menos uma conexão TCP exata.')
    for index, key in enumerate(keys):
        if index >= MAX_ENDPOINT_PAIRS:
            raise ValueError('Limite de 32 conexões exatas por sessão nativa.')
        if not isinstance(key, (tuple, list)) or len(key) != 2:
            raise ValueError('Conexão TCP deve conter exatamente dois endpoints.')
        endpoints = []
        versions = []
        for endpoint in key:
            if not isinstance(endpoint, (tuple, list)) or len(endpoint) != 2:
                raise ValueError('Endpoint deve conter endereço IP e porta.')
            address, port = endpoint
            if not isinstance(address, str) or '%' in address:
                raise ValueError('Endereço IP fora do perfil de captura nativa.')
            ip = ipaddress.ip_address(address)
            if ip.is_unspecified or ip.is_multicast:
                raise ValueError('Endereço genérico ou multicast não é uma conexão exata.')
            if type(port) is not int or not 1 <= port <= 65535 or port in (80, 443):
                raise ValueError('Porta fora do perfil de captura do jogo.')
            versions.append(ip.version)
            endpoints.append((str(ip), port))
        if versions[0] != versions[1] or endpoints[0] == endpoints[1]:
            raise ValueError('Endpoints incompatíveis para uma conexão TCP.')
        normalized.add(tuple(sorted(endpoints)))
    if not normalized:
        raise ValueError('Captura sem conexões exatas não é permitida.')
    return tuple(sorted(normalized))


def _constraints(keys):
    for number, pair in enumerate(keys):
        for reverse in (False, True):
            first, second = pair[::-1] if reverse else pair
            ip1, ip2 = ipaddress.ip_address(first[0]), ipaddress.ip_address(second[0])
            result = ProtocolConstraint()
            name = f'ImperioTCP-{number}-{int(reverse)}'.encode('utf-16-le')
            for i in range(0, len(name), 2):
                result.Name[i // 2] = int.from_bytes(name[i:i + 2], 'little')
            # Transport, both IPs, both full prefixes, both ports, no tunnel matching.
            result.IsPresentValue = sum(1 << bit for bit in (5, 6, 7, 9, 10, 11, 12, 14))
            if ip1.version == 6:
                result.IsPresentValue |= 1 << 8
            result.TransportProtocol = 6
            result.PrefixLength1 = result.PrefixLength2 = ip1.max_prefixlen
            for field, ip in ((result.Ip1, ip1), (result.Ip2, ip2)):
                target = field.IPv6_bytes if ip.version == 6 else field.IPv4_bytes
                target[:] = ip.packed  # IP bytes are network order; scalar ports are host order.
            result.Port1, result.Port2 = first[1], second[1]
            result.EncapType = 0
            yield result


def _check_ip_truncation(data, packet_type):
    """Check declared length without changing or reassembling the packet bytes."""
    offset = 0
    if packet_type == 1:  # PktMonPayload_Ethernet
        if len(data) < 14:
            raise NativeCaptureError('Pacote Ethernet truncado na captura nativa.')
        ethertype = int.from_bytes(data[12:14], 'big')
        offset = 14
        for _ in range(2):
            if ethertype not in (0x8100, 0x88A8):
                break
            if len(data) < offset + 4:
                raise NativeCaptureError('Cabeçalho VLAN truncado na captura nativa.')
            ethertype = int.from_bytes(data[offset + 2:offset + 4], 'big')
            offset += 4
        if ethertype not in (0x0800, 0x86DD):
            return  # Unsupported payload is counted/ignored by the packet parser.
    elif packet_type != 3:  # PktMonPayload_IP
        return
    available = len(data) - offset
    if available < 1:
        raise NativeCaptureError('Cabeçalho IP ausente na captura nativa.')
    version = data[offset] >> 4
    if version == 4:
        if available < 20:
            raise NativeCaptureError('Cabeçalho IPv4 truncado na captura nativa.')
        declared = int.from_bytes(data[offset + 2:offset + 4], 'big')
    elif version == 6:
        if available < 40:
            raise NativeCaptureError('Cabeçalho IPv6 truncado na captura nativa.')
        payload = int.from_bytes(data[offset + 4:offset + 6], 'big')
        if not payload:
            raise NativeCaptureError('IPv6 sem tamanho comum; jumbograma não suportado pela captura de 9000 bytes.')
        declared = 40 + payload
    else:
        raise NativeCaptureError('Versão IP inválida na captura nativa.')
    if declared > available:
        raise NativeCaptureError(f'Pacote IP truncado: {available}/{declared} bytes. Pktmon limita a 9000 bytes; possível coalescência.')


class NativeCapture:
    """Independent exact-TCP session. Thread-safe reads; serialized lifecycle.

    start()/open(keys) acquire only this object's handles. read(timeout) returns
    immutable copied packet bytes plus metadata, or None on timeout/clean close.
    Errors always take precedence over queued packets. update() closes/recreates
    only this object; it explicitly marks a discontinuity and increments generation.
    _api is dependency injection for offline tests, never used by application code.
    """
    def __init__(self, exact_endpoint_keys=None, queue_capacity=512, *, _api=None):
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 8192:
            raise ValueError('Capacidade de fila deve estar entre 1 e 8192 pacotes.')
        self.keys = normalize_endpoint_keys(exact_endpoint_keys) if exact_endpoint_keys is not None else None
        self.queue_capacity = queue_capacity
        self._api = _api
        self._injected_api = _api is not None
        self._lifecycle = threading.RLock()
        self._condition = threading.Condition()
        self._queue = deque()
        self._driver = self._session = self._stream = None
        self._event_callback = self._data_callback = self._configuration = None
        self._active = self._accepting = self._closing = False
        self._error = None
        self._generation = 0
        self._meta = dict(backend='windows-pktmon', experimental=True, live_validated=False,
                          independent_session=True, source_selection='not-selected',
                          max_packet_bytes=MAX_PACKET_BYTES, queue_capacity=queue_capacity,
                          received=0, delivered=0, missed_write=0, missed_read=0,
                          queue_dropped=0, event_warnings=0, error=None,
                          state='idle', generation=0, lossless_handover=False)

    @property
    def meta(self):
        with self._condition:
            return dict(self._meta, queued=len(self._queue))

    def _fail(self, text, **counts):
        with self._condition:
            for key, count in counts.items():
                self._meta[key] = self._meta.get(key, 0) + count
            if self._error is None:
                self._error = NativeCaptureError(text)
                self._meta.update(error=text, state='failed')
            self._accepting = False
            self._queue.clear()
            self._condition.notify_all()

    def _on_data(self, context, pointer):
        # Never invoke close/API calls from the callback: close may join this thread.
        try:
            with self._condition:
                if not self._accepting:
                    return
            if not pointer:
                raise NativeCaptureError('Descritor de pacote nulo na captura nativa.')
            descriptor = pointer.contents
            if descriptor.MissedPacketWriteCount or descriptor.MissedPacketReadCount:
                self._fail('Pktmon informou perda de pacotes no stream; reinicie a captura.',
                           missed_write=int(descriptor.MissedPacketWriteCount),
                           missed_read=int(descriptor.MissedPacketReadCount))
                return
            size, start, length, metadata_start = map(int, (descriptor.DataSize,
                descriptor.PacketOffset, descriptor.PacketLength, descriptor.MetadataOffset))
            if (not descriptor.Data or not 1 <= size <= MAX_DESCRIPTOR_BYTES
                    or not 1 <= length <= MAX_PACKET_BYTES or start + length > size
                    or metadata_start + ct.sizeof(StreamMetadata) > size
                    or (start < metadata_start + ct.sizeof(StreamMetadata)
                        and metadata_start < start + length)):
                raise NativeCaptureError('Descritor inválido ou truncado na captura nativa.')
            metadata = StreamMetadata.from_buffer_copy(ct.string_at(
                int(descriptor.Data) + metadata_start, ct.sizeof(StreamMetadata)))
            payload = ct.string_at(int(descriptor.Data) + start, length)
            _check_ip_truncation(payload, metadata.PacketType)
            if metadata.TimeStamp <= 0:
                raise NativeCaptureError('Timestamp inválido na captura nativa.')
            record = dict(data=payload, packet_type=int(metadata.PacketType),
                          timestamp_filetime=int(metadata.TimeStamp), pkt_group_id=int(metadata.PktGroupId),
                          pkt_count=int(metadata.PktCount), appearance_count=int(metadata.AppearanceCount),
                          processor=int(metadata.Processor), component_id=int(metadata.ComponentId),
                          edge_id=int(metadata.EdgeId), direction=int(metadata.DirectionName),
                          drop_reason=int(metadata.DropReason), drop_location=int(metadata.DropLocation),
                          generation=self._generation)
            with self._condition:
                if not self._accepting:
                    return
                self._meta['received'] += 1
                if len(self._queue) >= self.queue_capacity:
                    self._fail('Fila da captura nativa cheia; houve perda de continuidade.', queue_dropped=1)
                    return
                self._queue.append(record)
                self._condition.notify()
        except Exception as exc:
            self._fail('Falha no callback de captura: ' + str(exc))

    def _on_event(self, context, pointer, kind):
        try:
            if not pointer:
                raise NativeCaptureError('Evento nulo na captura nativa.')
            info = pointer.contents
            if kind == 0:  # Started
                actual = int(info.StreamStartInfo.TruncationSize)
                if actual != MAX_PACKET_BYTES:
                    raise NativeCaptureError(f'Pktmon aplicou truncamento de {actual} bytes em vez de 9000.')
                with self._condition:
                    self._meta['ring_buffer_bytes'] = int(info.StreamStartInfo.PacketBufferSizeInBytes)
            elif kind in (1, 2):  # Stopped / FatalError
                reason = int(info.StreamStopInfo.Reason)
                if kind == 2 or info.StreamStopInfo.IsFatalError or reason or not self._closing:
                    raise NativeCaptureError(f'Stream Pktmon interrompido (evento {kind}, erro {reason}).')
            elif kind == 3:  # ProcessInfo includes oversized packet warnings.
                value = info.StreamProcessInfo
                self._fail(f'Pktmon informou aviso/erro {value.Reason}; pacote de {value.PacketLength} bytes, limite 9000.',
                           event_warnings=1)
            else:
                raise NativeCaptureError(f'Evento Pktmon desconhecido: {kind}.')
        except Exception as exc:
            self._fail('Falha no stream nativo: ' + str(exc))

    def start(self, exact_endpoint_keys=None):
        with self._lifecycle:
            keys = normalize_endpoint_keys(exact_endpoint_keys if exact_endpoint_keys is not None else self.keys)
            if self._driver is not None:
                raise NativeCaptureError('Esta sessão nativa já foi iniciada.')
            if not _abi_valid():
                raise NativeCaptureError('ABI de captura nativa incompatível.')
            capability = None
            if not self._injected_api:
                capability = probe()
                if not capability['ready']:
                    raise NativeCaptureError(capability['error'] or 'Captura nativa indisponível.')
                self._api = self._api or _WindowsAPI()
            self.keys = keys
            self._generation += 1
            with self._condition:
                self._queue.clear()
                self._error = None
                self._closing = self._accepting = False
                self._meta.update(state='starting', error=None, generation=self._generation,
                                  source_selection='not-selected', data_sources_count=0,
                                  source_selection_stage='not-selected',
                                  data_sources_reported_count=0, data_sources_unique_count=0,
                                  data_source_buffer_bytes=0,
                                  source_spec_prefix_u32_counts={}, source_spec_passthrough=True,
                                  source_selection_last_hresult=None, source_selection_index=None,
                                  endpoint_count=len(keys), constraint_count=2 * len(keys),
                                  kernel_filter='TCP + exact IP/port pairs, both directions',
                                  capability=capability, session_name='ImperioAION-' + uuid.uuid4().hex)
            try:
                diagnostic = getattr(self._api, 'source_selection_diagnostics', None)
                if isinstance(diagnostic, dict):
                    diagnostic.clear()
                self._driver = self._api.initialize()
                with _OWNERS_LOCK:
                    _OWNED_CAPTURES.add(self)
                self._session = self._api.create_session(self._driver, self._meta['session_name'])
                for constraint in _constraints(keys):
                    self._api.add_constraint(self._session, constraint)
                sources = self._api.select_data_sources(self._driver, self._session)
                if sources.get('data_sources_count', 0) <= 0:
                    raise NativeCaptureError('Nenhuma fonte nativa foi selecionada para a sessão própria.')
                with self._condition:
                    self._meta.update(sources)
                self._event_callback = EVENT_CALLBACK(self._on_event)
                self._data_callback = DATA_CALLBACK(self._on_data)
                self._configuration = StreamConfiguration(None, self._event_callback,
                    self._data_callback, 10, MAX_PACKET_BYTES)
                self._stream = self._api.create_stream(self._driver, self._configuration)
                self._api.attach(self._session, self._stream)
                with self._condition:
                    if self._error:
                        raise self._error
                    self._accepting = True
                # Mark before the call so a partially successful activation is stopped on failure.
                self._active = True
                self._api.set_active(self._session, True)
                with self._condition:
                    if self._error:
                        raise self._error
                    self._meta['state'] = 'active'
                return self
            except Exception as exc:
                # Snapshot safe numeric diagnostics before cleanup; no opaque
                # source memory or pointers enter the report, even on partial Add.
                diagnostic = getattr(self._api, 'source_selection_diagnostics', None)
                if isinstance(diagnostic, dict):
                    with self._condition:
                        self._meta.update(diagnostic)
                self._fail(str(exc))
                self.close()
                raise NativeCaptureError(str(exc)) from exc

    open = start

    def read(self, timeout=0.5):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Timeout inválido para leitura nativa.')
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._error and not self._queue and self._accepting:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            error = self._error
            if not error and self._queue:
                self._meta['delivered'] += 1
                return self._queue.popleft()
        if error:
            self.close()
            raise error
        return None

    def update(self, exact_endpoint_keys):
        keys = normalize_endpoint_keys(exact_endpoint_keys)
        with self._lifecycle:
            if self._error:
                raise self._error
            if keys == self.keys and self._active:
                return False
            self.close()
            self.start(keys)
            return True

    def close(self):
        """Stop only this object's session, then release its stream and handles."""
        with self._lifecycle:
            with self._condition:
                self._closing = True
                self._accepting = False
                self._queue.clear()
                self._condition.notify_all()
            problems = []
            if self._active and self._session is not None:
                try:
                    self._api.set_active(self._session, False)
                except Exception as exc:
                    problems.append(str(exc))
                self._active = False
            # DLL callback references survive CloseRealtimeStream, which stops callbacks.
            for attribute, method in (('_stream', 'close_stream'), ('_session', 'close_session'),
                                      ('_driver', 'uninitialize')):
                handle = getattr(self, attribute)
                if handle is not None:
                    try:
                        getattr(self._api, method)(handle)
                    except Exception as exc:
                        problems.append(str(exc))
                        # A still-open child may use its parent and callbacks.
                        # Keep all of them alive so a later close can retry.
                        break
                    else:
                        setattr(self, attribute, None)
            if self._stream is None:
                self._configuration = self._event_callback = self._data_callback = None
            if problems:
                self._fail('Falha ao encerrar os handles próprios: ' + '; '.join(problems))
            with self._condition:
                self._meta['state'] = 'failed' if self._error else 'closed'
                self._meta['cleanup_errors'] = problems
                self._meta['close_incomplete'] = any(handle is not None for handle in
                    (self._stream, self._session, self._driver))
            if self._driver is None and self._session is None and self._stream is None:
                with _OWNERS_LOCK:
                    _OWNED_CAPTURES.discard(self)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
