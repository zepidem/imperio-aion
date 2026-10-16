# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only IP Helper ownership discovery; no PowerShell or traffic capture.

GetExtendedTcpTable OWNER_MODULE retains the OS connection creation timestamp.
Only exact game executables in our session, with a live process handle, leave
this module. A query error is an error, never evidence that a socket vanished.
"""
import ctypes as ct
from ctypes import wintypes as wt
import ipaddress
import json
import queue
import threading
import time
from pathlib import PureWindowsPath

D=ct.c_uint32
class Row4(ct.Structure):
    _fields_=[('state',D),('local',D),('lp',D),('remote',D),('rp',D),('pid',D),('created',ct.c_int64),('module',ct.c_uint64*16)]
class Row6(ct.Structure):
    _fields_=[('local',ct.c_ubyte*16),('lscope',D),('lp',D),('remote',ct.c_ubyte*16),('rscope',D),('rp',D),('state',D),('pid',D),('created',ct.c_int64),('module',ct.c_uint64*16)]
class Pid4(ct.Structure):_fields_=Row4._fields_[:6]
class Pid6(ct.Structure):_fields_=Row6._fields_[:8]

def decode_table(raw,family,module=True):
    row_type=(Row4 if family==2 else Row6) if module else (Pid4 if family==2 else Pid6)
    class Table(ct.Structure):_fields_=[('count',D),('rows',row_type*1)]
    offset=Table.rows.offset
    if len(raw)<4:raise ValueError('Tabela TCP truncada.')
    count=int.from_bytes(raw[:4],'little')
    if count>32768 or count and offset+count*ct.sizeof(row_type)>len(raw):raise ValueError('Tabela TCP fora do limite.')
    result=[]
    for n in range(count):
        r=row_type.from_buffer_copy(raw,offset+n*ct.sizeof(row_type))
        if r.state!=5:continue
        port=lambda x:int.from_bytes(int(x).to_bytes(4,'little')[:2],'big')
        lp,rp=port(r.lp),port(r.rp)
        if not 1<=lp<=65535 or not 1<=rp<=65535 or rp in (80,443):continue
        if family==2:
            local=ipaddress.ip_address(int(r.local).to_bytes(4,'little'))
            remote=ipaddress.ip_address(int(r.remote).to_bytes(4,'little'))
        else:
            # Scoped addresses are outside capture_filter's supported profile.
            if r.lscope or r.rscope:continue
            local=ipaddress.ip_address(bytes(r.local));remote=ipaddress.ip_address(bytes(r.remote))
        item=dict(LocalAddress=str(local),LocalPort=lp,RemoteAddress=str(remote),RemotePort=rp,OwningProcess=int(r.pid))
        if getattr(r,'created',0)>0:item['ConnectionStartedFiletime']=int(r.created)
        result.append(item)
    return result

class WindowsConnections:
    def __init__(self):
        self.ip=ct.WinDLL('iphlpapi',use_last_error=True,winmode=0x800)
        self.kernel=ct.WinDLL('kernel32',use_last_error=True,winmode=0x800)
        self.ip.GetExtendedTcpTable.argtypes=[ct.c_void_p,ct.POINTER(D),wt.BOOL,D,ct.c_int,D]
        self.ip.GetExtendedTcpTable.restype=D
        k=self.kernel
        k.OpenProcess.argtypes=[D,wt.BOOL,D];k.OpenProcess.restype=wt.HANDLE
        k.CloseHandle.argtypes=[wt.HANDLE];k.CloseHandle.restype=wt.BOOL
        k.QueryFullProcessImageNameW.argtypes=[wt.HANDLE,D,wt.LPWSTR,ct.POINTER(D)];k.QueryFullProcessImageNameW.restype=wt.BOOL
        k.GetProcessTimes.argtypes=[wt.HANDLE,*([ct.POINTER(wt.FILETIME)]*4)];k.GetProcessTimes.restype=wt.BOOL
        k.GetExitCodeProcess.argtypes=[wt.HANDLE,ct.POINTER(D)];k.GetExitCodeProcess.restype=wt.BOOL
    def table(self,family,module=True):
        size=D()
        for _ in range(4):
            buf=ct.create_string_buffer(size.value) if size.value else None
            result=self.ip.GetExtendedTcpTable(buf,ct.byref(size),False,family,8 if module else 5,0)
            if result==0:return decode_table(bytes(buf)[:size.value] if buf else b'\0'*4,family,module)
            # The OS may deny module details to an unelevated read-only probe.
            # PID ownership remains mandatory. Missing creation time follows
            # the existing controller policy; never synthesize a timestamp.
            if result==5 and module:return self.table(family,module=False)
            if result!=122:raise OSError(result,'Não foi possível consultar a propriedade das conexões TCP.')
            if not 4<=size.value<=4*1024*1024:raise ValueError('Tabela TCP excessiva.')
        raise OSError('A tabela TCP mudou repetidamente durante a leitura.')
    def observe(self):
        from game_startup import game_processes,GAME_NAMES
        handles={}
        try:
            for pid in game_processes():
                handle=self.kernel.OpenProcess(0x1000,False,pid)
                if not handle:raise ct.WinError(ct.get_last_error())
                handles[pid]=(handle,None)
                name=ct.create_unicode_buffer(32768);length=D(len(name))
                created,exited,kernel,user=(wt.FILETIME() for _ in range(4))
                if not self.kernel.QueryFullProcessImageNameW(handle,0,name,ct.byref(length)) or not self.kernel.GetProcessTimes(handle,*map(ct.byref,(created,exited,kernel,user))):raise ct.WinError(ct.get_last_error())
                if PureWindowsPath(name.value).name.casefold() not in GAME_NAMES:raise ValueError('O processo do jogo mudou durante a consulta.')
                handles[pid]=(handle,(created.dwHighDateTime<<32)|created.dwLowDateTime)
            rows=[]
            if handles:
                for family in (2,23):
                    rows.extend(r for r in self.table(family) if r['OwningProcess'] in handles)
            for pid,(handle,started) in handles.items():
                code=D()
                if not started or not self.kernel.GetExitCodeProcess(handle,ct.byref(code)) or code.value!=259:raise ValueError('O processo do jogo encerrou durante a consulta.')
            for r in rows:r['ProcessStartedFiletime']=handles[r['OwningProcess']][1]
            return dict(ok=True,observed_utc_ms=int(time.time()*1000),game_count=len(handles),connections=rows)
        finally:
            for h,_ in handles.values():self.kernel.CloseHandle(h)

class NativeConnectionObserver:
    """Small stream adapter for the existing observer ownership/lifetime code."""
    def __init__(self,scan=None,interval=.2):
        self.scan=scan or WindowsConnections().observe
        self.interval=interval;self.stop=threading.Event();self.items=queue.Queue(maxsize=1)
        self.stdout=self
        self.thread=threading.Thread(target=self.run,daemon=True,name='ImperioAION-conexoes');self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:item=self.scan()
            except Exception as e:item=dict(ok=False,error=str(e),error_code=getattr(e,'winerror',None) or getattr(e,'errno',None),connections=[])
            try:self.items.put_nowait(json.dumps(item)+'\n')
            except queue.Full:
                try:self.items.get_nowait()
                except queue.Empty:pass
                try:self.items.put_nowait(json.dumps(item)+'\n')
                except queue.Full:pass
            if self.stop.wait(self.interval):break
    def __enter__(self):return self
    def __exit__(self,*args):self.terminate()
    def __iter__(self):return self
    def __next__(self):
        while not self.stop.is_set():
            try:return self.items.get(timeout=.1)
            except queue.Empty:pass
        raise StopIteration
    def poll(self):return None if self.thread.is_alive() else 0
    def terminate(self):self.stop.set()
    kill=terminate
    def wait(self,timeout=None):self.thread.join(timeout);return 0 if not self.thread.is_alive() else None
