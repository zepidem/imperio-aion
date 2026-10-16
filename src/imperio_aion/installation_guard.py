# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Fail-closed policy for elevated code in a protected Program Files install.

This is an early runtime guard, not a signature verifier. The native installer
must authenticate the release before writing its protected version directory.
Python/bootloader DLLs may already be loaded before Python executes this guard.
An administrator who can replace the guard is outside its trust boundary.
"""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path, PureWindowsPath
import sys
import uuid

PRODUCT_DIRECTORY = 'ImperioAION'
PROGRAM_FILES_X64 = '6d809377-6af0-444b-8957-a3773f02200e'
TRUSTED_WRITERS = frozenset((
    'S-1-5-18', 'S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464',
))
WRITE_MASK = 0x0002 | 0x0004 | 0x0010 | 0x0040 | 0x0100 | 0x10000 | 0x40000 | 0x80000 | 0x40000000 | 0x10000000
INHERIT_ONLY_ACE = 0x08
DIRECTORY = 0x10
REPARSE_POINT = 0x400
MAX_OBJECTS = 20000
_validated = set()


class InstallationSecurityError(RuntimeError):
    pass


class GUID(ct.Structure):
    _fields_ = [('Data1', wt.DWORD), ('Data2', wt.WORD), ('Data3', wt.WORD), ('Data4', ct.c_ubyte * 8)]


class ACLSize(ct.Structure):
    _fields_ = [('AceCount', wt.DWORD), ('AclBytesInUse', wt.DWORD), ('AclBytesFree', wt.DWORD)]


class ACEHeader(ct.Structure):
    _fields_ = [('AceType', wt.BYTE), ('AceFlags', wt.BYTE), ('AceSize', wt.WORD)]


class WindowsInspector:
    """Read Windows known folders, attributes and owner/DACL via system DLLs."""
    def __init__(self):
        if os.name != 'nt':
            raise InstallationSecurityError('A validação da instalação requer Windows.')
        self.kernel = ct.WinDLL('kernel32', use_last_error=True, winmode=0x800)
        self.advapi = ct.WinDLL('advapi32', use_last_error=True, winmode=0x800)
        self.shell = ct.WinDLL('shell32', use_last_error=True, winmode=0x800)
        self.ole = ct.WinDLL('ole32', use_last_error=True, winmode=0x800)
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = wt.HANDLE
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.restype = wt.BOOL
        self.kernel.LocalFree.argtypes = [ct.c_void_p]
        self.kernel.LocalFree.restype = ct.c_void_p
        self.kernel.GetFileAttributesW.argtypes = [wt.LPCWSTR]
        self.kernel.GetFileAttributesW.restype = wt.DWORD
        self.advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)]
        self.advapi.OpenProcessToken.restype = wt.BOOL
        self.advapi.GetTokenInformation.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.POINTER(wt.DWORD)]
        self.advapi.GetTokenInformation.restype = wt.BOOL
        self.shell.SHGetKnownFolderPath.argtypes = [ct.POINTER(GUID), wt.DWORD, wt.HANDLE, ct.POINTER(ct.c_void_p)]
        self.shell.SHGetKnownFolderPath.restype = ct.c_long
        self.ole.CoTaskMemFree.argtypes = [ct.c_void_p]
        self.ole.CoTaskMemFree.restype = None
        self.advapi.GetNamedSecurityInfoW.argtypes = [wt.LPWSTR, ct.c_int, wt.DWORD,
            ct.POINTER(ct.c_void_p), ct.c_void_p, ct.POINTER(ct.c_void_p), ct.c_void_p, ct.POINTER(ct.c_void_p)]
        self.advapi.GetNamedSecurityInfoW.restype = wt.DWORD
        self.advapi.ConvertSidToStringSidW.argtypes = [ct.c_void_p, ct.POINTER(ct.c_void_p)]
        self.advapi.ConvertSidToStringSidW.restype = wt.BOOL
        self.advapi.GetLengthSid.argtypes = [ct.c_void_p]
        self.advapi.GetLengthSid.restype = wt.DWORD
        self.advapi.GetAclInformation.argtypes = [ct.c_void_p, ct.c_void_p, wt.DWORD, ct.c_int]
        self.advapi.GetAclInformation.restype = wt.BOOL
        self.advapi.GetAce.argtypes = [ct.c_void_p, wt.DWORD, ct.POINTER(ct.c_void_p)]
        self.advapi.GetAce.restype = wt.BOOL

    def elevated(self):
        token = wt.HANDLE()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x8, ct.byref(token)):
            raise InstallationSecurityError('Não foi possível consultar o token de segurança do processo.')
        try:
            elevation = wt.DWORD()
            length = wt.DWORD()
            if not self.advapi.GetTokenInformation(token, 20, ct.byref(elevation), ct.sizeof(elevation), ct.byref(length)):
                raise InstallationSecurityError('Não foi possível confirmar a elevação do processo.')
            return bool(elevation.value)
        finally:
            self.kernel.CloseHandle(token)

    def program_files(self):
        identifier = GUID.from_buffer_copy(uuid.UUID(PROGRAM_FILES_X64).bytes_le)
        value = ct.c_void_p()
        result = self.shell.SHGetKnownFolderPath(ct.byref(identifier), 0, None, ct.byref(value))
        if result < 0 or not value.value:
            raise InstallationSecurityError('A pasta Program Files de 64 bits não foi localizada.')
        try:
            return ct.wstring_at(value.value)
        finally:
            self.ole.CoTaskMemFree(value)

    def attributes(self, path):
        value = self.kernel.GetFileAttributesW(str(path))
        if value == 0xffffffff:
            raise InstallationSecurityError('Não foi possível consultar os atributos: ' + str(path))
        return value

    def sid_string(self, value):
        if not value:
            raise InstallationSecurityError('Identidade de segurança ausente.')
        text = ct.c_void_p()
        if not self.advapi.ConvertSidToStringSidW(value, ct.byref(text)):
            raise InstallationSecurityError('Identidade de segurança inválida.')
        try:
            return ct.wstring_at(text.value)
        finally:
            self.kernel.LocalFree(text)

    def security(self, path):
        owner = ct.c_void_p()
        dacl = ct.c_void_p()
        descriptor = ct.c_void_p()
        status = self.advapi.GetNamedSecurityInfoW(str(path), 1, 0x1 | 0x4,
            ct.byref(owner), None, ct.byref(dacl), None, ct.byref(descriptor))
        if status:
            raise InstallationSecurityError('Não foi possível consultar as permissões: ' + str(path))
        try:
            result = dict(owner=self.sid_string(owner), dacl=None)
            if not dacl.value:
                return result
            info = ACLSize()
            if not self.advapi.GetAclInformation(dacl, ct.byref(info), ct.sizeof(info), 2) or info.AceCount > 4096:
                raise InstallationSecurityError('Lista de permissões inválida: ' + str(path))
            result['dacl'] = []
            for number in range(info.AceCount):
                pointer = ct.c_void_p()
                if not self.advapi.GetAce(dacl, number, ct.byref(pointer)):
                    raise InstallationSecurityError('Entrada de permissão ilegível: ' + str(path))
                header = ACEHeader.from_address(pointer.value)
                if header.AceSize < 4:
                    raise InstallationSecurityError('Entrada de permissão truncada: ' + str(path))
                entry = dict(type=header.AceType, flags=header.AceFlags)
                if header.AceType in (0, 1):
                    if header.AceSize < 16:
                        raise InstallationSecurityError('Entrada de permissão truncada: ' + str(path))
                    entry['mask'] = ct.c_uint32.from_address(pointer.value + 4).value
                    sid = ct.c_void_p(pointer.value + 8)
                    if 8 + self.advapi.GetLengthSid(sid) > header.AceSize:
                        raise InstallationSecurityError('Identidade de permissão truncada: ' + str(path))
                    entry['sid'] = self.sid_string(sid)
                result['dacl'].append(entry)
            return result
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)

    def children(self, path):
        with os.scandir(str(path)) as entries:
            return [entry.name for entry in entries]


def _absolute(path):
    value = PureWindowsPath(str(path))
    if (not value.is_absolute() or len(value.drive) != 2 or value.drive[1] != ':'
            or '..' in value.parts or any(':' in part for part in value.parts[1:])):
        raise InstallationSecurityError('O caminho da instalação não é um caminho local absoluto válido.')
    return value


def _check_object(inspector, path):
    attributes = inspector.attributes(path)
    if attributes & REPARSE_POINT:
        raise InstallationSecurityError('A instalação contém um link ou redirecionamento: ' + str(path))
    security = inspector.security(path)
    if security.get('owner') not in TRUSTED_WRITERS or not isinstance(security.get('dacl'), list):
        raise InstallationSecurityError('A instalação tem proprietário ou permissões desprotegidos: ' + str(path))
    for entry in security['dacl']:
        # Unknown/object/callback ACEs require an explicit future policy review.
        if entry.get('type') not in (0, 1):
            raise InstallationSecurityError('Permissão avançada não validada: ' + str(path))
        if entry.get('flags', 0) & INHERIT_ONLY_ACE or entry['type'] == 1:
            continue
        if entry.get('mask', 0) & WRITE_MASK and entry.get('sid') not in TRUSTED_WRITERS:
            raise InstallationSecurityError('Outro usuário pode alterar a instalação: ' + str(path))
    return attributes


def validate_installation(application_root, *, inspector=None):
    """Validate owners, write grants and no-reparse paths for the entire runtime.

    Only ProgramFilesX64/ImperioAION/versions/<version> is accepted. Version
    format/authenticity is the native installer's signed-manifest responsibility.
    KnownFolder itself and descendants are checked; the volume root is not.
    """
    inspector = inspector or WindowsInspector()
    root = _absolute(application_root)
    known = _absolute(inspector.program_files())
    versions = known / PRODUCT_DIRECTORY / 'versions'
    if root.parent != versions or root.name in ('', '.', '..'):
        raise InstallationSecurityError('Instale o Império AION pelo instalador em Program Files antes de executar como administrador.')
    ancestors = [known, known / PRODUCT_DIRECTORY, versions, root]
    checked = 0
    for path in ancestors:
        if not _check_object(inspector, path) & DIRECTORY:
            raise InstallationSecurityError('Uma pasta da instalação não é um diretório: ' + str(path))
        checked += 1
    pending = [root]
    while pending:
        directory = pending.pop()
        for name in inspector.children(directory):
            if not isinstance(name, str) or name in ('', '.', '..') or any(c in name for c in '\\/:\0'):
                raise InstallationSecurityError('Nome de arquivo inválido na instalação.')
            child = directory / name
            attributes = _check_object(inspector, child)
            checked += 1
            if checked > MAX_OBJECTS:
                raise InstallationSecurityError('A instalação excede o inventário permitido.')
            if attributes & DIRECTORY:
                pending.append(child)
    return dict(validated=True, root=str(root), program_files=str(known), objects_checked=checked,
                policy='protected-program-files-owner-dacl-no-reparse-v1')


def enforce_elevated_installation(application_root=None, *, inspector=None, refresh=False):
    """No-op for unelevated UI/tests; refuse unsafe elevated runtime/capture.

    Entry calls this BEFORE app/collector imports. Capture revalidates the tree.
    Cache only avoids redundant checks in the same protected elevated process.
    """
    inspector = inspector or WindowsInspector()
    if not inspector.elevated():
        return dict(validated=False, elevated=False, reason='unelevated')
    root = application_root or (Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent)
    key = str(_absolute(root)).casefold()
    if not refresh and key in _validated:
        return dict(validated=True, elevated=True, cached=True, root=str(root))
    result = validate_installation(root, inspector=inspector)
    _validated.add(key)
    return dict(result, elevated=True, cached=False)
