# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Validate the launcher's Windows account before loading any application data.

The expected SID is an ephemeral launch argument, not a credential or login.
Never derives AppData paths from a SID and never reads or writes user files.
Frozen entry must catch StartupIdentityError without its normal AppData log.
"""
import ctypes as ct
from ctypes import wintypes as wt
import os
import re
import sys

OPTION = '--expected-user-sid'
SID_PATTERN = re.compile(r'S-1-([0-9]{1,15})((?:-[0-9]{1,10}){1,15})', re.ASCII)
INVALID_EXPECTATION = 'A confirmação da conta do Windows está inválida. Feche esta janela e abra novamente pelo atualizador.'
WRONG_ACCOUNT = ('O Windows usou uma conta diferente da conta que abriu o atualizador. '
                 'O Império AION foi interrompido antes de carregar ou alterar seus dados. '
                 'Abra o atualizador na conta desejada e autorize a abertura usando essa mesma conta.')


class StartupIdentityError(RuntimeError):
    """Display without a file log: even an error log could use the wrong account."""


class SIDAndAttributes(ct.Structure):
    _fields_ = [('Sid', ct.c_void_p), ('Attributes', wt.DWORD)]


class TokenUser(ct.Structure):
    _fields_ = [('User', SIDAndAttributes)]


def canonical_sid(value):
    if not isinstance(value, str) or len(value) > 184 or not value.isascii() or not SID_PATTERN.fullmatch(value):
        raise StartupIdentityError(INVALID_EXPECTATION)
    parts = value.split('-')
    authority = int(parts[2])
    subauthorities = [int(part) for part in parts[3:]]
    if authority >= 1 << 48 or any(part >= 1 << 32 for part in subauthorities):
        raise StartupIdentityError(INVALID_EXPECTATION)
    canonical = 'S-1-' + '-'.join(str(number) for number in [authority, *subauthorities])
    if value != canonical:
        raise StartupIdentityError(INVALID_EXPECTATION)
    return canonical


def expected_user_sid(argv):
    """Read exactly one hidden option without mutating the original argv."""
    if not isinstance(argv, (list, tuple)) or not all(isinstance(arg, str) for arg in argv):
        raise StartupIdentityError(INVALID_EXPECTATION)
    found = None
    seen = False
    for index, arg in enumerate(argv):
        if arg == OPTION or arg.startswith(OPTION + '='):
            if seen:
                raise StartupIdentityError(INVALID_EXPECTATION)
            seen = True
            if arg == OPTION:
                if index + 1 >= len(argv):
                    raise StartupIdentityError(INVALID_EXPECTATION)
                found = argv[index + 1]
            else:
                found = arg[len(OPTION) + 1:]
    return canonical_sid(found) if seen else None


def current_user_sid():
    """Read TokenUser from the current process, independent of environment names."""
    if os.name != 'nt':
        raise StartupIdentityError('A confirmação da conta do Windows requer Windows.')
    kernel = ct.WinDLL('kernel32', use_last_error=True, winmode=0x800)
    advapi = ct.WinDLL('advapi32', use_last_error=True, winmode=0x800)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL
    kernel.LocalFree.argtypes = [ct.c_void_p]
    kernel.LocalFree.restype = ct.c_void_p
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)]
    advapi.OpenProcessToken.restype = wt.BOOL
    advapi.GetTokenInformation.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.POINTER(wt.DWORD)]
    advapi.GetTokenInformation.restype = wt.BOOL
    advapi.IsValidSid.argtypes = [ct.c_void_p]
    advapi.IsValidSid.restype = wt.BOOL
    advapi.GetLengthSid.argtypes = [ct.c_void_p]
    advapi.GetLengthSid.restype = wt.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [ct.c_void_p, ct.POINTER(ct.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wt.BOOL
    token = wt.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ct.byref(token)):
        raise StartupIdentityError('Não foi possível confirmar a conta do Windows. Nenhum dado do aplicativo foi carregado.')
    try:
        needed = wt.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ct.byref(needed))
        if not ct.sizeof(TokenUser) <= needed.value <= 4096:
            raise StartupIdentityError('A identificação da conta do Windows retornou dados inválidos.')
        buffer = ct.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), ct.byref(needed)):
            raise StartupIdentityError('Não foi possível consultar a identificação da conta do Windows.')
        record = ct.cast(buffer, ct.POINTER(TokenUser)).contents
        sid = record.User.Sid
        begin = ct.addressof(buffer)
        if not sid or not begin <= sid <= begin + len(buffer) - 8 or not advapi.IsValidSid(sid):
            raise StartupIdentityError('A identificação da conta do Windows está inválida.')
        length = advapi.GetLengthSid(sid)
        if not 8 <= length <= 68 or sid + length > begin + len(buffer):
            raise StartupIdentityError('A identificação da conta do Windows está incompleta.')
        text = ct.c_void_p()
        if not advapi.ConvertSidToStringSidW(sid, ct.byref(text)):
            raise StartupIdentityError('Não foi possível interpretar a conta do Windows.')
        try:
            return canonical_sid(ct.wstring_at(text.value))
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def enforce_expected_user_sid(argv=None, *, token_reader=None):
    """Fail before app imports/writes on cross-account elevation or bad input.

    No expected SID keeps direct launch compatible. The native updater passes
    its own token SID when launching through UAC. Do not persist returned data.
    """
    expected = expected_user_sid(sys.argv[1:] if argv is None else argv)
    if expected is None:
        return dict(checked=False, reason='direct-launch')
    try:
        actual = canonical_sid((token_reader or current_user_sid)())
    except StartupIdentityError:
        raise
    except Exception as error:
        raise StartupIdentityError('Não foi possível confirmar a conta do Windows antes de abrir o aplicativo.') from error
    if actual != expected:
        raise StartupIdentityError(WRONG_ACCOUNT)
    return dict(checked=True, matches=True, source='windows-process-token')
