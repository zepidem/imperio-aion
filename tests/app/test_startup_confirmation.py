# SPDX-License-Identifier: GPL-3.0-only
"""Native startup check painting, isolated user data, no driver or game."""
import copy,os,tempfile,tkinter as tk,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import ImageTk
from app import App
from collector import Controller
from test_initial_loading import confirmed_login


class NoGame:
    def viewport(self):return None
    def own_foreground(self):return False


class StartupConfirmationTests(unittest.TestCase):
    def setUp(self):
        work=Path(__file__).resolve().parents[2]/'work';work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work);self.addCleanup(self.temp.cleanup)
        self.now=100.
        def forbidden(*args,**kwargs):raise AssertionError('No real capture in UI tests')
        for p in (patch.dict(os.environ,LOCALAPPDATA=self.temp.name),
                  patch('app.PanelHotkey',return_value=None),patch('app.game_processes',return_value=set()),
                  patch.object(Controller,'start',side_effect=forbidden),
                  patch.object(Controller,'_spawn',side_effect=forbidden),
                  patch.object(App,'follow_game',return_value=None),
                  patch('hud_view.time.monotonic',side_effect=lambda:self.now)):
            p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.root.withdraw();self.errors=[]
        self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.app=App(self.root,controller=Controller(),preview=True,game_window=NoGame())
        self.root.update();self.addCleanup(self.app.close)
        self.hud=self.app.hud_view
        self.app.overlay.geometry('390x370+30000+30000')
        self.app.overlay.deiconify();self.root.update_idletasks()
        self.state=dict(self.app.controller.snapshot(),**confirmed_login(),
            character='Example',capture_generation=83)

    def sync(self):
        self.app.snapshot=self.state
        self.hud.sync_welcome(self.state);self.root.update_idletasks()
        self.assertEqual(self.errors,[])

    def assert_green(self,count):
        for index in range(count):
            self.assertEqual(self.hud.welcome_step_keys[index],'capture_step_check')
            image=ImageTk.getimage(self.hud.welcome_card_photos[index]).convert('RGB')
            self.assertEqual(image.getpixel((image.width//2,image.height//2)),(36,67,47))

    def test_fast_login_paints_all_green_before_automatic_handoff(self):
        before=copy.deepcopy(self.state)
        self.sync()
        self.assertTrue(self.app.onboarding_complete)
        self.assertTrue(self.hud.welcome_shown)
        self.assert_green(3)
        self.now+=.6;self.sync();self.assertTrue(self.hud.welcome_shown)
        self.now+=.4;self.sync();self.assertFalse(self.hud.welcome_shown)
        self.assertEqual(self.state,before)
        self.sync();self.assertFalse(self.hud.welcome_shown)

    def test_selection_and_capture_turn_green_only_after_selection_evidence(self):
        self.state.update(login_session={},character_selection={})
        self.sync()
        self.assertEqual(self.hud.welcome_step_keys,['capture_step_1','capture_step_2','capture_step_3'])
        self.state['character_selection']=dict(visited=True,confirmed=True)
        self.sync();self.assert_green(2)
        self.assertEqual(self.hud.welcome_step_keys[2],'capture_step_3')
        self.assertFalse(self.app.login_ready)

    def test_slow_baselines_do_not_add_another_wait_after_green_confirmation(self):
        energy=self.state.pop('od_energy');self.sync()
        self.assertFalse(self.app.onboarding_complete);self.assert_green(3)
        self.now+=5;self.state['od_energy']=energy;self.sync()
        self.assertTrue(self.app.onboarding_complete)
        self.assertFalse(self.hud.welcome_shown)

    def test_skip_bypasses_visual_confirmation_and_keeps_data_unchanged(self):
        self.sync();self.assertTrue(self.hud.welcome_shown)
        before=copy.deepcopy(self.state)
        self.app.onboarding_skipped=True;self.app.hud_page.set('combat');self.sync()
        self.assertFalse(self.hud.welcome_shown)
        self.assertEqual(self.state,before)

    def test_return_to_selection_and_new_capture_reset_confirmation(self):
        self.sync();self.now+=2;self.sync();self.assertFalse(self.hud.welcome_shown)
        self.state['character_selection']=dict(visited=True,confirmed=True)
        self.sync();self.assert_green(2)
        self.assertFalse(self.app.onboarding_complete)
        self.state.update(capture_generation=84,character='Another',login_session={},character_selection={})
        self.sync();self.assertEqual(self.hud.welcome_step_keys,['capture_step_1','capture_step_2','capture_step_3'])
        self.assertIsNone(self.hud.welcome_confirmation_at)

    def test_direct_sync_paints_the_evaluated_snapshot_not_the_previous_one(self):
        self.state.update(login_session={},character_selection=dict(visited=True,confirmed=True))
        self.hud.status_snapshot={}
        self.sync();self.assertIs(self.hud.status_snapshot,self.state);self.assert_green(2)


if __name__=='__main__':unittest.main()
