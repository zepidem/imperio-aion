# SPDX-License-Identifier: GPL-3.0-only
"""Real-clock status animation with isolated state and no capture or game."""
import ctypes
import importlib
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from ui_loading import loading_motion_enabled


class LoadingAnimationTests(unittest.TestCase):
    def test_status_animation_does_not_read_decorative_windows_motion(self):
        class NoWindowsPreferenceRead:
            def __getattr__(self, name):
                raise AssertionError('Status feedback must not read decorative motion')

        with patch.object(ctypes, 'windll', NoWindowsPreferenceRead(), create=True):
            self.assertIs(loading_motion_enabled(), True)

    def test_pending_names_animate_while_damage_refreshes_every_150ms(self):
        # Return a disabled decorative-animation preference without reading or
        # changing the real Windows setting. Other Windows APIs remain intact.
        def disabled_decorative_motion(action, parameter, pointer, flags):
            self.assertEqual(action, 0x1042)
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = 0
            return 1

        windows = patch.object(ctypes.windll.user32, 'SystemParametersInfoW',
                               side_effect=disabled_decorative_motion)
        windows.start()
        self.addCleanup(windows.stop)
        fixtures = importlib.import_module('test_display_identity_ui')
        fixture = fixtures.DisplayIdentityUITests(
            'test_pending_peers_keep_separate_table_rows_and_accumulated_damage')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.pending_peers()
        app, root = fixture.app, fixture.root
        app.onboarding_skipped = True
        app.onboarding_complete = True
        # Mapped offscreen windows exercise the real Tk event loop without
        # showing an application window or pretending a hidden widget is mapped.
        root.geometry('1250x850+10000+10000')
        tk.Wm.deiconify(root)
        app.overlay.geometry('390x370+11500+10000')
        tk.Wm.deiconify(app.overlay)
        app.show_page('summary')
        fixture.refresh()
        widgets = {'hud': app.hud_view.table, 'panel': app.group_table}
        self.assertFalse(app.hud_view.welcome_dps.motion)
        for name, widget in widgets.items():
            with self.subTest(widget=name):
                self.assertTrue(widget.loading_motion)
                self.assertTrue(widget.winfo_ismapped())
                self.assertEqual(len(widget.find_withtag('name_loading')), 2)

        samples, errors = [], []
        ticks = [0]
        root.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
        original_tick = app.tick
        root.after_cancel(app.tick_after)
        app.tick_after = None

        def tick():
            ticks[0] += 1
            fixture.rows[1]['damage'] += 7
            fixture.fight['damage'] += 7
            fixture.fight['records'] += 1
            original_tick()

        app.tick = tick
        began = time.monotonic()

        def sample():
            samples.append({name: widget.loading_frame for name, widget in widgets.items()})
            if time.monotonic() - began < 2.4:
                root.after(50, sample)
            else:
                root.quit()

        root.after(1, app.tick)
        root.after(10, sample)
        watchdog = root.after(6000, root.quit)
        root.mainloop()
        root.after_cancel(watchdog)
        report = dict(seconds=time.monotonic() - began, ticks=ticks[0], errors=errors,
                      frames={name: sorted({sample[name] for sample in samples})
                              for name in widgets}, windows_preference_mocked_disabled=True,
                      settings_changed=False, capture_started=False)
        (Path(tempfile.gettempdir()) / 'loading-clock-result.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        self.assertEqual(errors, [])
        self.assertGreaterEqual(ticks[0], 8)
        for name in widgets:
            with self.subTest(widget=name):
                self.assertGreaterEqual(len(report['frames'][name]), 4)


if __name__ == '__main__':
    unittest.main()
