# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Windows hotkeys: replace on the owning message thread, rollback on conflict."""
import ctypes as ct
from ctypes import wintypes as wt
import os,queue,threading
from shortcuts import DEFAULTS,validate,resolve

IDS={'panel':0x491,'hud_visibility':0x492,'hud_adjustment':0x493,'hud_collapse':0x494,'reset_combat':0x495}
UPDATE=0x8001
class PanelHotkey:
    def __init__(self,bindings=None):
        self.bindings=validate(bindings or DEFAULTS);self.events=queue.SimpleQueue();self.requests=queue.SimpleQueue()
        self.ready=threading.Event();self.registered=False;self.error_code=0;self.error_codes={}
        self.registrations={key:False for key in IDS};self.thread_id=None;self.worker=None
        if os.name=='nt':
            self.worker=threading.Thread(target=self._run,daemon=True);self.worker.start()
        else:self.ready.set()

    def _register(self,user,bindings):
        registered={};errors={};physical=set()
        for action,identifier in IDS.items():
            try:
                modifiers,key=resolve(bindings[action],user.VkKeyScanW)
                if (modifiers,key) in physical:raise ValueError('Teclas equivalentes no layout do Windows.')
                physical.add((modifiers,key));ok=bool(user.RegisterHotKey(None,identifier,modifiers,key));error=0 if ok else ct.get_last_error()
            except ValueError:ok=False;error=87
            registered[action]=ok;errors[action]=error
        return registered,errors

    def _remove(self,user,registered):
        for action,ok in registered.items():
            if ok:user.UnregisterHotKey(None,IDS[action])

    def _run(self):
        user=ct.WinDLL('user32',use_last_error=True);kernel=ct.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentThreadId.restype=wt.DWORD
        user.RegisterHotKey.argtypes=[wt.HWND,ct.c_int,wt.UINT,wt.UINT];user.RegisterHotKey.restype=wt.BOOL
        user.UnregisterHotKey.argtypes=[wt.HWND,ct.c_int]
        user.VkKeyScanW.argtypes=[wt.WCHAR];user.VkKeyScanW.restype=ct.c_short
        user.GetMessageW.argtypes=[ct.POINTER(wt.MSG),wt.HWND,wt.UINT,wt.UINT];user.GetMessageW.restype=ct.c_int
        self.thread_id=kernel.GetCurrentThreadId();self.requests=getattr(self,'requests',queue.SimpleQueue())
        self.bindings=getattr(self,'bindings',dict(DEFAULTS))
        # Create the queue before exposing ready so an immediate apply is reliable.
        if hasattr(user,'PeekMessageW'):
            user.PeekMessageW.argtypes=[ct.POINTER(wt.MSG),wt.HWND,wt.UINT,wt.UINT,wt.UINT]
            user.PeekMessageW(ct.byref(wt.MSG()),None,0,0,0)
        self.registrations,self.error_codes=self._register(user,self.bindings)
        self.registered=self.registrations['panel'];self.error_code=self.error_codes['panel'];self.ready.set()
        try:
            message=wt.MSG()
            while user.GetMessageW(ct.byref(message),None,0,0)>0:
                if message.message==0x312:
                    action=next((a for a,i in IDS.items() if i==message.wParam),None)
                    if action and self.registrations.get(action):self.events.put(action)
                elif message.message==UPDATE:
                    while not self.requests.empty():
                        request=self.requests.get()
                        with request['lock']:
                            if request.get('cancelled'):continue
                            old=dict(self.bindings);self._remove(user,self.registrations)
                            registered,errors=self._register(user,request['bindings'])
                            if all(registered.values()):
                                self.bindings=request['bindings'];self.registrations=registered;self.error_codes=errors
                                request['result']=(True,{})
                            else:
                                self._remove(user,registered)
                                self.registrations,self.error_codes=self._register(user,old)
                                request['result']=(False,{a:code for a,code in errors.items() if code})
                            self.registered=self.registrations['panel'];self.error_code=self.error_codes['panel']
                            # Queued events from the retired set must not trigger actions.
                            self.drain();request['done'].set()
        finally:self._remove(user,self.registrations)

    def replace_bindings(self,bindings):
        bindings=validate(bindings)
        if os.name!='nt' or not self.ready.wait(2) or self.worker is None or not self.worker.is_alive():return False,{'windows':0}
        request=dict(bindings=bindings,done=threading.Event(),lock=threading.Lock())
        self.requests.put(request)
        user=ct.WinDLL('user32',use_last_error=True)
        user.PostThreadMessageW.argtypes=[wt.DWORD,wt.UINT,wt.WPARAM,wt.LPARAM];user.PostThreadMessageW.restype=wt.BOOL
        if not user.PostThreadMessageW(self.thread_id,UPDATE,0,0):
            with request['lock']:request['cancelled']=True
            return False,{'windows':ct.get_last_error()}
        request['done'].wait(2)
        with request['lock']:
            if 'result' in request:return request['result']
            request['cancelled']=True
        return False,{'windows':1460}

    def drain(self):
        actions=[]
        while True:
            try:
                action=self.events.get_nowait();actions.append('panel' if action is True else action)
            except queue.Empty:return actions
    def pressed(self):
        pending=self.drain();pressed=False
        for action in pending:
            if not pressed and action=='panel':pressed=True
            else:self.events.put(action)
        return pressed
    def close(self):
        if os.name=='nt' and self.thread_id and self.worker and self.worker.is_alive():
            user=ct.WinDLL('user32',use_last_error=True)
            user.PostThreadMessageW.argtypes=[wt.DWORD,wt.UINT,wt.WPARAM,wt.LPARAM]
            user.PostThreadMessageW(self.thread_id,0x12,0,0);self.worker.join(timeout=1)
