# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded consumer of PktMon ETW packets; never starts packet capture.

Only an explicitly owned real-time ETW session is created/controlled. An outer
controller must arrange narrow PktMon capture and prevent competing captures:
subscribing to this provider does NOT itself restrict the provider to our TCP
endpoints. This consumer therefore validates exact localhost pairs before its
bounded queue. It never invokes pktmon.exe, installs/elevates, transmits network
data, changes offloads, or reads an ETL file. --check loads DLLs only.

ABI references: Microsoft EVENT_TRACE_LOGFILEW, EVENT_TRACE_PROPERTIES,
TRACE_LOGFILE_HEADER, EVENT_RECORD, PROPERTY_DATA_DESCRIPTOR, EnableTraceEx2,
TdhGetPropertySize/TdhGetProperty, ControlTraceW and CloseTrace documentation.
PktMon's locally inspected events160/170/240v0 have UInt16 packet types/sizes,
UInt64 PktGroupId, and a length-delimited binary Payload. ProcessTrace converts
timestamps to FILETIME because RAW_TIMESTAMP is intentionally not enabled.

ETW has a 64KiB event limit; accepting large decoded properties is not evidence
that live capture emits every coalesced packet. Truncation, drops, malformed
packets and queue/event loss invalidate continuity. Reports never contain bytes.
"""
from collections import deque
import argparse
import ctypes as ct
import ipaddress
import json
import math
import os
import threading
import time
import uuid

from native_packets import MAX_PACKET_BYTES, PacketParseError, parse_packet

PROVIDER_ID = '4d4f80d9-c8bd-4d73-bb5b-19c90402c5ac'
PACKET_EVENT_ID = 160
PACKET_EVENT_IDS = frozenset((160, 170, 240))
DROP_EVENT_IDS = frozenset((170, 240))
UNQUALIFIED_DROP_EVENT_IDS = frozenset((210, 230))
DRIVER_ERROR_EVENT_ID = 10
PACKET_EVENT_VERSION = 0
PACKET_KEYWORD = 0x10
ENABLED_KEYWORDS = 0x11
FILETIME_EPOCH = 116444736000000000
FLUSH_INTERVAL = .1
MAX_PAIRS = 32
ERROR_SUCCESS = 0
ERROR_ALREADY_EXISTS = 183
ERROR_CANCELLED = 1223
ERROR_WMI_INSTANCE_NOT_FOUND = 4201
ERROR_CTX_CLOSE_PENDING = 7007
INVALID_TRACE_HANDLE = 2**64 - 1
REALTIME_MODE = 0x100
NO_PROCESSOR_BUFFERING = 0x10000000
PROCESS_EVENT_RECORD = 0x10000000
WNODE_FLAG_TRACED_GUID = 0x00020000
U8, U16, U32, U64 = ct.c_uint8, ct.c_uint16, ct.c_uint32, ct.c_uint64
I32, I64, PTR = ct.c_int32, ct.c_int64, ct.c_void_p
CALLBACK = getattr(ct, 'WINFUNCTYPE', ct.CFUNCTYPE)
_OWNED_CONSUMERS = set()
_OWNERS_LOCK = threading.Lock()


class EtwError(RuntimeError):
    """Unavailable or discontinuous ETW transport; no silent fallback."""


class GUID(ct.Structure):
    _fields_ = [('Data1', U32), ('Data2', U16), ('Data3', U16), ('Data4', U8 * 8)]


def _guid(value):
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


PROVIDER = _guid(PROVIDER_ID)


class WNODE_HEADER(ct.Structure):
    _fields_ = [('BufferSize', U32), ('ProviderId', U32), ('HistoricalContext', U64),
                ('TimeStamp', I64), ('Guid', GUID), ('ClientContext', U32), ('Flags', U32)]


class EVENT_TRACE_PROPERTIES(ct.Structure):
    _fields_ = [('Wnode', WNODE_HEADER), ('BufferSize', U32), ('MinimumBuffers', U32),
                ('MaximumBuffers', U32), ('MaximumFileSize', U32), ('LogFileMode', U32),
                ('FlushTimer', U32), ('EnableFlags', U32), ('AgeLimit', I32),
                ('NumberOfBuffers', U32), ('FreeBuffers', U32), ('EventsLost', U32),
                ('BuffersWritten', U32), ('LogBuffersLost', U32), ('RealTimeBuffersLost', U32),
                ('LoggerThreadId', PTR), ('LogFileNameOffset', U32), ('LoggerNameOffset', U32)]


class EVENT_DESCRIPTOR(ct.Structure):
    _fields_ = [('Id', U16), ('Version', U8), ('Channel', U8), ('Level', U8),
                ('Opcode', U8), ('Task', U16), ('Keyword', U64)]


class EVENT_HEADER(ct.Structure):
    _fields_ = [('Size', U16), ('HeaderType', U16), ('Flags', U16), ('EventProperty', U16),
                ('ThreadId', U32), ('ProcessId', U32), ('TimeStamp', I64),
                ('ProviderId', GUID), ('EventDescriptor', EVENT_DESCRIPTOR),
                ('ProcessorTime', U64), ('ActivityId', GUID)]


class ETW_BUFFER_CONTEXT(ct.Structure):
    _fields_ = [('ProcessorNumber', U8), ('Alignment', U8), ('LoggerId', U16)]


class EVENT_RECORD(ct.Structure):
    _fields_ = [('EventHeader', EVENT_HEADER), ('BufferContext', ETW_BUFFER_CONTEXT),
                ('ExtendedDataCount', U16), ('UserDataLength', U16),
                ('ExtendedData', PTR), ('UserData', PTR), ('UserContext', PTR)]


class EVENT_TRACE_HEADER(ct.Structure):
    _fields_ = [('Size', U16), ('FieldTypeFlags', U16), ('Version', U32),
                ('ThreadId', U32), ('ProcessId', U32), ('TimeStamp', I64),
                ('Guid', GUID), ('ProcessorTime', U64)]


class EVENT_TRACE(ct.Structure):
    _fields_ = [('Header', EVENT_TRACE_HEADER), ('InstanceId', U32), ('ParentInstanceId', U32),
                ('ParentGuid', GUID), ('MofData', PTR), ('MofLength', U32), ('ClientContext', U32)]


class SYSTEMTIME(ct.Structure):
    _fields_ = [(name, U16) for name in ('Year', 'Month', 'DayOfWeek', 'Day', 'Hour', 'Minute', 'Second', 'Milliseconds')]


class TIME_ZONE_INFORMATION(ct.Structure):
    _fields_ = [('Bias', I32), ('StandardName', U16 * 32), ('StandardDate', SYSTEMTIME),
                ('StandardBias', I32), ('DaylightName', U16 * 32), ('DaylightDate', SYSTEMTIME),
                ('DaylightBias', I32)]


class TRACE_LOGFILE_HEADER(ct.Structure):
    _fields_ = [('BufferSize', U32), ('Version', U32), ('ProviderVersion', U32),
                ('NumberOfProcessors', U32), ('EndTime', I64), ('TimerResolution', U32),
                ('MaximumFileSize', U32), ('LogFileMode', U32), ('BuffersWritten', U32),
                ('LogInstanceGuid', GUID), ('LoggerName', PTR), ('LogFileName', PTR),
                ('TimeZone', TIME_ZONE_INFORMATION), ('BootTime', I64), ('PerfFreq', I64),
                ('StartTime', I64), ('ReservedFlags', U32), ('BuffersLost', U32)]


class EVENT_TRACE_LOGFILEW(ct.Structure):
    pass


EVENT_CALLBACK = CALLBACK(None, ct.POINTER(EVENT_RECORD))
BUFFER_CALLBACK = CALLBACK(U32, ct.POINTER(EVENT_TRACE_LOGFILEW))
EVENT_TRACE_LOGFILEW._fields_ = [
    ('LogFileName', ct.c_wchar_p), ('LoggerName', ct.c_wchar_p), ('CurrentTime', I64),
    ('BuffersRead', U32), ('ProcessTraceMode', U32), ('CurrentEvent', EVENT_TRACE),
    ('LogfileHeader', TRACE_LOGFILE_HEADER), ('BufferCallback', BUFFER_CALLBACK),
    ('BufferSize', U32), ('Filled', U32), ('EventsLost', U32),
    ('EventRecordCallback', EVENT_CALLBACK), ('IsKernelTrace', U32), ('Context', PTR)]


class PROPERTY_DATA_DESCRIPTOR(ct.Structure):
    _fields_ = [('PropertyName', U64), ('ArrayIndex', U32), ('Reserved', U32)]


def abi_report():
    expected = {'WNODE_HEADER': 48, 'EVENT_TRACE_PROPERTIES': 120, 'EVENT_DESCRIPTOR': 16,
        'EVENT_HEADER': 80, 'EVENT_RECORD': 112, 'EVENT_TRACE': 88,
        'TRACE_LOGFILE_HEADER': 280, 'EVENT_TRACE_LOGFILEW': 448, 'PROPERTY_DATA_DESCRIPTOR': 16}
    actual = {name:ct.sizeof(globals()[name]) for name in expected}
    offsets = {'LogfileHeader': EVENT_TRACE_LOGFILEW.LogfileHeader.offset,
        'BufferCallback': EVENT_TRACE_LOGFILEW.BufferCallback.offset,
        'EventRecordCallback': EVENT_TRACE_LOGFILEW.EventRecordCallback.offset,
        'Context': EVENT_TRACE_LOGFILEW.Context.offset,
        'UserDataLength': EVENT_RECORD.UserDataLength.offset, 'UserData': EVENT_RECORD.UserData.offset}
    valid = ct.sizeof(PTR) == 8 and actual == expected and offsets == dict(
        LogfileHeader=120, BufferCallback=400, EventRecordCallback=424, Context=440, UserDataLength=86, UserData=96)
    return dict(pointer_bits=ct.sizeof(PTR)*8, valid=valid, sizes=actual, offsets=offsets)


class PropertiesBuffer:
    """Retains UTF-16 logger name and zeroed ETW properties together."""
    def __init__(self, name, session_guid):
        encoded = name.encode('utf-16-le') + b'\0\0'
        self.buffer = ct.create_string_buffer(ct.sizeof(EVENT_TRACE_PROPERTIES) + len(encoded))
        self.pointer = ct.cast(self.buffer, ct.POINTER(EVENT_TRACE_PROPERTIES))
        p = self.pointer.contents
        p.Wnode.BufferSize = ct.sizeof(self.buffer)
        p.Wnode.Guid = session_guid
        p.Wnode.ClientContext = 1  # QPC; ProcessTrace converts to FILETIME.
        p.Wnode.Flags = WNODE_FLAG_TRACED_GUID
        p.BufferSize = 128  # KiB; still subject to ETW's 64KiB per-event limit.
        p.MinimumBuffers = 8
        p.MaximumBuffers = 32
        p.LogFileMode = REALTIME_MODE | NO_PROCESSOR_BUFFERING
        p.FlushTimer = 1
        p.LoggerNameOffset = ct.sizeof(EVENT_TRACE_PROPERTIES)
        ct.memmove(ct.addressof(self.buffer) + p.LoggerNameOffset, encoded, len(encoded))

    @property
    def losses(self):
        p = self.pointer.contents
        return dict(events_lost=int(p.EventsLost), log_buffers_lost=int(p.LogBuffersLost),
                    realtime_buffers_lost=int(p.RealTimeBuffersLost))


class WindowsEtwApi:
    """SDK bindings. Construction loads exports but starts no ETW session."""
    def __init__(self):
        if os.name != 'nt' or not abi_report()['valid']:
            raise EtwError('A origem ETW requer Windows x64 e ABI compatível.')
        kernel = ct.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        kernel.GetSystemDirectoryW.argtypes = [ct.c_wchar_p, U32]
        kernel.GetSystemDirectoryW.restype = U32
        directory = ct.create_unicode_buffer(32768)
        size = kernel.GetSystemDirectoryW(directory, len(directory))
        if not 0 < size < len(directory):
            raise EtwError('Não foi possível localizar a pasta de sistema do Windows.')
        self.advapi = ct.WinDLL(os.path.join(directory.value, 'advapi32.dll'), use_last_error=True, winmode=0x800)
        self.tdh = ct.WinDLL(os.path.join(directory.value, 'tdh.dll'), use_last_error=True, winmode=0x800)
        definitions = [
            (self.advapi, 'StartTraceW', U32, [ct.POINTER(U64), ct.c_wchar_p, ct.POINTER(EVENT_TRACE_PROPERTIES)]),
            (self.advapi, 'EnableTraceEx2', U32, [U64, ct.POINTER(GUID), U32, U8, U64, U64, U32, PTR]),
            (self.advapi, 'OpenTraceW', U64, [ct.POINTER(EVENT_TRACE_LOGFILEW)]),
            (self.advapi, 'ProcessTrace', U32, [ct.POINTER(U64), U32, PTR, PTR]),
            (self.advapi, 'ControlTraceW', U32, [U64, ct.c_wchar_p, ct.POINTER(EVENT_TRACE_PROPERTIES), U32]),
            (self.advapi, 'CloseTrace', U32, [U64]),
            (self.tdh, 'TdhGetPropertySize', U32, [ct.POINTER(EVENT_RECORD), U32, PTR, U32, ct.POINTER(PROPERTY_DATA_DESCRIPTOR), ct.POINTER(U32)]),
            (self.tdh, 'TdhGetProperty', U32, [ct.POINTER(EVENT_RECORD), U32, PTR, U32, ct.POINTER(PROPERTY_DATA_DESCRIPTOR), U32, PTR]),
        ]
        self.exports = []
        for library, name, result, arguments in definitions:
            try:function = getattr(library, name)
            except AttributeError as exc:raise EtwError('Export ETW ausente: ' + name) from exc
            function.restype, function.argtypes = result, arguments
            setattr(self, name, function)
            self.exports.append(name)

    def start(self, name, properties):
        handle = U64()
        code = self.StartTraceW(ct.byref(handle), name, properties.pointer)
        return int(code), int(handle.value)

    def enable(self, handle, enabled):
        return int(self.EnableTraceEx2(handle, ct.byref(PROVIDER), int(bool(enabled)), 4,
                                     ENABLED_KEYWORDS, 0, 2000, None))

    def open(self, logfile):
        handle = int(self.OpenTraceW(ct.byref(logfile)))
        return handle, int(ct.get_last_error()) if handle == INVALID_TRACE_HANDLE else 0

    def process(self, handle):
        value = U64(handle)
        return int(self.ProcessTrace(ct.byref(value), 1, None, None))

    def control(self, handle, properties, code):
        # Handle only: never query, update or stop a session by a foreign name.
        return int(self.ControlTraceW(handle, None, properties.pointer, code))

    def close(self, handle):
        return int(self.CloseTrace(handle))

    def property(self, event, name):
        label = ct.create_unicode_buffer(name)
        descriptor = PROPERTY_DATA_DESCRIPTOR(ct.addressof(label), 0xffffffff, 0)
        length = U32()
        code = self.TdhGetPropertySize(event, 0, None, 1, ct.byref(descriptor), ct.byref(length))
        if code:
            raise EtwError('TdhGetPropertySize falhou em ' + name + ': ' + str(int(code)))
        if not 0 < length.value <= MAX_PACKET_BYTES:
            raise EtwError('Tamanho TDH fora do limite em ' + name + '.')
        buffer = (U8 * length.value)()
        code = self.TdhGetProperty(event, 0, None, 1, ct.byref(descriptor), length.value, buffer)
        if code:
            raise EtwError('TdhGetProperty falhou em ' + name + ': ' + str(int(code)))
        return bytes(buffer)


def probe(api_factory=None):
    """DLL/export and ABI metadata only. Never StartTrace/Enable/OpenTrace."""
    result = dict(backend='windows-pktmon-etw', available=False, experimental=True,
        capture_started=False, live_validated=False, provider=PROVIDER_ID,
        event_id=PACKET_EVENT_ID, event_version=PACKET_EVENT_VERSION,
        packet_event_ids=sorted(PACKET_EVENT_IDS), keyword=ENABLED_KEYWORDS, max_packet_bytes=MAX_PACKET_BYTES,
        etw_event_limit_bytes=65536, abi=abi_report(), error=None)
    try:
        api = (api_factory or WindowsEtwApi)()
        result['exports'] = list(api.exports)
        result['available'] = True
    except Exception as exc:
        result['error'] = str(exc)
    return result


def _pairs(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_PAIRS:
        raise ValueError('A origem ETW precisa de 1 a 32 pares TCP localhost exatos.')
    result = set()
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('Par TCP localhost inválido.')
        endpoints = []
        for endpoint in pair:
            if not isinstance(endpoint, (list, tuple)) or len(endpoint) != 2:
                raise ValueError('Endpoint TCP localhost inválido.')
            address, port = endpoint
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback or type(port) is not int or not 1 <= port <= 65535 or '%' in str(ip):
                raise ValueError('A origem ETW aceita somente endpoints localhost com portas exatas.')
            endpoints.append((str(ip), port))
        if ipaddress.ip_address(endpoints[0][0]).version != ipaddress.ip_address(endpoints[1][0]).version or endpoints[0] == endpoints[1]:
            raise ValueError('Par TCP localhost incompatível.')
        result.add(tuple(sorted(endpoints)))
    return frozenset(result)


class EtwConsumer:
    def __init__(self, accepted_pairs, *, api=None, queue_capacity=512, on_error=None):
        self.accepted_pairs = _pairs(accepted_pairs)
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 8192:
            raise ValueError('Capacidade da fila ETW fora do limite.')
        self.api = api
        self.queue_capacity = queue_capacity
        self.on_error = on_error
        self.session_name = 'ImperioAION-ETW-' + str(os.getpid()) + '-' + uuid.uuid4().hex
        self.session_guid = _guid(str(uuid.uuid4()))
        self._condition = threading.Condition(threading.RLock())
        self._control_lock = threading.Lock()
        self._queue = deque()
        self._stop = threading.Event()
        self._start_finished = threading.Event()
        self._start_finished.set()
        self._process_thread = self._flush_thread = self._cleanup_thread = None
        self._session_handle = self._trace_handle = 0
        self._provider_enabled = False
        self._event_callback_ref = EVENT_CALLBACK(self._on_event)
        self._buffer_callback_ref = BUFFER_CALLBACK(self._on_buffer)
        self._logfile = EVENT_TRACE_LOGFILEW()
        self._logfile.LoggerName = self.session_name
        self._logfile.ProcessTraceMode = REALTIME_MODE | PROCESS_EVENT_RECORD
        self._logfile.EventRecordCallback = self._event_callback_ref
        self._logfile.BufferCallback = self._buffer_callback_ref
        self._state = 'initialized'
        self._error = None
        self._cleanup_errors = []
        self._ignored_event_ids = {}
        self._counts = dict(received=0, delivered=0, packet_events=0, ignored_events=0,
            ignored_unsupported_types=0, ignored_non_tcp=0, foreign_payload_events=0,
            queue_dropped=0, parse_errors=0, tdh_errors=0, truncated_events=0,
            packet_drop_events=0, unsupported_drop_events=0, event_errors=0,
            buffer_callbacks=0, buffer_consumer_losses=0,
            events_lost=0, log_buffers_lost=0,
            realtime_buffers_lost=0, flushes=0, flush_failures=0, max_packet_bytes_observed=0)

    def _properties(self):
        return PropertiesBuffer(self.session_name, self.session_guid)

    def _fail(self, message):
        notify = False
        with self._condition:
            if self._error is None:
                self._error = message
                notify = True
            self._queue.clear()
            self._stop.set()
            self._condition.notify_all()
        if notify and self.on_error is not None:
            # Notification only: it must not call blocking close from a callback.
            try:self.on_error(message)
            except Exception:pass

    def start(self):
        with self._condition:
            if self._state != 'initialized':
                raise EtwError('Esta origem ETW já foi iniciada ou encerrada.')
            self._state = 'starting'
            self._start_finished.clear()
        with _OWNERS_LOCK:_OWNED_CONSUMERS.add(self)
        failure = None
        try:
            if not abi_report()['valid']:
                raise EtwError('ABI ETW x64 incompatível.')
            if self.api is None:self.api = WindowsEtwApi()
            code, handle = self.api.start(self.session_name, self._properties())
            if code:
                # Even ERROR_ALREADY_EXISTS owns nothing: never stop that session.
                raise EtwError('StartTraceW falhou: ' + str(code))
            if not handle:raise EtwError('StartTraceW retornou handle vazio.')
            self._session_handle = handle
            if self._stop.is_set():raise EtwError('Ativação ETW interrompida.')
            code = self.api.enable(handle, True)
            if code:raise EtwError('EnableTraceEx2 falhou: ' + str(code))
            self._provider_enabled = True
            if self._stop.is_set():raise EtwError('Ativação ETW interrompida.')
            handle, code = self.api.open(self._logfile)
            if handle == INVALID_TRACE_HANDLE:
                raise EtwError('OpenTraceW falhou: ' + str(code))
            if not handle:raise EtwError('OpenTraceW retornou handle vazio.')
            self._trace_handle = handle
            if self._stop.is_set():raise EtwError('Ativação ETW interrompida.')
            with self._condition:self._state = 'active'
            self._process_thread = threading.Thread(target=self._process, name='ImperioAION-ETW-events', daemon=True)
            self._flush_thread = threading.Thread(target=self._flush, name='ImperioAION-ETW-flush', daemon=True)
            self._process_thread.start()
            self._flush_thread.start()
        except Exception as exc:
            failure = exc
            self._fail(str(exc))
        finally:
            self._start_finished.set()
        if failure is not None:
            self.close()
            raise EtwError(str(failure)) from failure
        return self

    def _process(self):
        try:
            code = self.api.process(self._trace_handle)
            if not self._stop.is_set():
                self._fail('ProcessTrace encerrou durante a leitura: ' + str(code))
            elif code not in (ERROR_SUCCESS, ERROR_CANCELLED):
                self._fail('ProcessTrace falhou no encerramento: ' + str(code))
        except Exception as exc:
            self._fail('ProcessTrace falhou: ' + str(exc))

    def _losses(self, properties):
        lost = properties.losses
        with self._condition:
            for name, value in lost.items():self._counts[name] = max(self._counts[name], value)
        if any(lost.values()):self._fail('ETW informou perda de eventos/buffers; leitura interrompida.')

    def _flush(self):
        while not self._stop.wait(FLUSH_INTERVAL):
            try:
                with self._control_lock:
                    if self._stop.is_set() or not self._session_handle:return
                    properties = self._properties()
                    code = self.api.control(self._session_handle, properties, 3)  # FLUSH
                if code:
                    with self._condition:self._counts['flush_failures'] += 1
                    self._fail('ControlTraceW FLUSH falhou: ' + str(code))
                    return
                with self._condition:self._counts['flushes'] += 1
                self._losses(properties)
            except Exception as exc:
                with self._condition:self._counts['flush_failures'] += 1
                self._fail('Flush ETW falhou: ' + str(exc))
                return

    def _property(self, event, name, length):
        try:
            data = self.api.property(event, name)
            if not isinstance(data, bytes) or len(data) != length:
                raise EtwError('Tamanho do campo TDH incompatível: ' + name)
        except Exception as exc:
            with self._condition:self._counts['tdh_errors'] += 1
            raise EtwError(str(exc)) from exc
        return data

    def _value(self, event, name, length):
        data = self._property(event, name, length)
        return int.from_bytes(data, 'little')

    def _on_event(self, event):
        try:
            if self._stop.is_set():return
            if not event:raise EtwError('Callback ETW sem EVENT_RECORD.')
            header = event.contents.EventHeader
            with self._condition:self._counts['received'] += 1
            if bytes(header.ProviderId) != bytes(PROVIDER):
                with self._condition:self._counts['ignored_events'] += 1
                return
            event_id = int(header.EventDescriptor.Id)
            if event_id == DRIVER_ERROR_EVENT_ID:
                if header.EventDescriptor.Version != 0:
                    raise EtwError('Versão do evento de erro PktMon não validada.')
                status = self._value(event, 'Status', 4)
                raise EtwError('PktMon informou falha ao carregar o driver: ' + str(status))
            if event_id in UNQUALIFIED_DROP_EVENT_IDS:
                # These local-manifest drop events have a different schema.
                # Do not decode/guess its payload or qualify by normal copies.
                with self._condition:self._counts['unsupported_drop_events'] += 1
                raise EtwError('PktMon informou descarte em schema não qualificado; evento ' + str(event_id) + '.')
            if event_id not in PACKET_EVENT_IDS:
                with self._condition:
                    self._counts['ignored_events'] += 1
                    if event_id in self._ignored_event_ids or len(self._ignored_event_ids) < 16:
                        self._ignored_event_ids[event_id] = self._ignored_event_ids.get(event_id, 0) + 1
                return
            with self._condition:self._counts['packet_events'] += 1
            if header.EventDescriptor.Version != PACKET_EVENT_VERSION:
                raise EtwError('Versão do evento PktMon não validada.')
            kind = self._value(event, 'PacketType', 2)
            if kind not in (1, 3):
                with self._condition:self._counts['ignored_unsupported_types'] += 1
                return
            component = self._value(event, 'ComponentId', 2)
            original = self._value(event, 'OriginalPayloadSize', 2)
            logged = self._value(event, 'LoggedPayloadSize', 2)
            if not 0 < logged <= MAX_PACKET_BYTES:
                raise EtwError('Tamanho de pacote ETW fora do limite.')
            if original != logged:
                with self._condition:self._counts['truncated_events'] += 1
                raise EtwError('Pacote ETW truncado; leitura interrompida.')
            data = self._property(event, 'Payload', logged)
            with self._condition:
                self._counts['max_packet_bytes_observed'] = max(self._counts['max_packet_bytes_observed'], len(data))
            try:packet = parse_packet(data, kind)
            except PacketParseError:
                with self._condition:self._counts['parse_errors'] += 1
                raise
            if packet is None:
                with self._condition:self._counts['ignored_non_tcp'] += 1
                return
            key = tuple(sorted(((packet['source'], packet['source_port']), (packet['destination'], packet['destination_port']))))
            if key not in self.accepted_pairs:
                with self._condition:self._counts['foreign_payload_events'] += 1
                return
            dropped = self._value(event, 'DropReason', 4)
            location = self._value(event, 'DropLocation', 4)
            if dropped or event_id in DROP_EVENT_IDS:
                with self._condition:self._counts['packet_drop_events'] += 1
                raise EtwError('PktMon informou descarte no par TCP próprio; evento ' + str(event_id) + '.')
            stamp = int(header.TimeStamp)
            if not FILETIME_EPOCH < stamp < 2**63:
                raise EtwError('Timestamp ETW não está em FILETIME válido.')
            group = self._value(event, 'PktGroupId', 8)
            record = dict(data=data, packet=packet, packet_type=kind, timestamp_filetime=stamp,
                component_id=component, pkt_group_id=group, drop_reason=dropped, drop_location=location)
            with self._condition:
                if self._stop.is_set():return
                if len(self._queue) >= self.queue_capacity:
                    self._counts['queue_dropped'] += 1
                    self._fail('Fila ETW cheia; continuidade perdida.')
                    return
                self._queue.append(record)
                self._condition.notify_all()
        except Exception as exc:
            with self._condition:self._counts['event_errors'] += 1
            self._fail(str(exc))

    def _on_buffer(self, logfile):
        try:
            if not logfile:raise EtwError('Callback de buffer ETW inválido.')
            with self._condition:self._counts['buffer_callbacks'] += 1
            if logfile.contents.LogfileHeader.BuffersLost:
                with self._condition:
                    self._counts['buffer_consumer_losses'] = max(self._counts['buffer_consumer_losses'], int(logfile.contents.LogfileHeader.BuffersLost))
                self._fail('O consumidor ETW informou buffers perdidos.')
            return 0 if self._stop.is_set() else 1
        except Exception as exc:
            self._fail(str(exc))
            return 0

    def read_records(self, timeout=.1, maximum=64):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 <= timeout <= 60:
            raise ValueError('Prazo de leitura ETW fora do limite.')
        if type(maximum) is not int or not 1 <= maximum <= 8192:
            raise ValueError('Lote ETW fora do limite.')
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._queue and self._error is None and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:break
                self._condition.wait(remaining)
            if self._error:raise EtwError(self._error)
            result = [self._queue.popleft() for _ in range(min(maximum, len(self._queue)))]
            self._counts['delivered'] += len(result)
            return result

    def read(self, timeout=.1):
        result = self.read_records(timeout, maximum=1)
        return result[0] if result else None

    def _cleanup(self):
        self._start_finished.wait()  # Caller never waits unboundedly on this worker.
        with self._control_lock:
            handle = self._session_handle
            if handle:
                if self._provider_enabled:
                    try:
                        code = self.api.enable(handle, False)
                        if code not in (0, ERROR_WMI_INSTANCE_NOT_FOUND):
                            self._cleanup_errors.append('DisableTraceEx2: ' + str(code))
                        else:self._provider_enabled = False
                    except Exception as exc:self._cleanup_errors.append('DisableTraceEx2: ' + str(exc))
                try:
                    properties = self._properties()
                    code = self.api.control(handle, properties, 1)  # STOP our handle.
                    if code not in (0, ERROR_WMI_INSTANCE_NOT_FOUND):
                        self._cleanup_errors.append('ControlTraceW STOP: ' + str(code))
                    else:
                        self._session_handle = 0
                        self._provider_enabled = False
                        self._losses(properties)
                except Exception as exc:self._cleanup_errors.append('ControlTraceW STOP: ' + str(exc))
            if self._trace_handle:
                try:
                    code = self.api.close(self._trace_handle)
                    if code not in (0, ERROR_CTX_CLOSE_PENDING):
                        self._cleanup_errors.append('CloseTrace: ' + str(code))
                    else:self._trace_handle = 0
                except Exception as exc:self._cleanup_errors.append('CloseTrace: ' + str(exc))

    def close(self, timeout=2.0):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not .01 <= timeout <= 5:
            raise ValueError('Prazo de encerramento ETW fora do limite.')
        deadline = time.monotonic() + timeout
        with self._condition:
            self._stop.set()
            self._queue.clear()
            self._condition.notify_all()
            if self._state == 'initialized':
                self._state = 'closed'
                return self.report()
            self._state = 'stopping'
            if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
                self._cleanup_thread = threading.Thread(target=self._cleanup, name='ImperioAION-ETW-close', daemon=True)
                self._cleanup_thread.start()
        for worker in (self._cleanup_thread, self._process_thread, self._flush_thread):
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=max(0, deadline-time.monotonic()))
        incomplete = bool(self._session_handle or self._trace_handle or any(
            worker is not None and worker.is_alive() for worker in (self._cleanup_thread, self._process_thread, self._flush_thread)))
        with self._condition:self._state = 'close_incomplete' if incomplete else 'closed'
        if not incomplete:
            with _OWNERS_LOCK:_OWNED_CONSUMERS.discard(self)
        return self.report()

    def report(self):
        with self._condition:
            return dict(backend='windows-pktmon-etw', experimental=True, live_validated=False,
                state=self._state, independent_etw_session=True, session_name=self.session_name,
                provider=PROVIDER_ID, event_id=PACKET_EVENT_ID, accepted_pair_count=len(self.accepted_pairs),
                packet_event_ids=sorted(PACKET_EVENT_IDS), ignored_event_ids=dict(self._ignored_event_ids),
                queue_capacity=self.queue_capacity, queued=len(self._queue), error=self._error,
                session_open=bool(self._session_handle), trace_open=bool(self._trace_handle),
                close_incomplete=self._state == 'close_incomplete', cleanup_errors=list(self._cleanup_errors),
                max_packet_bytes=MAX_PACKET_BYTES, etw_event_limit_bytes=65536,
                buffers_lost=max(self._counts['buffer_consumer_losses'],
                    self._counts['log_buffers_lost'] + self._counts['realtime_buffers_lost']),
                truncated_packets=self._counts['truncated_events'],
                **self._counts)

    @property
    def stats(self):return self.report()

    @property
    def meta(self):return self.report()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Conferir capacidade ETW; não inicia captura.')
    parser.add_argument('--check', action='store_true', help='Somente DLLs/exports e ABI; padrão.')
    parser.parse_args(argv)
    result = probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['available'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
