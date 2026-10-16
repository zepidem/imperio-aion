# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Create a current-user Windows shortcut to an authenticated installed launcher.

No elevation, shell scripts, registry writes or changes to the app data folder.
NativeBridge must authenticate/return the target before this module is called.
"""
from pathlib import Path
import ctypes as ct
from ctypes import wintypes as wt
import os,subprocess,uuid


class GUID(ct.Structure):
    _fields_=[('Data1',wt.DWORD),('Data2',wt.WORD),('Data3',wt.WORD),('Data4',ct.c_ubyte*8)]
    @classmethod
    def parse(cls,value):return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _succeeded(value):
    if value<0:raise OSError('Não foi possível criar o atalho do aplicativo (HRESULT %08x).'%(value&0xffffffff))


def create_shortcut(target,*,local_channel=None,desktop=None,label='Império AION'):
    if os.name!='nt':raise OSError('Atalho disponível apenas no Windows.')
    target=Path(target)
    if not target.is_absolute() or target.name!='imperioaion-update.exe' or not target.is_file():
        raise ValueError('O destino do atalho precisa ser o atualizador instalado e autenticado.')
    if ct.windll.shell32.IsUserAnAdmin():raise OSError('O atalho deve ser criado pela conta original, sem elevação.')
    if label not in ('Império AION','Império AION · teste'):raise ValueError('Nome de atalho não autorizado.')
    ole=ct.OleDLL('ole32');shell=ct.WinDLL('shell32',winmode=0x800)
    ole.CoInitializeEx.argtypes=[ct.c_void_p,wt.DWORD];ole.CoInitializeEx.restype=ct.c_long
    initialized=ole.CoInitializeEx(None,2)
    if initialized not in (0,1):_succeeded(initialized)
    link=ct.c_void_p();persist=ct.c_void_p()
    ole.CoCreateInstance.argtypes=[ct.POINTER(GUID),ct.c_void_p,wt.DWORD,ct.POINTER(GUID),ct.POINTER(ct.c_void_p)]
    ole.CoCreateInstance.restype=ct.c_long
    ole.CoTaskMemFree.argtypes=[ct.c_void_p];ole.CoTaskMemFree.restype=None
    ole.CoUninitialize.argtypes=[];ole.CoUninitialize.restype=None
    def call(obj,index,signature,*args):
        table=ct.cast(obj,ct.POINTER(ct.POINTER(ct.c_void_p))).contents
        return ct.WINFUNCTYPE(ct.c_long,ct.c_void_p,*signature)(table[index])(obj,*args)
    try:
        if desktop is None:
            value=ct.c_void_p();folder=GUID.parse('b4bfcc3a-db2c-424c-b029-7fe99a87c641')
            shell.SHGetKnownFolderPath.argtypes=[ct.POINTER(GUID),wt.DWORD,wt.HANDLE,ct.POINTER(ct.c_void_p)]
            shell.SHGetKnownFolderPath.restype=ct.c_long
            _succeeded(shell.SHGetKnownFolderPath(ct.byref(folder),0,None,ct.byref(value)))
            try:desktop=Path(ct.wstring_at(value.value))
            finally:ole.CoTaskMemFree(value)
        desktop=Path(desktop)
        if not desktop.is_absolute() or not desktop.is_dir() or desktop.is_symlink():raise ValueError('Pasta de atalhos inválida.')
        cls=GUID.parse('00021401-0000-0000-c000-000000000046')
        iid=GUID.parse('000214f9-0000-0000-c000-000000000046')
        _succeeded(ole.CoCreateInstance(ct.byref(cls),None,1,ct.byref(iid),ct.byref(link)))
        args=['--launch-mode']
        if local_channel is not None:
            channel=Path(local_channel).resolve()
            args.extend(['--local-channel',str(channel)])
        _succeeded(call(link,20,[wt.LPCWSTR],str(target)))
        _succeeded(call(link,11,[wt.LPCWSTR],subprocess.list2cmdline(args)))
        _succeeded(call(link,9,[wt.LPCWSTR],str(target.parent)))
        _succeeded(call(link,7,[wt.LPCWSTR],'Império AION · verificar atualizações e abrir'))
        _succeeded(call(link,17,[wt.LPCWSTR,ct.c_int],str(target),0))
        _succeeded(call(link,15,[ct.c_int],1))
        iid_file=GUID.parse('0000010b-0000-0000-c000-000000000046')
        _succeeded(call(link,0,[ct.POINTER(GUID),ct.POINTER(ct.c_void_p)],ct.byref(iid_file),ct.byref(persist)))
        destination=desktop/(label+'.lnk')
        if destination.is_symlink():raise ValueError('Atalho de destino redirecionado.')
        _succeeded(call(persist,6,[wt.LPCWSTR,wt.BOOL],str(destination),True))
        return destination
    finally:
        if persist:call(persist,2,[])
        if link:call(link,2,[])
        ole.CoUninitialize()
