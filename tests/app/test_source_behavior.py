# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic protocol boundaries and explicit missing-catalog behavior."""
import tempfile,unittest
from pathlib import Path
from inspect_wire import varint,reconstruct
from pet_catalog import PetCatalog
from skill_catalog import SkillCatalog

class ProtocolBoundaryTests(unittest.TestCase):
    def test_varint_uint32_limit(self):
        self.assertEqual(varint(bytes.fromhex('ffffffff0f'),0),(2**32-1,5))
        for raw in (b'\x80',bytes.fromhex('ffffffff10'),bytes.fromhex('808080808000')):
            with self.subTest(raw=raw),self.assertRaises(ValueError):varint(raw,0)

    def test_conflicting_overlap_and_gap_are_rejected(self):
        def row(seq,payload,number):
            return {'tcp.seq_raw':str(seq),'tcp.payload':payload.hex(),'frame.time_epoch':'1','frame.number':str(number)}
        for second in (row(101,b'X',2),row(104,b'd',2)):
            with self.subTest(second=second),self.assertRaises(ValueError):
                reconstruct([row(100,b'abc',1),second])

    def test_identical_tcp_retransmission_is_not_counted_twice(self):
        rows=[{'tcp.seq_raw':'100','tcp.payload':'616263','frame.time_epoch':'1','frame.number':str(n)} for n in (1,2)]
        data,spans,duplicates=reconstruct(rows)
        self.assertEqual(data,b'abc');self.assertEqual(duplicates,3);self.assertEqual(len(spans),1)

class MissingCatalogTests(unittest.TestCase):
    def test_missing_pet_catalog_does_not_invent_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog=PetCatalog(Path(tmp)/'absent.json')
            self.assertIsNone(catalog.get(999999));self.assertEqual(catalog.portraits(),{})
            self.assertTrue(catalog.errors)

    def test_missing_skill_catalog_keeps_unknown_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog=SkillCatalog(tmp)
            self.assertEqual(catalog.name(999999),'Habilidade não identificada')
            self.assertIsNone(catalog.photo(None,999999))

if __name__=='__main__':unittest.main()
