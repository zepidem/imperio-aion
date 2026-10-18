# SPDX-License-Identifier: GPL-3.0-only
"""A deferred duty-list refresh must not hold a verified login in preparation."""
import copy,unittest
from capture_readiness import initial_loading_complete,initial_readings,initial_loading_status


def confirmed_login():
    fresh=lambda:dict(available=True,cached=False)
    return dict(mode='live',running=True,capture_ready=True,error=None,
        login_session=dict(confirmed=True,entry_confirmed=True),
        character_selection=dict(confirmed=False,visited=True),
        od_energy=fresh(),resource_gains=dict(kinah_daily=fresh(),abyss_weekly=fresh()),
        daily_duty=dict(available=True,current=0,limit=5,reset_pending=True),
        weekly_orders=dict(altgard=fresh(),latesran=fresh()),
        activity_entries={key:fresh() for key in ('daily_dungeon','shugo_party','nightmare','ascension_rite')},
        pet_collection=dict(wire_decoder_validated=True,character_scope=dict(name='Example',server=2405)))


class InitialLoadingTests(unittest.TestCase):
    def test_expired_duty_list_remains_unconfirmed_but_releases_verified_login(self):
        state=confirmed_login();before=copy.deepcopy(state)
        self.assertEqual(sum(initial_readings(state).values()),3)
        self.assertTrue(initial_loading_complete(state))
        report=initial_loading_status(state)
        self.assertEqual(report['pending_fields'],['Dever diário'])
        self.assertEqual(report['deferred_fields'],['Dever diário'])
        self.assertFalse(report['groups']['Diárias e Semanais'])
        self.assertEqual(state,before)

    def test_no_duty_list_or_rejected_duty_is_deferred_without_guessing_a_counter(self):
        for duty in ({},{'available':False,'reading_error':'Unknown list'},
                     {'available':True,'cached':True,'current':4}):
            state=confirmed_login();state['daily_duty']=duty;before=copy.deepcopy(duty)
            self.assertTrue(initial_loading_complete(state))
            self.assertEqual(initial_loading_status(state)['pending_fields'],['Dever diário'])
            self.assertEqual(state['daily_duty'],before)

    def test_every_other_baseline_must_still_be_fresh(self):
        paths=[('od_energy',),('resource_gains','kinah_daily'),('resource_gains','abyss_weekly'),
            ('weekly_orders','altgard'),('weekly_orders','latesran'),
            *(('activity_entries',key) for key in ('daily_dungeon','shugo_party','nightmare','ascension_rite'))]
        for path in paths:
            for flag in ('cached','manual','partial','reset_pending','reading_error','invalidated'):
                state=confirmed_login();value=state
                for key in path:value=value[key]
                value[flag]=True
                with self.subTest(path=path,flag=flag):self.assertFalse(initial_loading_complete(state))

    def test_character_pet_source_and_selection_proofs_cannot_be_bypassed(self):
        for change in ({'login_session':{'entry_confirmed':True}}, {'pet_collection':{}},
                       {'running':False},{'capture_ready':False},{'error':'capture failed'},
                       {'character_selection':{'confirmed':True}}):
            state=confirmed_login();state.update(change)
            with self.subTest(change=change):self.assertFalse(initial_loading_complete(state))

    def test_fresh_duty_later_completes_status_without_relogin(self):
        state=confirmed_login();self.assertTrue(initial_loading_complete(state))
        state['daily_duty']=dict(available=True,current=2,limit=5)
        self.assertTrue(all(initial_readings(state).values()))
        self.assertEqual(initial_loading_status(state)['pending_fields'],[])
        self.assertEqual(state['daily_duty']['current'],2)


if __name__=='__main__':unittest.main()
