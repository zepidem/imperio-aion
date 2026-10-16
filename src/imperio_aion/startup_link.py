# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Write a local Windows .lnk without requiring Windows Script Host.

Format: Microsoft's MS-SHLLINK, ShellLinkHeader / LinkInfo / StringData.
https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-shllink/747629b3-b5be-452a-8101-b9a2ec49978c
Only the package's local observer launcher is used as the startup target.
"""
import ntpath
import ctypes as ct
from ctypes import wintypes as wt
from pathlib import Path
import struct
import uuid


def _target_id_list(target):
    """Let the Windows Shell resolve its own local target, without WSH."""
    shell=ct.WinDLL('shell32');ole=ct.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[ct.c_void_p,wt.DWORD];ole.CoInitializeEx.restype=ct.c_long
    ole.CoTaskMemFree.argtypes=[ct.c_void_p];ole.CoTaskMemFree.restype=None
    ole.CoUninitialize.argtypes=[];ole.CoUninitialize.restype=None
    shell.SHParseDisplayName.argtypes=[wt.LPCWSTR,ct.c_void_p,ct.POINTER(ct.c_void_p),wt.DWORD,ct.POINTER(wt.DWORD)]
    shell.SHParseDisplayName.restype=ct.c_long
    shell.ILGetSize.argtypes=[ct.c_void_p];shell.ILGetSize.restype=wt.UINT
    initialized=ole.CoInitializeEx(None,2)
    pointer=ct.c_void_p()
    try:
        result=shell.SHParseDisplayName(target,None,ct.byref(pointer),0,None)
        if result<0:raise OSError('Não foi possível resolver o executável de inicialização: '+hex(result&0xffffffff))
        size=shell.ILGetSize(pointer)
        if not 2<=size<=65535:raise ValueError('Identificador de atalho inválido.')
        return struct.pack('<H',size)+ct.string_at(pointer,size)
    finally:
        if pointer:ole.CoTaskMemFree(pointer)
        if initialized in (0,1):ole.CoUninitialize()


def _unicode_string(text):
    if '\0' in text:
        raise ValueError('Texto de atalho inválido.')
    raw=text.encode('utf-16-le')
    if len(raw)//2>65535:
        raise ValueError('Texto de atalho muito longo.')
    return struct.pack('<H',len(raw)//2)+raw


def shell_link_bytes(target,arguments,working_directory):
    target=ntpath.normpath(str(target))
    drive,_=ntpath.splitdrive(target)
    if not ntpath.isabs(target) or len(drive)!=2 or drive[1]!=':':
        raise ValueError('O destino do iniciador precisa ser um executável local absoluto.')
    if '\0' in target or '\0' in working_directory:
        raise ValueError('Caminho de atalho inválido.')
    header=bytearray(76)
    target_id_list=_target_id_list(target)
    struct.pack_into('<I',header,0,76)
    header[4:20]=uuid.UUID('00021401-0000-0000-C000-000000000046').bytes_le
    # HasLinkTargetIDList, HasLinkInfo, HasWorkingDir, HasArguments, IsUnicode.
    struct.pack_into('<I',header,20,0xB3)
    struct.pack_into('<I',header,60,7)  # SW_SHOWMINNOACTIVE; startup never takes focus.
    volume=struct.pack('<IIII',17,3,0,16)+b'\0'
    local=target.encode('cp1252',errors='replace')+b'\0'
    local_unicode=target.encode('utf-16-le')+b'\0\0'
    volume_at=36;local_at=volume_at+len(volume)
    suffix_at=local_at+len(local)
    unicode_at=suffix_at+1
    unicode_suffix_at=unicode_at+len(local_unicode)
    info=struct.pack('<9I',unicode_suffix_at+2,36,1,volume_at,local_at,0,suffix_at,
                     unicode_at,unicode_suffix_at)+volume+local+b'\0'+local_unicode+b'\0\0'
    return bytes(header)+target_id_list+info+_unicode_string(str(working_directory))+_unicode_string(arguments)+b'\0'*4


def write_shell_link(destination,target,arguments,working_directory):
    destination=Path(destination)
    if destination.suffix.casefold()!='.lnk':
        raise ValueError('O iniciador precisa usar a extensão .lnk.')
    payload=shell_link_bytes(target,arguments,str(working_directory))
    temporary=destination.with_suffix('.lnk.tmp')
    try:
        temporary.write_bytes(payload)
        try:
            temporary.replace(destination)
        except PermissionError:
            # Some managed Startup folders allow creation but deny rename.
            # Only create a new link; never truncate a protected existing one.
            if destination.exists():raise
            with destination.open('xb') as output:
                output.write(payload)
    finally:
        if temporary.exists():temporary.unlink()
    return destination
