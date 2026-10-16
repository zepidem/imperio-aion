# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Narrow Windows/helper bridge. No personal-data reads or app code execution.

Only install() requests UAC for the authenticated native installer. launch()
delegates to its signed-inventory/SID-aware launch policy. Tests inject a fake
bridge; importing this module neither queries Windows nor touches user data.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from update_core import UpdateError
from verify_core import _plain_ancestors, _strict_json


class PermissionDeclined(UpdateError):
    pass


def non_admin():
    if os.name != "nt":
        raise UpdateError("O atualizador requer Windows.")
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise UpdateError("Abra normalmente, sem Executar como administrador. O Windows pode pedir permissão durante a instalação ou a abertura.")


def safe_environment():
    unsafe = ("COR_", "CORECLR_", "COMPLUS_", "DOTNET_", "PYTHON", "TCL", "TK_")
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith(unsafe) and key.upper() not in
            ("DEVPATH", "APPDOMAIN_MANAGER_ASM", "APPDOMAIN_MANAGER_TYPE", "PSMODULEPATH")}


@contextmanager
def clean_process_environment():
    before = dict(os.environ)
    clean = safe_environment()
    for key in before:
        if key not in clean:
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in before.items():
            if key not in clean:
                os.environ[key] = value


def default_data_root():
    """Resolve updater storage for the original, unelevated current user."""
    non_admin()
    buffer = ctypes.create_unicode_buffer(32768)
    shell = ctypes.WinDLL("shell32", use_last_error=True, winmode=0x800)
    shell.SHGetFolderPathW.argtypes = [w.HWND, ctypes.c_int, w.HANDLE, w.DWORD, w.LPWSTR]
    shell.SHGetFolderPathW.restype = ctypes.c_long
    if shell.SHGetFolderPathW(None, 28, None, 0, buffer) != 0:
        raise UpdateError("A pasta local da sua conta não está disponível.")
    return Path(buffer.value) / "ImperioBDO-Atualizador"


@contextmanager
def locked_helper(resources_root):
    non_admin()
    from helper_trust import HELPER_SHA256, HELPER_CONFIG_SHA256
    path = Path(resources_root) / "imperio-install.exe"
    config = Path(resources_root) / "imperio-install.exe.config"
    _plain_ancestors(path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    class Info(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("creation", w.FILETIME), ("access", w.FILETIME),
                    ("write", w.FILETIME), ("volume", w.DWORD), ("size_high", w.DWORD),
                    ("size_low", w.DWORD), ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(Info)]
    kernel.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    handles = []
    try:
        for item in [*reversed(path.parents), path, config]:
            is_file = item in (path, config)
            handle = kernel.CreateFileW(str(item), 0x80000000 if is_file else 0x80,
                1 if is_file else 3, None, 3, 0x00200000 | (0 if is_file else 0x02000000), None)
            if handle in (None, ctypes.c_void_p(-1).value):
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            info = Info()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & 0x400:
                raise UpdateError("O instalador contém um redirecionamento de pasta ou arquivo.")
        for handle, expected in ((handles[-2], HELPER_SHA256), (handles[-1], HELPER_CONFIG_SHA256)):
            digest = hashlib.sha256()
            buffer = ctypes.create_string_buffer(65536)
            count = w.DWORD()
            while True:
                if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not count.value:
                    break
                digest.update(buffer.raw[:count.value])
            if digest.hexdigest() != expected:
                raise UpdateError("O componente de instalação foi alterado. Extraia novamente o atualizador.")
        yield path
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


class NativeBridge:
    def __init__(self, resources_root=None, on_notice=None):
        self.resources_root = Path(resources_root or getattr(sys, "_MEIPASS", Path(__file__).parent))
        self.on_notice = on_notice or (lambda message: None)

    def _run(self, arguments):
        with locked_helper(self.resources_root) as path:
            result = subprocess.run([str(path), *map(str, arguments)], capture_output=True,
                timeout=180, creationflags=0x08000000, env=safe_environment(), cwd=path.parent)
        if len(result.stdout) + len(result.stderr) > 65536:
            raise UpdateError("Resposta do instalador excedeu o limite.")
        output = result.stdout.decode("utf-8", "replace").strip()
        if result.returncode:
            if result.returncode == 3 and arguments and arguments[0] == "--launch":
                raise PermissionDeclined("A abertura foi cancelada na janela de permissão do Windows.")
            reason = result.stderr.decode("utf-8", "replace").strip()
            raise UpdateError(reason or "O instalador recusou a operação.")
        return output

    def inspect_installed(self):
        raw = self._run(["--inspect-installed"]).encode("utf-8")
        value = _strict_json(raw, 65536, "Inspeção da instalação")
        expected = {"status", "version", "counter", "running", "running_state", "authenticated", "launch_supported", "reason"}
        if (type(value) is not dict or set(value) != expected or value["status"] not in ("missing", "installed", "corrupted")
                or type(value["running"]) is not bool or type(value["authenticated"]) is not bool
                or type(value["launch_supported"]) is not bool):
            raise UpdateError("Resposta de inspeção da instalação inválida.")
        if value["status"] == "installed" and (not value["authenticated"]
                or type(value["version"]) is not str or type(value["counter"]) is not int):
            raise UpdateError("A versão instalada não foi autenticada.")
        return value

    def verify_archive(self, archive, metadata):
        return self._run(["--verify", archive, metadata])

    def install(self, archive, metadata, *, on_started=None):
        non_admin()
        class Execute(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("mask", w.ULONG), ("hwnd", w.HWND), ("verb", w.LPCWSTR),
                        ("file", w.LPCWSTR), ("parameters", w.LPCWSTR), ("directory", w.LPCWSTR),
                        ("show", ctypes.c_int), ("instance", w.HINSTANCE), ("idlist", ctypes.c_void_p),
                        ("klass", w.LPCWSTR), ("key", w.HKEY), ("hotkey", w.DWORD),
                        ("icon", w.HANDLE), ("process", w.HANDLE)]
        with locked_helper(self.resources_root) as path:
            execute = Execute()
            execute.size = ctypes.sizeof(execute)
            execute.mask = 0x40
            execute.verb = "runas"
            execute.file = str(path)
            execute.parameters = subprocess.list2cmdline(["--install", str(archive), str(metadata)])
            execute.directory = str(path.parent)
            execute.show = 0
            shell = ctypes.WinDLL("shell32", use_last_error=True, winmode=0x800)
            shell.ShellExecuteExW.argtypes = [ctypes.POINTER(Execute)]
            with clean_process_environment():
                if not shell.ShellExecuteExW(ctypes.byref(execute)):
                    error = ctypes.get_last_error()
                    if error == 1223:
                        raise PermissionDeclined("A permissão do Windows foi cancelada. A versão anterior continua instalada.")
                    raise ctypes.WinError(error)
            if on_started is not None:
                on_started()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
            kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
            kernel.CloseHandle.argtypes = [w.HANDLE]
            waits = 0
            try:
                # Keep validation handles until child exit. A slow child is not
                # killed or falsely reported as canceled after a UI timeout.
                while True:
                    result = kernel.WaitForSingleObject(execute.process, 1000)
                    if result == 0:
                        break
                    if result != 258:
                        raise ctypes.WinError(ctypes.get_last_error())
                    waits += 1
                    if waits == 180:
                        self.on_notice("O Windows ainda está concluindo a instalação. Aguarde sem fechar esta janela.")
                code = w.DWORD()
                if not kernel.GetExitCodeProcess(execute.process, ctypes.byref(code)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if code.value:
                    raise UpdateError("A instalação foi recusada. A versão anterior foi mantida (código " + str(code.value) + ").")
            finally:
                kernel.CloseHandle(execute.process)

    def launch(self):
        return self._run(["--launch"])

    def launch_background(self):
        return self._run(["--launch-background"])

    def launcher_info(self):
        value=_strict_json(self._run(['--launcher-info']).encode('utf8'),65536,'Atualizador instalado')
        if (type(value) is not dict or set(value)!={'status','version','path'}
                or value['status']!='VERIFIED_LAUNCHER' or not isinstance(value['path'],str)):
            raise UpdateError('O atualizador instalado não foi autenticado.')
        return value
