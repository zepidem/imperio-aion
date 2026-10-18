# SPDX-License-Identifier: GPL-3.0-only
"""Sparse absolute resource updates; synthetic boundaries and a recorded shape."""
import tempfile,time,unittest
from od_energy import OdEnergy,decode_od,decode_initial_od,energy_parts
from collector import Controller
from live_core import Direction

KEY=0x03938701
CTX=dict(flow='owned',clock_us=1000000,packet=1)
CAPTURED=bytes.fromhex('0c61000801879303820103')

def number(value):
    out=bytearray()
    while value>=128:out.append((value&127)|128);value>>=7
    out.append(value);return bytes(out)

def update(kind,values=(),cause=0,key=KEY,delta=15):
    return (b'\x0c\x61'+bytes([cause,kind])+key.to_bytes(4,'little')+
        b''.join(number(v) for v in values)+
        (b'\x03' if cause==0 else b'\x01'+delta.to_bytes(4,'little')))

def initial(kind=12,values=(15,5)):
    return b'\x0b\x61\x01'+bytes([kind])+KEY.to_bytes(4,'little')+b''.join(number(v) for v in values)

def own(name='TestPlayer'):
    raw=name.encode();return b'\x33\x36'+number(1243)+bytes(4)+b'\x01'+bytes([len(raw)])+raw+(2405).to_bytes(2,'little')

class OdUpdateTests(unittest.TestCase):
    def test_recorded_bonus_only_update_does_not_keep_old_normal_balance(self):
        p=OdEnergy('TestPlayer');p.observe(CTX,initial());p.observe(CTX,own())
        p.observe(CTX,CAPTURED);value=p.snapshot(True)
        self.assertEqual(energy_parts(value),('0','(+130)','/840'))
        self.assertEqual(value['attribute_key'],KEY);self.assertIsNone(value['delta'])
        self.assertFalse(value.get('cached'))

    def test_presence_masks_match_the_initial_typed_attribute(self):
        for kind,values in [(0,()),(4,(30,)),(8,(130,)),(12,(30,5))]:
            for cause in (0,1):
                with self.subTest(kind=kind,cause=cause):
                    first=decode_initial_od(initial(kind,values));next_value=decode_od(update(kind,values,cause))
                    self.assertEqual((first['current'],first['bonus']),(next_value['current'],next_value['bonus']))

    def test_absolute_recharge_is_not_integrated_again_or_predicted_by_time(self):
        p=OdEnergy();body=update(12,(30,5),cause=1,delta=15)
        p.observe(CTX,body);p.observe(dict(CTX,clock_us=99999999999),body)
        self.assertEqual(energy_parts(p.snapshot(True)),('30','(+5)','/840'))
        self.assertEqual(p.state['delta'],15)

    def test_existing_full_recharge_and_spending_remain_supported(self):
        p=OdEnergy()
        p.observe(CTX,bytes.fromhex('0c61010c01879303649b010128000000'))
        self.assertEqual(energy_parts(p.snapshot(True)),('100','(+155)','/840'))
        p.observe(CTX,bytes.fromhex('0c61000c01879303149b0103'))
        self.assertEqual(energy_parts(p.snapshot(True)),('20','(+155)','/840'))

    def test_truncation_extra_bytes_unknown_kind_key_and_cause_are_rejected(self):
        for body in [CAPTURED[:n] for n in range(len(CAPTURED))]+[
            CAPTURED+b'\0',update(1),update(8,(130,),key=KEY+1),
            CAPTURED[:2]+b'\x02'+CAPTURED[3:],update(8,(1000000,)),
            update(8,(130,),cause=1,delta=0)]:
            with self.subTest(body=body.hex()),self.assertRaises((ValueError,IndexError)):
                decode_od(body)

    def test_unsupported_energy_update_invalidates_reading_until_repaired(self):
        p=OdEnergy();p.observe(CTX,CAPTURED);p.observe(CTX,CAPTURED[:-1])
        self.assertFalse(p.snapshot(True)['available'])
        p.observe(CTX,CAPTURED);self.assertTrue(p.snapshot(True)['available'])

    def test_other_attribute_does_not_replace_or_invalidate_energy(self):
        p=OdEnergy();p.observe(CTX,CAPTURED);before=dict(p.state)
        p.observe(CTX,update(4,(30,),key=101))
        self.assertEqual(p.state,before)

    def test_loading_update_stays_private_until_matching_self_header(self):
        p=OdEnergy('TestPlayer');p.observe(CTX,initial());p.observe(CTX,CAPTURED)
        self.assertFalse(p.snapshot(True)['available'])
        p.observe(CTX,own());self.assertEqual(energy_parts(p.snapshot(True)),('0','(+130)','/840'))

    def test_another_character_cannot_release_pending_energy(self):
        p=OdEnergy('TestPlayer');p.observe(CTX,initial());p.observe(CTX,CAPTURED)
        p.observe(CTX,own('Other'));self.assertFalse(p.snapshot(True)['available'])

    def test_owned_controller_updates_live_value_and_saved_cache_without_relogin(self):
        with tempfile.TemporaryDirectory() as directory:
            c=Controller('TestPlayer',cache_directory=directory)
            try:
                c._prepare('TestPlayer','live');c.metadata['capture_source_ready']=True
                d=Direction();d.accepted=True;d.heartbeats=20
                c.reader.directions={'owned':d};c.incoming_flows={'owned'};c.flow_processes={'owned':123}
                for i,body in enumerate([initial(),own(),update(12,(30,5)),CAPTURED],1):
                    c.reader.clock_us=i*1000;c.last_packet=time.monotonic()
                    c.reader.message(dict(flow='owned',clock_us=i*1000,packet=i,message_index=i),body)
                    state=c.snapshot()
                    if i==3:self.assertEqual(energy_parts(state['od_energy']),('30','(+5)','/840'))
                self.assertEqual(energy_parts(state['od_energy']),('0','(+130)','/840'))
                self.assertFalse(state['od_energy'].get('cached'))
                self.assertEqual(c.character_cache.select('TestPlayer')['od']['bonus'],130)
            finally:c.stop()


if __name__=='__main__':unittest.main()
