# SPDX-License-Identifier: GPL-3.0-only
"""Initial traffic is recorded only after a strict explicit one-use request."""
from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
from initial_diagnostic import REQUEST_FILE,consume_request
from collector import Controller


class InitialDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)

    def request(self,value=None):
        (self.path/REQUEST_FILE).write_text(json.dumps(value or dict(schema=1,record='initial-identity',seconds=600)),encoding='utf8')

    def test_no_request_never_arms_and_valid_request_is_consumed_once(self):
        self.assertFalse(consume_request(self.path));self.request()
        self.assertTrue(consume_request(self.path));self.assertFalse(consume_request(self.path))

    def test_invalid_oversized_or_unconsumable_requests_never_record(self):
        for value in ({'schema':True,'record':'initial-identity','seconds':600},
                      {'schema':1,'record':'initial-identity','seconds':601},
                      {'schema':1,'record':'initial-identity','seconds':600,'output':'untrusted'},['capture']):
            self.request(value);self.assertFalse(consume_request(self.path))
        self.request()
        with patch.object(Path,'unlink',side_effect=PermissionError):self.assertFalse(consume_request(self.path))
        (self.path/REQUEST_FILE).write_bytes(b' '*1025);self.assertFalse(consume_request(self.path))

    def test_old_request_expires_instead_of_recording_on_a_later_launch(self):
        self.request();mtime=(self.path/REQUEST_FILE).stat().st_mtime
        with patch('initial_diagnostic.time.time',return_value=mtime+3601):self.assertFalse(consume_request(self.path))

    def test_requested_diagnostic_precedes_first_worker_packet_and_ordinary_start_does_not_record(self):
        for requested in (False,True):
            c=Controller(cache_directory=self.path/'cache');self.addCleanup(c.stop)
            if requested:self.request()
            def first_packet():
                self.assertEqual(c.collection_diagnostic.active,requested)
                if requested:
                    self.assertEqual(c.collection_diagnostic.transport_observed,0)
                    self.assertFalse((self.path/REQUEST_FILE).exists())
            with patch('collector.enforce_elevated_installation'),patch('app_paths.data_directory',return_value=self.path),patch('collector.threading.Thread') as thread:
                thread.return_value.start.side_effect=first_packet
                c.start('','windivert')
            self.assertEqual(c.metadata.get('initial_diagnostic_requested',False),requested)
            c.worker=None


if __name__=='__main__':unittest.main()
