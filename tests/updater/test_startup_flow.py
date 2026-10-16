# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
import unittest
from startup_flow import apply_startup_view,startup_action

class StartupFlowTests(unittest.TestCase):
    def test_checks_finish_before_auto_launch(self):
        state={'phase':'up_to_date','can_launch':True}
        self.assertIsNone(startup_action(state,launch_mode=True,check_finished=False))
        self.assertEqual(startup_action(state,launch_mode=True,check_finished=True),'launch')
        self.assertIsNone(startup_action(state,launch_mode=True,check_finished=True,already_requested=True))
    def test_new_version_requires_choice_and_keeps_current_launch(self):
        state={'phase':'ready','can_launch':True}
        self.assertIsNone(startup_action(state,launch_mode=True,check_finished=True))
        view=apply_startup_view({},state,launch_mode=True)
        self.assertEqual(view['secondary_action'],'launch')
    def test_offline_or_refused_channel_never_silently_installs(self):
        for phase in ('offline','error'):
            state={'phase':phase,'can_launch':True}
            self.assertIsNone(startup_action(state,launch_mode=True,check_finished=True))
            view=apply_startup_view({},state,launch_mode=True)
            self.assertEqual(view['action'],'launch');self.assertEqual(view['secondary_action'],'check')
    def test_corrupted_or_missing_install_cannot_be_offline_fallback(self):
        for phase in ('corrupted','missing','error'):
            state={'phase':phase,'can_launch':False}
            self.assertIsNone(startup_action(state,launch_mode=True,check_finished=True))
            view=apply_startup_view({'action':'check'},state,launch_mode=True)
            self.assertEqual(view['action'],'check')
    def test_normal_installer_does_not_auto_launch(self):
        self.assertIsNone(startup_action({'phase':'up_to_date','can_launch':True},launch_mode=False,check_finished=True))

if __name__=='__main__':unittest.main(verbosity=2)
