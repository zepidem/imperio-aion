# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic identity/class regressions for the imported 0.8.79 behavior."""
import unittest
from presentation import Catalog

class ClassOwnershipTests(unittest.TestCase):
    def setUp(self):self.catalog=Catalog();self.fight={'flow':'synthetic-current'}
    def row(self,code=11280140,**changes):return dict(dict(name='Nome pendente',actor=1001,identity_pending=True,skills=[dict(code=code)]),**changes)
    def test_shared_artwork_does_not_override_skill_class(self):
        self.assertEqual(self.catalog.player_class({},self.fight,self.row()),'gladiator')
    def test_later_name_preserves_class(self):
        self.assertEqual(self.catalog.player_class({},self.fight,self.row(name='Jogador Exemplo',identity_pending=False)),'gladiator')
    def test_unknown_and_shared_effect_are_not_evidence(self):
        for code in (3000023,99999999):self.assertEqual(self.catalog.player_class({},self.fight,self.row(code)),'unknown')
    def test_conflicting_skills_stay_unknown(self):
        self.assertEqual(self.catalog.player_class({},self.fight,self.row(skills=[dict(code=11010140),dict(code=12010140)])),'unknown')
    def test_explicit_class_is_authoritative(self):
        self.assertEqual(self.catalog.player_class({},self.fight,self.row(class_id=27)),'sorcerer')
    def test_unrelated_actor_is_not_borrowed(self):
        state=dict(player_classes=[dict(flow='synthetic-current',actor=9999,name='Outro exemplo',class_id=27)])
        self.assertEqual(self.catalog.player_class(state,self.fight,self.row()),'gladiator')
    def test_eight_job_associations(self):
        for code,job in [(11010140,'gladiator'),(12010140,'templar'),(13010140,'assassin'),(14010140,'ranger'),(15010140,'sorcerer'),(16010140,'spiritmaster'),(17010140,'cleric'),(18010140,'chanter')]:
            self.assertEqual(self.catalog.player_class({},self.fight,self.row(code)),job)
