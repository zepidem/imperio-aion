# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Passive WinDivert 2.2 adapter for exact, verified game TCP endpoints.

SNIFF | RECV_ONLY copies packets without blocking or reinjection. No send API
is bound. Direct IPv4/IPv6 and local accelerator connections use the same
endpoint filter. Ownership is supplied and refreshed by the controller.
Official API/ABI: https://reqrypt.org/windivert-doc.html and bundled 2.2.2 header.
Driver queue loss counters are not exposed by this API: never claim lossless.
"""
from collections import deque
import ctypes as ct
import ipaddress
import os
from pathlib import Path
import threading
import time
from installation_guard import enforce_elevated_installation

from native_packets import MAX_PACKET_BYTES, parse_packet

ROOT = Path(__file__).resolve().parent
SNIFF_RECV_ONLY = 0x0001 | 0x0004
FILETIME_EPOCH = 116444736000000000
_OWNERS = set()
_OWNERS_LOCK = threading.Lock()


class CaptureError(RuntimeError):pass


class Address(ct.Structure):
    # Explicit words preserve the official header layout without ctypes bitfields.
    _fields_ = [('timestamp',ct.c_int64),('flags',ct.c_uint32),
                ('reserved',ct.c_uint32),('network',ct.c_ubyte*64)]


def abi_report():
    return dict(address_size=ct.sizeof(Address), network_offset=Address.network.offset,
                pointer_bits=ct.sizeof(ct.c_void_p)*8,
                valid=ct.sizeof(Address)==80 and Address.network.offset==16 and ct.sizeof(ct.c_void_p)==8)


def normalize_pairs(values):
    if not isinstance(values,(list,tuple)) or not 1<=len(values)<=32:
        raise ValueError('São necessários 1 a 32 pares TCP exatos.')
    pairs=set()
    for pair in values:
        if not isinstance(pair,(list,tuple)) or len(pair)!=2:raise ValueError('Par TCP inválido.')
        endpoints=[]
        for endpoint in pair:
            if not isinstance(endpoint,(list,tuple)) or len(endpoint)!=2:raise ValueError('Endpoint inválido.')
            host,port=endpoint
            ip=ipaddress.ip_address(host)
            if '%' in str(ip) or ip.is_unspecified or ip.is_multicast or type(port) is not int or not 1<=port<=65535:
                raise ValueError('Endereço ou porta fora do perfil TCP.')
            endpoints.append((str(ip),port))
        if endpoints[0]==endpoints[1] or ipaddress.ip_address(endpoints[0][0]).version!=ipaddress.ip_address(endpoints[1][0]).version:
            raise ValueError('Par TCP incompatível.')
        pairs.add(tuple(sorted(endpoints)))
    return tuple(sorted(pairs))


def exact_filter(values):
    parts=[]
    for (a,ap),(b,bp) in normalize_pairs(values):
        prefix='ip' if ipaddress.ip_address(a).version==4 else 'ipv6'
        parts.append(f'(({prefix}.SrcAddr == {a} and tcp.SrcPort == {ap} and {prefix}.DstAddr == {b} and tcp.DstPort == {bp}) or ({prefix}.SrcAddr == {b} and tcp.SrcPort == {bp} and {prefix}.DstAddr == {a} and tcp.DstPort == {ap}))')
    return 'tcp and ('+' or '.join(parts)+')'


class WindowsApi:
    def __init__(self,directory=None):
        enforce_elevated_installation()
        if os.name!='nt' or not abi_report()['valid']:raise CaptureError('WinDivert requer Windows de 64 bits.')
        self.directory=Path(directory or ROOT/'vendor/windivert').resolve()
        self.dll_path=self.directory/'WinDivert.dll'
        if not self.dll_path.is_file() or not (self.directory/'WinDivert64.sys').is_file():
            raise CaptureError('Arquivos de captura ausentes no pacote do aplicativo.')
        self.administrator=bool(ct.windll.shell32.IsUserAnAdmin())
        self.dll=ct.WinDLL(str(self.dll_path),use_last_error=True,winmode=0x100|0x800)
        self.open=self.dll.WinDivertOpen;self.open.argtypes=[ct.c_char_p,ct.c_int,ct.c_int16,ct.c_uint64];self.open.restype=ct.c_void_p
        self.recv=self.dll.WinDivertRecv;self.recv.argtypes=[ct.c_void_p,ct.c_void_p,ct.c_uint,ct.POINTER(ct.c_uint),ct.POINTER(Address)];self.recv.restype=ct.c_int
        self.shutdown=self.dll.WinDivertShutdown;self.shutdown.argtypes=[ct.c_void_p,ct.c_int];self.shutdown.restype=ct.c_int
        self.close=self.dll.WinDivertClose;self.close.argtypes=[ct.c_void_p];self.close.restype=ct.c_int
        self.set_param=self.dll.WinDivertSetParam;self.set_param.argtypes=[ct.c_void_p,ct.c_int,ct.c_uint64];self.set_param.restype=ct.c_int
        self.get_param=self.dll.WinDivertGetParam;self.get_param.argtypes=[ct.c_void_p,ct.c_int,ct.POINTER(ct.c_uint64)];self.get_param.restype=ct.c_int
        self.compile_filter=self.dll.WinDivertHelperCompileFilter
        self.compile_filter.argtypes=[ct.c_char_p,ct.c_int,ct.c_void_p,ct.c_uint,ct.POINTER(ct.c_char_p),ct.POINTER(ct.c_uint)]
        self.compile_filter.restype=ct.c_int
        self.kernel=ct.WinDLL('kernel32',use_last_error=True)
        for name in ('QueryPerformanceCounter','QueryPerformanceFrequency'):
            fn=getattr(self.kernel,name);fn.argtypes=[ct.POINTER(ct.c_int64)];fn.restype=ct.c_int
        freq=ct.c_int64();now=ct.c_int64()
        if not self.kernel.QueryPerformanceFrequency(ct.byref(freq)) or freq.value<=0:raise CaptureError('Relógio de captura indisponível.')
        self.kernel.QueryPerformanceCounter(ct.byref(now));self.frequency=freq.value
        self.anchor_qpc=now.value;self.anchor_filetime=FILETIME_EPOCH+time.time_ns()//100

    def validate_filter(self,text):
        error=ct.c_char_p();position=ct.c_uint()
        if not self.compile_filter(text.encode('ascii'),0,None,0,ct.byref(error),ct.byref(position)):
            raise CaptureError('Filtro TCP recusado na posição '+str(position.value)+': '+(error.value or b'unknown').decode('ascii','replace'))
        return True

    def start(self,filter_text):
        self.validate_filter(filter_text)
        if not self.administrator:raise CaptureError('Abra o aplicativo como administrador para iniciar a captura incluída no pacote.')
        handle=self.open(filter_text.encode('ascii'),0,0,SNIFF_RECV_ONLY)
        if handle in (None,ct.c_void_p(-1).value):
            error=ct.get_last_error()
            raise CaptureError(f'Não foi possível abrir o driver de captura (Windows {error}).')
        try:
            for parameter,value in ((0,4096),(1,2000),(2,4*1024*1024)):
                if not self.set_param(handle,parameter,value):raise CaptureError('Não foi possível configurar a fila de captura.')
            major=ct.c_uint64();minor=ct.c_uint64()
            if not self.get_param(handle,3,ct.byref(major)) or not self.get_param(handle,4,ct.byref(minor)) or (major.value,minor.value)!=(2,2):
                raise CaptureError('Versão do driver de captura incompatível com a API 2.2.')
        except Exception:self.close(handle);raise
        return handle

    def receive(self,handle):
        packet=ct.create_string_buffer(MAX_PACKET_BYTES);length=ct.c_uint();address=Address()
        if not self.recv(handle,packet,len(packet),ct.byref(length),ct.byref(address)):
            error=ct.get_last_error()
            if error==232:return None  # Shutdown(RECV) drained the handle.
            raise CaptureError(f'Leitura do driver interrompida (Windows {error}).')
        if not 0<length.value<=len(packet):raise CaptureError('Pacote truncado ou fora do limite.')
        if address.flags&0xffff or not address.flags&(1<<16):raise CaptureError('Evento fora do modo de leitura passiva.')
        stamp=self.anchor_filetime+(address.timestamp-self.anchor_qpc)*10_000_000//self.frequency
        if stamp<=FILETIME_EPOCH:raise CaptureError('Timestamp do driver inválido.')
        return dict(data=packet.raw[:length.value],timestamp_filetime=stamp,packet_type=3,
                    loopback=bool(address.flags&(1<<18)))


def probe():
    result=dict(available=False,ready=False,backend='windivert-passive',abi=abi_report(),
                driver_started=False,requires_administrator=True)
    try:
        api=WindowsApi()
        samples=[[(('127.0.0.1',51000),('127.0.0.1',55333))],
                 [(('192.0.2.1',51000),('198.51.100.2',55333))],
                 [(('2001:db8::1',51000),('2001:db8::2',55333))]]
        for sample in samples:api.validate_filter(exact_filter(sample))
        result.update(available=True,ready=api.administrator,administrator=api.administrator,filter_compiler_validated=True)
    except Exception as exc:result['error']=str(exc)
    return result


class NativeCapture:
    def __init__(self,exact_endpoint_keys,on_error=None,*,api=None,queue_capacity=512):
        self.keys=normalize_pairs(exact_endpoint_keys);self.filter_text=exact_filter(self.keys)
        if type(queue_capacity) is not int or not 1<=queue_capacity<=8192:raise ValueError('Capacidade de fila inválida.')
        self.api=api;self.on_error=on_error;self.capacity=queue_capacity
        self._cv=threading.Condition(threading.RLock());self._queue=deque();self._stop=threading.Event()
        self._start_finished=threading.Event();self._start_finished.set()
        self._close_lock=threading.Lock()
        self._handle=None;self._thread=None;self._state='initialized';self._error=None;self._close_errors=[]
        self._counts=dict(received=0,delivered=0,queue_dropped=0,foreign_packets=0)

    def _fail(self,error):
        with self._cv:
            self._error=str(error);self._stop.set();self._queue.clear();self._cv.notify_all()
        if self.on_error:
            try:self.on_error(self._error)
            except Exception:pass

    def start(self):
        with self._cv:
            if self._state!='initialized':raise CaptureError('A sessão não pode ser reutilizada.')
            self._state='starting';self._start_finished.clear()
        with _OWNERS_LOCK:_OWNERS.add(self)
        failure=None
        try:
            self.api=self.api or WindowsApi();self._handle=self.api.start(self.filter_text)
            if self._stop.is_set():raise CaptureError('Ativação da captura cancelada.')
            self._thread=threading.Thread(target=self._run,name='ImperioAION-PassiveCapture',daemon=True)
            self._state='active';self._thread.start()
        except Exception as exc:failure=exc;self._fail(exc)
        finally:self._start_finished.set()
        if failure is not None:self.close();raise failure
        return self

    def _accept(self,record):
        packet=parse_packet(record['data'],3)
        if packet is None:raise CaptureError('O filtro retornou um pacote que não é TCP.')
        key=tuple(sorted(((packet['source'],packet['source_port']),(packet['destination'],packet['destination_port']))))
        if key not in self.keys:
            self._counts['foreign_packets']+=1;raise CaptureError('O filtro retornou uma conexão não autorizada.')
        with self._cv:
            if self._stop.is_set():return
            if len(self._queue)>=self.capacity:
                self._counts['queue_dropped']+=1;raise CaptureError('Fila de captura cheia; continuidade não confirmada.')
            self._counts['received']+=1;self._queue.append(record);self._cv.notify_all()

    def _run(self):
        try:
            while not self._stop.is_set():
                record=self.api.receive(self._handle)
                if record is None:
                    if not self._stop.is_set():raise CaptureError('O driver encerrou a leitura inesperadamente.')
                    break
                self._accept(record)
        except Exception as exc:
            if not self._stop.is_set():self._fail(exc)

    def read(self,timeout=.1):
        deadline=time.monotonic()+max(0,timeout)
        with self._cv:
            while not self._queue and not self._error and not self._stop.is_set():
                remaining=deadline-time.monotonic()
                if remaining<=0:return None
                self._cv.wait(remaining)
            if self._error:raise CaptureError(self._error)
            if not self._queue:return None
            self._counts['delivered']+=1;return self._queue.popleft()

    def close(self,timeout=3):
        with self._cv:self._stop.set();self._queue.clear();self._cv.notify_all()
        if not self._start_finished.wait(timeout):self._state='close_incomplete';return self.meta
        if not self._close_lock.acquire(timeout=timeout):self._state='close_incomplete';return self.meta
        try:return self._close_handle(timeout)
        finally:self._close_lock.release()

    def _close_handle(self,timeout):
        if self._handle:
            try:
                if not self.api.shutdown(self._handle,1):raise CaptureError('Shutdown da captura não confirmado.')
            except Exception as exc:self._close_errors.append(str(exc))
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout)
        if self._thread and self._thread.is_alive():self._state='close_incomplete';return self.meta
        if self._handle:
            try:
                if not self.api.close(self._handle):raise CaptureError('Fechamento do driver não confirmado.')
                self._handle=None
            except Exception as exc:self._close_errors.append(str(exc));self._state='close_incomplete';return self.meta
        self._state='closed'
        with _OWNERS_LOCK:_OWNERS.discard(self)
        return self.meta

    @property
    def meta(self):
        with self._cv:return dict(backend='windivert-passive',state=self._state,flags=SNIFF_RECV_ONLY,
            exact_pair_count=len(self.keys),filter=self.filter_text,read_only=True,
            external_npcap_required=False,exitlag_required=False,kernel_loss_counters_available=False,
            close_incomplete=self._state=='close_incomplete',handle_open=bool(self._handle),
            error=self._error,cleanup_errors=list(self._close_errors),**self._counts)
