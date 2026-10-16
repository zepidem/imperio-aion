# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Windows startup companion: observe game processes and open our monitor.

No game input, hooks, memory reads, packet injection or launcher modification.
Named Windows objects stay in the current interactive session. Closing the
monitor is respected until the game has exited and starts again.
"""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
NAMESPACE = r'Local\ImperioAionV1'
GAME_NAMES = frozenset(('aion2.exe', 'aion2-win64-shipping.exe'))


class WindowsSignals:
    def __init__(self, namespace=NAMESPACE):
        if os.name != 'nt':
            raise OSError('A integração automática requer Windows.')
        self.namespace = namespace
        self.api = ct.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateMutexW': ([ct.c_void_p, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
            'CreateEventW': ([ct.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
            'OpenEventW': ([wt.DWORD, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
            'SetEvent': ([wt.HANDLE], wt.BOOL),
            'WaitForSingleObject': ([wt.HANDLE, wt.DWORD], wt.DWORD),
            'CloseHandle': ([wt.HANDLE], wt.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = arguments, result

    def name(self, suffix):
        return self.namespace + '-' + suffix

    def claim(self, suffix):
        ct.set_last_error(0)
        handle = self.api.CreateMutexW(None, False, self.name(suffix))
        error = ct.get_last_error()
        if not handle:
            raise ct.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            self.close(handle)
            return None
        return handle

    def event(self, suffix):
        handle = self.api.CreateEventW(None, False, False, self.name(suffix))
        if not handle:
            raise ct.WinError(ct.get_last_error())
        return handle

    def signal(self, suffix):
        handle = self.api.OpenEventW(0x0002, False, self.name(suffix))
        if not handle:
            if ct.get_last_error() == 2:  # No running owner
                return False
            raise ct.WinError(ct.get_last_error())
        try:
            if not self.api.SetEvent(handle):
                raise ct.WinError(ct.get_last_error())
            return True
        finally:
            self.close(handle)

    def wait(self, handle, milliseconds=0):
        result = self.api.WaitForSingleObject(handle, milliseconds)
        if result == 0:
            return True
        if result == 258:  # WAIT_TIMEOUT
            return False
        raise ct.WinError(ct.get_last_error())

    def close(self, handle):
        if handle:
            self.api.CloseHandle(handle)


class MonitorInstance:
    """One monitor per Windows session, with commands to the existing owner."""
    def __init__(self, signals=None):
        self.signals = signals or WindowsSignals()
        self.handles = {}
        self.mutex = None
        try:
            # Create command events before claiming the instance. A secondary
            # launch can then signal a primary that is still building its UI.
            for action in ('auto-capture', 'show-panel'):
                self.handles[action] = self.signals.event(action)
            self.mutex = self.signals.claim('monitor')
        except Exception:
            self.close()
            raise

    @property
    def primary(self):
        return bool(self.mutex)

    def request(self, auto_capture=False):
        return self.signals.signal('auto-capture' if auto_capture else 'show-panel')

    def poll(self):
        if not self.primary:
            return []
        return [action for action, handle in self.handles.items() if self.signals.wait(handle)]

    def close(self):
        for handle in self.handles.values():
            self.signals.close(handle)
        self.handles.clear()
        self.signals.close(self.mutex)
        self.mutex = None


class GameLifetime:
    """Confirm game exit after successful empty scans, allowing launch handoffs."""
    def __init__(self, grace_seconds=3):
        self.grace_seconds=grace_seconds
        self.active=False
        self.absent_since=None

    def observe(self, processes, now):
        if processes is None:
            # A failed query is not evidence that the game has exited. Require
            # a new uninterrupted absence window after that failure.
            self.absent_since=None
            return False
        if processes:
            self.active=True;self.absent_since=None
            return False
        if not self.active:return False
        if self.absent_since is None:self.absent_since=now
        if now-self.absent_since<self.grace_seconds:return False
        self.active=False;self.absent_since=None
        return True


class LaunchPolicy:
    """Launch once per continuous game run; bounded retries on spawn failure."""
    def __init__(self):
        self.active = False
        self.launched = False
        self.attempts = 0
        self.next_attempt = 0
        self.lifetime=GameLifetime()

    def observe(self, processes, now):
        if self.lifetime.observe(processes,now):
            self.active = self.launched = False
            self.attempts = 0
            self.next_attempt = 0
        if not processes:return False
        self.active = True
        if self.launched or self.attempts >= 3 or now < self.next_attempt:
            return False
        self.attempts += 1
        self.next_attempt = now + 5
        return True

    def acknowledge(self):
        self.launched = True


def game_processes():
    """Exact game executable names in our Windows session, via Toolhelp."""
    from game_window import ProcessEntry
    api = ct.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    api.CreateToolhelp32Snapshot.restype = wt.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        fn = getattr(api, name)
        fn.argtypes = [wt.HANDLE, ct.POINTER(ProcessEntry)]
        fn.restype = wt.BOOL
    api.ProcessIdToSessionId.argtypes = [wt.DWORD, ct.POINTER(wt.DWORD)]
    api.ProcessIdToSessionId.restype = wt.BOOL
    api.CloseHandle.argtypes = [wt.HANDLE]
    api.CloseHandle.restype = wt.BOOL
    session = wt.DWORD()
    if not api.ProcessIdToSessionId(os.getpid(), ct.byref(session)):
        raise ct.WinError(ct.get_last_error())
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ct.c_void_p(-1).value:
        raise ct.WinError(ct.get_last_error())
    try:
        entry = ProcessEntry()
        entry.size = ct.sizeof(entry)
        found = api.Process32FirstW(handle, ct.byref(entry))
        if not found and ct.get_last_error() != 18:
            raise ct.WinError(ct.get_last_error())
        result = set()
        while found:
            if entry.exe.casefold() in GAME_NAMES:
                candidate = wt.DWORD()
                if api.ProcessIdToSessionId(entry.pid, ct.byref(candidate)) and candidate.value == session.value:
                    result.add(int(entry.pid))
            found = api.Process32NextW(handle, ct.byref(entry))
        if ct.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ct.WinError(ct.get_last_error())
        return result
    finally:
        api.CloseHandle(handle)


def launch_monitor():
    executable = Path(sys.executable)
    windowless = executable.with_name('pythonw.exe')
    if windowless.is_file():
        executable = windowless
    from app_paths import data_directory
    folder = data_directory() / 'logs'
    folder.mkdir(parents=True,exist_ok=True)
    log = folder / ('automatico-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
    with log.open('ab') as error:
        args=[str(executable),'--auto-capture'] if getattr(sys,'frozen',False) else [str(executable),str(ROOT/'app.py'),'--auto-capture']
        return subprocess.Popen(args,
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error,
            creationflags=subprocess.CREATE_NO_WINDOW)


def run_watcher(signals=None, scan=game_processes, launch=launch_monitor):
    signals = signals or WindowsSignals()
    mutex = None
    # Updating the installed version signals the previous observer to stop.
    # Wait briefly for its handle to close before claiming the same session.
    for _ in range(40):
        mutex = signals.claim('watcher')
        if mutex:
            break
        time.sleep(.25)
    if not mutex:
        return False
    stop = None
    try:
        stop = signals.event('stop-watcher')
        policy = LaunchPolicy()
        while not signals.wait(stop, 500):
            try:
                processes = scan()
            except OSError:
                processes = None
            if policy.observe(processes, time.monotonic()):
                try:
                    launch()
                except OSError:
                    continue
                policy.acknowledge()
        return True
    finally:
        signals.close(stop)
        signals.close(mutex)


def main():
    parser = argparse.ArgumentParser(description='Abrir Império AION com o AION 2')
    parser.add_argument('--stop-watcher', action='store_true')
    parser.add_argument('--write-startup-link', type=Path)
    parser.add_argument('--launcher', type=Path)
    parser.add_argument('--python-path')
    args = parser.parse_args()
    if args.write_startup_link:
        if not args.launcher or not args.launcher.is_file():
            parser.error('Informe o launcher existente para o atalho de inicialização.')
        from startup_link import write_shell_link
        target=args.launcher.parent/'Observar-Aion2.cmd'
        if not target.is_file():parser.error('Falta o iniciador Observar-Aion2.cmd no pacote.')
        command=[args.python_path] if args.python_path else []
        write_shell_link(args.write_startup_link,str(target),subprocess.list2cmdline(command),args.launcher.parent)
    elif args.stop_watcher:
        WindowsSignals().signal('stop-watcher')
    else:
        run_watcher()


if __name__ == '__main__':
    main()
