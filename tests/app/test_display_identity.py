# SPDX-License-Identifier: GPL-3.0-only
"""The local-player label must not become identity or a damage-selection key."""
from copy import deepcopy
import unittest

from combat_charts import chart_series, row_key
from display_identity import LOCAL_PLAYER_LABEL, local_player_rows, player_label
from group_core import GroupBook
from presentation import Catalog


class _SkillNames:
    def name(self, code, fallback=''):
        return fallback or 'Synthetic skill'


class DisplayIdentityTests(unittest.TestCase):
    def test_named_own_group_history_keeps_its_epoch_identity_after_relog(self):
        book = GroupBook()
        flow = 'same-tcp'
        old_actor = 101
        book.confirm_local_actor(flow, dict(actor=old_actor, first_us=0,
                                          basis='owned-self-stats-4a36'))
        roster = dict(key=7, members=[
            dict(name='Meu nome real', server=2405, dbid=9001, class_id=36),
            dict(name='Integrante real', server=2406, dbid=9002, class_id=27),
        ])
        original_roster = deepcopy(roster)
        book.rosters[flow] = roster
        for member, actor in zip(roster['members'], (old_actor, 202)):
            book.identify(flow, dict(member, actor=actor))
        book.timeline.append(dict(kind='roster', flow=flow, clock_us=0,
                                  roster=roster, scope_epoch=0))
        book.timeline.append(dict(kind='damage', flow=flow, clock_us=1_000_000,
                                  actor=old_actor, target=999, raw_skill=1,
                                  skill_name='Synthetic skill', candidate_amount=100))

        # Existing same-flow login cleanup; the same nickname can keep history.
        book.clear_roster(dict(flow=flow, clock_us=2_000_000), block_scope=False)
        book.local_actors.pop(flow, None)
        new_proof = dict(actor=303, first_us=3_000_000, basis='owned-self-stats-4a36')
        book.confirm_local_actor(flow, new_proof)
        fight = book.snapshot(3_500_000)['group_encounters'][0]
        # The current proof is newer than the historical fight, so App supplies
        # no current actor; the old epoch's own flag must already be preserved.
        self.assertLess(fight['end_us'], new_proof['first_us'])
        rows = local_player_rows(fight['members'])
        own = next(row for row in rows if row['actor'] == old_actor)
        peer = next(row for row in rows if row['actor'] == 202)

        self.assertEqual(player_label(own), 'Você')
        self.assertIs(own.get('self_identity'), True)
        self.assertFalse(peer.get('self_identity'))
        self.assertEqual(player_label(peer), 'Integrante real')
        self.assertEqual((own['name'], own['class_id'], own['damage']),
                         ('Meu nome real', 36, 100))
        self.assertEqual(peer['damage'], 0)
        self.assertEqual(roster, original_roster)
        self.assertEqual(book.local_actor_epochs[(flow, 0)]['actor'], old_actor)

    def test_local_rows_use_proven_actor_and_copy_without_name_heuristics(self):
        rows = [
            dict(name='Mesmo nome', actor=101, self_identity=False, damage=120),
            dict(name='Mesmo nome', actor=202, self_identity=False, damage=80),
            dict(name='Mesmo nome', actor=None, identity_pending=True),
            dict(name='Você', actor=303, self_identity=False),
        ]
        original = deepcopy(rows)

        marked = local_player_rows(rows, local_actor=101)

        self.assertEqual(rows, original)
        self.assertIsNot(marked, rows)
        for before, after in zip(rows, marked):
            self.assertIsNot(after, before)
            self.assertEqual(after['name'], before['name'])
        self.assertIs(marked[0].get('self_identity'), True)
        self.assertFalse(marked[1].get('self_identity'))
        self.assertFalse(marked[2].get('self_identity'))
        self.assertFalse(marked[3].get('self_identity'))
        self.assertEqual([player_label(row) for row in marked],
                         ['Você', 'Mesmo nome', 'Sincronizando', 'Você'])

    def test_existing_self_flag_survives_without_later_actor_proof(self):
        rows = [dict(name='Você · nome pendente', actor=101, self_identity=True),
                dict(name='Nome informado', actor=None)]
        original = deepcopy(rows)

        marked = local_player_rows(rows)

        self.assertIs(marked[0].get('self_identity'), True)
        self.assertFalse(marked[1].get('self_identity'))
        self.assertEqual(player_label(marked[0]), 'Você')
        self.assertEqual(rows, original)

    def test_actor_matching_does_not_coerce_text_or_boolean_identifiers(self):
        rows = [dict(name='Texto', actor='1'), dict(name='Booleano', actor=True),
                dict(name='Sem ator', actor=None), dict(name='Ator numérico', actor=1)]
        original = deepcopy(rows)

        marked = local_player_rows(rows, local_actor=1)

        self.assertEqual([bool(row.get('self_identity')) for row in marked],
                         [False, False, False, True])
        self.assertEqual(rows, original)

    def test_local_label_is_stable_before_and_after_explicit_nickname(self):
        pending = dict(name='Você · nome pendente', self_identity=True,
                       identity_pending=True, actor=101, server=2405, dbid=9001,
                       class_id=36, damage=123, dps_average=12.3,
                       skills=[dict(code=18010140, damage=123, records=1)])
        named = dict(pending, name='Nome real sintético', identity_pending=False)
        originals = deepcopy((pending, named))
        catalog = Catalog()

        self.assertEqual(LOCAL_PLAYER_LABEL, 'Você')
        for row in (pending, named):
            with self.subTest(identity_pending=row['identity_pending']):
                self.assertEqual(player_label(row), 'Você')
                self.assertEqual(catalog.player_class({}, {'flow': 'one'}, row), 'chanter')
                self.assertEqual(row_key(row), 'actor/101')
        self.assertEqual((pending, named), originals)

    def test_peers_keep_real_names_and_pending_display_without_becoming_local(self):
        cases = [
            dict(name='Integrante 2 · nome pendente', identity_pending=True),
            dict(name='Outro nome', self_identity=False),
            dict(name='Você', self_identity=False, actor=202, dbid=9002),
            dict(name='Marcador numérico', self_identity=1),
            dict(name='Marcador textual', self_identity='true'),
            dict(name=None, identity_pending=True),
        ]
        for row in cases:
            with self.subTest(row=row):
                original = deepcopy(row)
                expected = ('Sincronizando' if row.get('identity_pending') else
                            str(row.get('name') or ''))
                self.assertEqual(player_label(row), expected)
                self.assertEqual(row, original)

    def test_chart_display_collision_preserves_actor_hits_and_distinct_keys(self):
        fight = dict(key='grupo/one/1/77/1', flow='one', start_us=0, duration_us=2_000_000)
        own = dict(name='Meu nome real', self_identity=True, actor=101, dbid=9001,
                   server=2405, class_key='chanter', damage=120,
                   skills=[dict(code=1, damage=120, records=1)])
        peer = dict(name='Você', self_identity=False, actor=202, dbid=9002,
                    server=2406, class_key='sorcerer', damage=80,
                    skills=[dict(code=2, damage=80, records=1)])
        rows = [own, peer]
        state = dict(group_events=[
            dict(encounter_key=fight['key'], actor=101, actor_name='Meu nome real',
                 clock_us=500_000, candidate_amount=120, raw_skill=1),
            dict(encounter_key=fight['key'], actor=202, actor_name='Você',
                 clock_us=1_000_000, candidate_amount=80, raw_skill=2),
        ])
        original = deepcopy((state, fight, rows))

        players, skills, partial = chart_series(state, fight, rows, 'Meu nome real', _SkillNames())

        self.assertFalse(partial)
        self.assertEqual([line['name'] for line in players], ['Você', 'Você'])
        self.assertEqual([line['key'] for line in players], ['actor/101', 'actor/202'])
        self.assertEqual([line['hits'] for line in players], [[(0.5, 120)], [(1.0, 80)]])
        self.assertEqual([(line['member'], line['damage']) for line in skills],
                         [('actor/101', 120), ('actor/202', 80)])
        self.assertEqual((state, fight, rows), original)

    def test_chart_name_fallback_and_class_lookup_still_use_original_name(self):
        fight = dict(key='grupo/legacy/1/77/1', flow='legacy', start_us=0, duration_us=1_000_000)
        row = dict(name='Nome vinculado', self_identity=True, actor=None,
                   class_key='sorcerer', damage=60,
                   skills=[dict(code=3, damage=60, records=1)])
        state = dict(group_events=[
            dict(encounter_key=fight['key'], actor=101, actor_name='Nome vinculado',
                 clock_us=500_000, candidate_amount=60, raw_skill=3),
            dict(encounter_key=fight['key'], actor=202, actor_name='Você',
                 clock_us=600_000, candidate_amount=999, raw_skill=3),
        ], player_classes=[dict(flow='legacy', actor=101, name='Nome vinculado', class_id=27)])
        original = deepcopy((state, fight, row))

        self.assertEqual(Catalog().player_class(state, fight, row), 'sorcerer')
        players, skills, partial = chart_series(state, fight, [row], 'Nome vinculado', _SkillNames())

        self.assertFalse(partial)
        self.assertEqual(players[0]['name'], 'Você')
        self.assertEqual(players[0]['key'], 'Nome vinculado')
        self.assertEqual(players[0]['hits'], [(0.5, 60)])
        self.assertEqual(skills[0]['hits'], [(0.5, 60)])
        self.assertEqual((state, fight, row), original)


if __name__ == '__main__':
    unittest.main()
