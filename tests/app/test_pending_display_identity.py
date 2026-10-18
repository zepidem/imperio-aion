# SPDX-License-Identifier: GPL-3.0-only
"""Pending nicknames are presentation state, never a damage attribution key."""
from copy import deepcopy
import unittest

from combat_charts import chart_series, row_key
from display_identity import local_player_rows, name_pending, player_label
from presentation import Catalog


class _SkillNames:
    def name(self, code, fallback=''):
        return fallback or 'Synthetic skill'


class PendingDisplayIdentityTests(unittest.TestCase):
    def test_only_explicitly_pending_peers_show_sync_label_and_self_takes_precedence(self):
        rows = [
            dict(name='Você · nome pendente', actor=101, identity_pending=True),
            dict(name='Integrante 2 · nome pendente', actor=202, identity_pending=True),
            dict(name='Nome recebido', actor=303, identity_pending=False),
            dict(name='Nome recebido sem marcador', actor=404),
            dict(name='Integrante no nome real', actor=505, identity_pending=False),
            dict(name='Sincronizando', actor=606, identity_pending=False),
            dict(name='Nome com marcador inválido', actor=707, identity_pending='true'),
        ]
        original = deepcopy(rows)

        displayed = local_player_rows(rows, local_actor=101)

        self.assertEqual([player_label(row) for row in displayed],
                         ['Você', 'Sincronizando', 'Nome recebido',
                          'Nome recebido sem marcador', 'Integrante no nome real',
                          'Sincronizando', 'Nome com marcador inválido'])
        self.assertEqual([name_pending(row) for row in displayed],
                         [False, True, False, False, False, False, False])
        self.assertEqual(rows, original)
        self.assertFalse(any(row.get('self_identity') for row in displayed[1:]))

    def test_equal_pending_labels_do_not_merge_actor_hits_or_skill_series(self):
        fight = dict(key='grupo/one/1/77/1', flow='one', start_us=0,
                     duration_us=2_000_000)
        rows = [
            dict(name='Integrante 2 · nome pendente', actor=202,
                 observation_key='one/1/202', identity_pending=True,
                 class_key='sorcerer', damage=80,
                 skills=[dict(code=2, damage=80, records=1)]),
            dict(name='Integrante 3 · nome pendente', actor=303,
                 observation_key='one/1/303', identity_pending=True,
                 class_key='chanter', damage=50,
                 skills=[dict(code=3, damage=50, records=1)]),
        ]
        state = dict(group_events=[
            dict(encounter_key=fight['key'], actor=202, clock_us=500_000,
                 actor_name=rows[0]['name'], candidate_amount=80, raw_skill=2),
            dict(encounter_key=fight['key'], actor=303, clock_us=1_000_000,
                 actor_name=rows[1]['name'], candidate_amount=50, raw_skill=3),
        ])
        original = deepcopy((state, fight, rows))

        players, skills, partial = chart_series(state, fight, rows, '', _SkillNames())

        self.assertFalse(partial)
        self.assertEqual([line['name'] for line in players],
                         ['Sincronizando', 'Sincronizando'])
        self.assertEqual([line['key'] for line in players], ['actor/202', 'actor/303'])
        self.assertEqual([line['hits'] for line in players], [[(0.5, 80)], [(1.0, 50)]])
        self.assertEqual([(line['member'], line['damage']) for line in skills],
                         [('actor/202', 80), ('actor/303', 50)])
        self.assertEqual((state, fight, rows), original)

        # Receiving one name changes that label immediately, not the other row,
        # and never resets, merges, or reallocates either player's accumulated DPS.
        rows[0].update(name='Primeiro nome recebido', identity_pending=False,
                       server=2406, dbid=9002)
        named = deepcopy((state, fight, rows))
        after, after_skills, after_partial = chart_series(state, fight, rows, '', _SkillNames())

        self.assertFalse(after_partial)
        self.assertEqual([line['name'] for line in after],
                         ['Primeiro nome recebido', 'Sincronizando'])
        self.assertEqual([line['key'] for line in after], [line['key'] for line in players])
        self.assertEqual([line['hits'] for line in after], [line['hits'] for line in players])
        self.assertEqual(after_skills, skills)
        self.assertEqual((state, fight, rows), named)

    def test_pending_projection_preserves_export_identity_and_combat_fields(self):
        rows = [dict(name='Integrante 2 · nome pendente', actor=202, dbid=None,
                     observation_key='flow-a/7/202', flow='flow-a', scope_epoch=7,
                     server=2406, identity_pending=True, class_id=27,
                     damage=1234, dps_average=617, dps_current=617,
                     duration_us=2_000_000, records=2,
                     skills=[dict(code=1, damage=1234, records=2)])]
        original = deepcopy(rows)

        displayed = local_player_rows(rows)
        self.assertEqual(player_label(displayed[0]), 'Sincronizando')
        self.assertTrue(name_pending(displayed[0]))
        self.assertEqual(row_key(displayed[0]), 'actor/202')
        for field, value in rows[0].items():
            with self.subTest(field=field):
                self.assertEqual(displayed[0][field], value)
        self.assertEqual(rows, original)

    def test_pending_name_does_not_hide_received_class_and_color(self):
        row = dict(name='Integrante 2 · nome pendente', actor=202,
                   identity_pending=True, class_id=27)
        original = deepcopy(row)

        self.assertEqual(player_label(row), 'Sincronizando')
        self.assertEqual(Catalog().player_class({}, {'flow': 'one'}, row), 'sorcerer')
        row.update(name='Nome recebido', identity_pending=False)
        self.assertEqual(player_label(row), 'Nome recebido')
        self.assertEqual(Catalog().player_class({}, {'flow': 'one'}, row), 'sorcerer')
        row.update(name=original['name'], identity_pending=True)
        self.assertEqual(row, original)


if __name__ == '__main__':
    unittest.main()
