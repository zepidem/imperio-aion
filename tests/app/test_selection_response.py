# SPDX-License-Identifier: GPL-3.0-only
"""Selection recognition must not pin the varying field in the initial list."""
import tempfile,unittest
from character_selection import CharacterSelection,selection_profile
from collector import Controller


def initial_list(variable=1257495672,base=3787):
    name=b'Example';body=bytearray(base+len(name))
    body[:4]=b'\x15\x36\0\0';body[4:8]=variable.to_bytes(4,'little')
    body[790]=len(name);body[791:798]=name
    body[798:802]=(20).to_bytes(4,'little')
    body[804:808]=(45).to_bytes(4,'little')
    realm_at=base-452+len(name);body[realm_at:realm_at+2]=(2405).to_bytes(2,'little')
    return bytes(body)


class InitialSelectionResponseTests(unittest.TestCase):
    def test_both_observed_values_and_the_field_boundaries_preserve_the_valid_layout(self):
        for value in (1171624224,1257495672,0,2**32-1):
            for base in (3787,3560):
                with self.subTest(value=value,base=base):
                    self.assertEqual(selection_profile(initial_list(value,base)),'sa-1536-initial-avatar-list')

    def test_unknown_layouts_and_invalid_avatar_fields_remain_pending(self):
        body=initial_list();bad_name=bytearray(body);bad_name[791]=255
        bad_class=bytearray(body);bad_class[798:802]=bytes(4)
        bad_level=bytearray(body);bad_level[804:808]=bytes(4)
        bad_realm=bytearray(body);bad_realm[3342:3344]=(999).to_bytes(2,'little')
        for raw in (body[:-1],body+b'\0',b'\x15\x36\x01'+body[3:],
                    b'\x04\x38'+body,bytes(bad_name),bytes(bad_class),bytes(bad_level),bytes(bad_realm)):
            with self.subTest(length=len(raw)):self.assertIsNone(selection_profile(raw))

    def test_selection_proof_still_requires_owned_incoming_channel_and_running_capture(self):
        state=CharacterSelection();ctx=dict(flow='incoming')
        for owned in (None,'other'):
            state.observe(ctx,initial_list(),owned)
            self.assertFalse(state.snapshot('incoming',True)['confirmed'])
        state.observe(ctx,initial_list(),'incoming')
        self.assertTrue(state.snapshot('incoming',True)['confirmed'])
        self.assertFalse(state.snapshot('incoming',False)['confirmed'])
        self.assertFalse(state.snapshot('incoming',True)['visited'])

    def test_framed_startup_recognizes_selection_without_inventing_a_login_or_character(self):
        def packed(value):
            raw=bytearray()
            while value>127:raw.append((value&127)|128);value>>=7
            raw.append(value);return bytes(raw)
        body=initial_list();heartbeat=b'\x06\x00\x36'
        wire=heartbeat*20+packed(len(body)+4)+body
        row={'frame.time_epoch':'1','frame.number':'1','tcp.stream':'1',
             'ip.src':'127.0.0.1','tcp.srcport':'50000','ip.dst':'127.0.0.1','tcp.dstport':'50001',
             'tcp.seq_raw':'1000','tcp.len':str(len(wire)),'tcp.payload':wire.hex()}
        # The test runner isolates temporary directories under its workspace.
        with tempfile.TemporaryDirectory() as folder:
            c=Controller(cache_directory=folder);self.addCleanup(c.stop);c._prepare('','live')
            flow='|'.join((str(c.reader.generation),'1','127.0.0.1','50000','127.0.0.1','50001'))
            c.incoming_flows.add(flow);c.flow_processes[flow]=12345
            c.metadata['capture_source_ready']=True;c.reader.ingest(row)
            result=c.snapshot()
            self.assertTrue(result['character_selection']['confirmed'])
            self.assertFalse(result['login_session']['confirmed'])
            self.assertFalse(result['identified']);self.assertEqual(result['character'],'')
            self.assertFalse(result['od_energy']['available']);self.assertEqual(result['encounters'],[])


if __name__=='__main__':unittest.main()
