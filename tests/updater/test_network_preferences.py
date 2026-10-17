# SPDX-License-Identifier: GPL-3.0-only
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import network_preferences as prefs
from startup_flow import startup_action,apply_startup_view

class NetworkPreferenceTests(unittest.TestCase):
    def test_missing_corrupt_and_non_boolean_never_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'network-preferences.json'
            self.assertFalse(prefs.load(folder))
            for text in ('broken','[]','null','{"automatic_update_checks":1}','{"automatic_update_checks":true,"extra":0}'):
                p.write_text(text);self.assertFalse(prefs.load(folder),text)
    def test_opt_in_and_opt_out_persist_without_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs.save(folder,True);self.assertTrue(prefs.load(folder))
            prefs.save(folder,False);self.assertFalse(prefs.load(folder))
            self.assertEqual(len(list(Path(folder).iterdir())),1)
    def test_offline_startup_never_calls_network_check(self):
        service=Mock();prefs.start_service(service,False)
        service.detect.assert_called_once();service.prepare_offline.assert_called_once();service.check_updates.assert_not_called()
    def test_opt_in_explicitly_checks_after_local_detection(self):
        service=Mock();prefs.start_service(service,True)
        self.assertEqual([c[0] for c in service.method_calls],['detect','check_updates'])
    def test_only_authenticated_local_install_can_auto_launch(self):
        for allowed in (False,True):
            state={'phase':'offline_ready','can_launch':allowed}
            self.assertEqual(startup_action(state,launch_mode=True,check_finished=True),'launch' if allowed else None)
            self.assertEqual(apply_startup_view({},state,launch_mode=True)['action'],'launch' if allowed else 'check')
