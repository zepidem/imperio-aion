# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_preferences as p

class Registry:
    HKEY_CURRENT_USER=1;KEY_SET_VALUE=2;REG_SZ=1
    def __init__(self):self.values={}
    def CreateKeyEx(self,*args):return contextlib.nullcontext('key')
    def SetValueEx(self,key,name,zero,kind,value):self.values[name]=value
    def DeleteValue(self,key,name):
        if name not in self.values:raise FileNotFoundError()
        del self.values[name]

class PreferencesTests(unittest.TestCase):
    def test_choices_roundtrip_startup_opt_in_and_disabled_cleanup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp);target=root/'imperioaion-update.exe';target.write_bytes(b'test');menu=root/'menu';menu.mkdir()
            registry=Registry();links=[]
            def shortcut(target,**kw):links.append(kw);(menu/(kw['label']+'.lnk')).write_bytes(b'test')
            self.assertEqual(p.load(root),dict(start_windows=False,start_menu=True))
            with patch('install_preferences.ct.windll.shell32.IsUserAnAdmin',return_value=False):
                p.apply(root,target,dict(start_windows=True,start_menu=True),registry=registry,menu=menu,shortcut=shortcut)
                self.assertIn('--windows-startup',registry.values['ImperioAION']);self.assertEqual(len(links),1)
                self.assertTrue(p.load(root)['start_windows'])
                p.apply(root,target,dict(start_windows=False,start_menu=False),registry=registry,menu=menu,shortcut=shortcut)
                self.assertFalse(registry.values);self.assertFalse(list(menu.iterdir()))
                self.assertEqual(p.load(root),dict(start_windows=False,start_menu=False))
    def test_invalid_choices_and_long_startup_command_refused(self):
        with self.assertRaises(ValueError):p.command(Path('relative/imperioaion-update.exe'))
        with self.assertRaises(ValueError):p.command(Path('C:/')/('x'*245)/'imperioaion-update.exe')
    def test_malformed_saved_options_never_enable_startup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp);(root/'install-preferences.json').write_text('{"start_windows":"yes","start_menu":true}')
            self.assertEqual(p.load(root),p.DEFAULTS)

if __name__=='__main__':unittest.main()
