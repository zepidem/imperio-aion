# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Locate Windows tools without PATH, the current directory or environment roots.

This module only resolves paths. It never starts a process or loads capture.
It does not authenticate writable scripts/modules passed to a trusted tool.
"""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path, PureWindowsPath


def powershell_executable(*, kernel=None, is_file=None):
    """Resolve inbox Windows PowerShell; use native System32 under WOW64.

    GetSystemDirectoryW supplies the Windows-owned root. Sysnative is the
    documented virtual alias for a WOW64 process to bypass file redirection.
    Missing APIs/files fail closed; there is no relative/PATH fallback.
    Optional dependencies support offline tests, not application settings.
    """
    if kernel is None:
        if os.name != 'nt':
            raise OSError('A descoberta de conexões requer Windows.')
        kernel = ct.WinDLL('kernel32', use_last_error=True, winmode=0x800)
    kernel.GetSystemDirectoryW.argtypes = [wt.LPWSTR, wt.UINT]
    kernel.GetSystemDirectoryW.restype = wt.UINT
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.IsWow64Process.argtypes = [wt.HANDLE, ct.POINTER(wt.BOOL)]
    kernel.IsWow64Process.restype = wt.BOOL

    buffer = ct.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError('Não foi possível localizar o diretório do sistema Windows.')
    system = PureWindowsPath(buffer.value)
    if (not system.is_absolute() or len(system.drive) != 2
            or system.drive[1] != ':' or '..' in system.parts):
        raise OSError('Diretório do sistema Windows inválido.')
    wow64 = wt.BOOL()
    if not kernel.IsWow64Process(kernel.GetCurrentProcess(), ct.byref(wow64)):
        raise OSError('Não foi possível confirmar a arquitetura do processo Windows.')
    if wow64.value:
        system = system.parent / 'Sysnative'
    executable = system / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    exists = is_file if is_file is not None else lambda path: Path(path).is_file()
    if not exists(str(executable)):
        raise FileNotFoundError('O Windows PowerShell do sistema não está disponível.')
    return str(executable)


def powershell_environment(executable):
    """Minimal child environment: no user modules, CLR profilers or PATH tools.

    executable must be the absolute path obtained from powershell_executable.
    A native 64-bit PowerShell cannot use the parent's Sysnative virtual alias,
    so its module directory is the real trusted System32 path in either case.
    """
    path = PureWindowsPath(executable)
    if (not path.is_absolute() or len(path.parts) < 6 or path.name.casefold() != 'powershell.exe'
            or path.parent.name.casefold() != 'v1.0' or path.parent.parent.name.casefold() != 'windowspowershell'
            or path.parents[2].name.casefold() not in ('system32', 'sysnative') or '..' in path.parts):
        raise OSError('Caminho do Windows PowerShell não validado.')
    windows = path.parents[3]
    system = windows / 'System32'
    return dict(SystemRoot=str(windows), WINDIR=str(windows), SystemDrive=windows.drive,
                PATH=str(system) + ';' + str(windows),
                PSModulePath=str(system / 'WindowsPowerShell' / 'v1.0' / 'Modules'),
                ComSpec=str(system / 'cmd.exe'), PATHEXT='.COM;.EXE;.BAT;.CMD',
                TEMP=str(windows / 'Temp'), TMP=str(windows / 'Temp'))
