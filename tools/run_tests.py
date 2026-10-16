# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests, with isolated user directories; no capture or installer launch."""
import os,socket,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT/'src/imperio_aion'),str(ROOT/'src/updater'),str(ROOT/'tests/updater')]
def no_network(*args,**kwargs):raise AssertionError('Network is disabled in these tests.')
def main():
    workspace=ROOT/'work';workspace.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='offline-',dir=workspace) as tmp:
        with patch.dict(os.environ,{'LOCALAPPDATA':tmp,'APPDATA':tmp,'TEMP':tmp,'TMP':tmp}), \
             patch.object(socket.socket,'connect',no_network), \
             patch.object(socket,'create_connection',no_network):
            suites=[]
            for folder in ('tests/app','tests/updater'):
                suites.append(unittest.TestLoader().discover(str(ROOT/folder),pattern='test_*.py'))
            result=unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(suites))
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
