# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Offline adversarial updater tests with ephemeral signing keys/inert fixtures."""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import update_core as update
from verify_core import VerificationError


BASE = "https://imperiobdo.com/aion2/releases/pilot/"
NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


class Response:
    def __init__(self, raw=b"", *, status=200, headers=None):
        self.status = status
        self.headers = {"Content-Length": str(len(raw))} if headers is None else headers
        self.stream = io.BytesIO(raw)
        self.closed = False

    def read(self, size):
        return self.stream.read(size)

    def close(self):
        self.closed = True


class Transport:
    def __init__(self):
        self.routes = {}
        self.requests = []

    def request(self, url, *, timeout):
        self.requests.append(url)
        if url not in self.routes:
            return Response(status=404)
        route = self.routes[url]
        return route() if callable(route) else Response(route)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="updater-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes_raw()
        self.transport = Transport()
        self.now = NOW
        self.manager = self.new_manager()

    def new_manager(self, **kwargs):
        values = dict(policy=update.UpdatePolicy(BASE), minimum_counter=1,
                      transport=self.transport, clock=lambda: self.now)
        values.update(kwargs)
        return update.UpdateManager(self.root / "code", self.root / "state", self.public, **values)

    def fixture(self, counter=1, *, content=b"MZ inert test bytes", expires=None, name=None, entries=None):
        files = {"imperioaion.exe": content, "_internal/data.json": b'{"inert":true}'}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for filename, raw in (entries if entries is not None else [("release/" + p, b) for p, b in files.items()]):
                archive.writestr(filename, raw)
        archive_raw = output.getvalue()
        payload = {
            "schema": 1, "product": "Imperio.AION", "channel": "pilot", "version": f"0.8.{counter}",
            "release_counter": counter, "not_before": "2026-10-14T00:00:00Z",
            "expires": expires or "2026-11-14T00:00:00Z",
            "archive": {"name": name or f"release-{counter}.zip", "prefix": "release/",
                        "size": len(archive_raw), "sha256": hashlib.sha256(archive_raw).hexdigest()},
            "files": [{"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                      for path, raw in files.items()],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        envelope = json.dumps({"payload": base64.b64encode(raw).decode(),
                               "signature": base64.b64encode(self.key.sign(raw)).decode()}).encode()
        self.transport.routes[BASE + "release.manifest"] = envelope
        self.transport.routes[self.manager._url(payload["archive"]["name"])] = archive_raw
        return envelope, archive_raw, payload

    def install(self, counter, **kwargs):
        self.fixture(counter, **kwargs)
        return self.manager.update()

    def state(self):
        return json.loads(self.manager.state_file.read_text())

    def test_full_download_verify_extract_commit_and_reverify_without_execution(self):
        result = self.install(1)
        self.assertEqual(result["status"], "INSTALLED")
        self.assertFalse(result["executed"])
        self.assertFalse(result["elevated"])
        self.assertEqual(self.manager.current()["counter"], 1)
        self.assertEqual(len(self.transport.requests), 2)
        self.assertEqual(list(self.manager.downloads.iterdir()), [])
        self.assertEqual(list(self.manager.staging.iterdir()), [])
        self.assertFalse(self.manager.state_root.is_relative_to(self.manager.code_root))

    def test_https_origin_and_explicit_literal_loopback_policy(self):
        for base in ("http://imperiobdo.com/aion2/", "https://evil.example/aion2/",
                     "https://imperiobdo.com.evil/aion2/", "https://user@imperiobdo.com/aion2/",
                     "https://imperiobdo.com:444/aion2/", "http://127.0.0.1:8080/",
                     "https://imperiobdo.com/aion2/?a=1", "https://imperiobdo.com/a/../b/",
                     "https://imperiobdo.com/a%2fb/", "https://imperiobdo.com//a/", "file:///tmp/"):
            with self.subTest(base=base), self.assertRaises(VerificationError):
                self.new_manager(policy=update.UpdatePolicy(base))
        self.new_manager(policy=update.UpdatePolicy("http://127.0.0.1:8080/releases/", allow_loopback_test=True))
        self.new_manager(policy=update.UpdatePolicy("http://[::1]:8080/releases/", allow_loopback_test=True))
        with self.assertRaises(VerificationError):
            self.new_manager(policy=update.UpdatePolicy("http://localhost:8080/", allow_loopback_test=True))

    def test_redirect_outside_origin_loopback_wrong_path_or_infinite_rejected(self):
        for location in ("https://evil.example/payload", "http://imperiobdo.com/aion2/releases/pilot/a.zip",
                         "http://127.0.0.1:8080/a.zip", "https://imperiobdo.com/other/a.zip",
                         "https://imperiobdo.com:444/aion2/releases/pilot/a.zip", "release.manifest"):
            self.transport.routes[BASE + "release.manifest"] = lambda value=location: Response(
                status=302, headers={"Location": value})
            with self.subTest(location=location), self.assertRaises(VerificationError):
                self.manager.check()

    def test_same_origin_single_basename_redirect_is_validated(self):
        envelope, _, _ = self.fixture()
        self.transport.routes[BASE + "release.manifest"] = lambda: Response(status=302, headers={"Location": "other.manifest"})
        self.transport.routes[BASE + "other.manifest"] = envelope
        self.assertEqual(self.manager.check().manifest.release_counter, 1)

    def test_signature_failure_prevents_archive_request(self):
        envelope, _, _ = self.fixture()
        data = json.loads(envelope)
        data["signature"] = base64.b64encode(b"x" * 64).decode()
        self.transport.routes[BASE + "release.manifest"] = json.dumps(data).encode()
        with self.assertRaises(VerificationError):
            self.manager.update()
        self.assertEqual(self.transport.requests, [BASE + "release.manifest"])

    def test_download_actual_size_length_encoding_and_partial_responses_rejected(self):
        envelope, _, _ = self.fixture()
        cases = [lambda: Response(envelope, headers={"Content-Length": "1"}),
                 lambda: Response(envelope, headers={"Content-Length": "-1"}),
                 lambda: Response(envelope, headers={"Content-Length": "999999999"}),
                 lambda: Response(envelope, headers={"Content-Encoding": "gzip"}),
                 lambda: Response(envelope, headers={"Content-Range": "bytes 0-10/100"}),
                 lambda: Response(envelope, status=206),
                 lambda: Response(envelope, headers={"X-Long": "x" * 9000})]
        for response in cases:
            self.transport.routes[BASE + "release.manifest"] = response
            with self.subTest(response=response), self.assertRaises(VerificationError):
                self.manager.check()

    def test_absent_content_length_still_enforces_actual_bytes(self):
        self.transport.routes[BASE + "release.manifest"] = lambda: Response(b"a" * 100, headers={})
        with self.assertRaisesRegex(VerificationError, "excedeu"):
            self.manager._fetch(BASE + "release.manifest", maximum=10, deadline=60)

    def test_global_deadline_and_transport_failure_fail_closed(self):
        self.fixture()
        clock = iter((0, 0, 61))
        self.manager.monotonic = lambda: next(clock)
        with self.assertRaisesRegex(VerificationError, "Prazo"):
            self.manager.check()
        self.manager.monotonic = lambda: 0
        self.transport.routes[BASE + "release.manifest"] = lambda: (_ for _ in ()).throw(OSError("offline"))
        with self.assertRaises(VerificationError):
            self.manager.check()

    def test_bad_archive_digest_or_truncated_download_never_extracts(self):
        _, raw, _ = self.fixture()
        release = self.manager.check()
        for changed in (b"x" * len(raw), raw[:-1], raw + b"x"):
            self.transport.routes[release.archive_url] = changed
            with self.subTest(length=len(changed)), self.assertRaises(VerificationError):
                self.manager.stage(release)
            self.assertEqual(list(self.manager.staging.iterdir()), [])
            self.assertEqual(list(self.manager.downloads.iterdir()), [])
            self.assertIsNone(self.manager.current())

    def test_signed_archive_with_extra_traversal_or_symlink_never_extracts(self):
        entry = zipfile.ZipInfo("release/imperioaion.exe")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [[("../outside", b"evil")], [("release/extra.dll", b"evil")], [(entry, b"elsewhere")]]
        for entries in cases:
            self.fixture(entries=entries)
            with self.subTest(entries=entries), self.assertRaises(VerificationError):
                self.manager.stage(self.manager.check())
            self.assertFalse((self.root / "outside").exists())
            self.assertEqual(list(self.manager.staging.iterdir()), [])

    def test_staged_extra_code_or_changed_file_blocks_commit(self):
        self.fixture()
        staged = self.manager.stage(self.manager.check())
        extra = staged.directory / "extra.dll"
        extra.write_bytes(b"evil")
        with self.assertRaises(VerificationError):
            self.manager.commit(staged)
        extra.unlink()
        (staged.directory / "imperioaion.exe").write_bytes(b"changed")
        with self.assertRaises(VerificationError):
            self.manager.commit(staged)
        self.assertIsNone(self.manager.current())

    def test_expiry_between_stage_and_commit_is_rechecked(self):
        self.fixture(expires="2026-10-15T13:00:00Z")
        staged = self.manager.stage(self.manager.check())
        self.now = NOW + timedelta(hours=2)
        with self.assertRaisesRegex(VerificationError, "expirado"):
            self.manager.commit(staged)
        self.assertIsNone(self.manager.current())

    def test_update_preserves_previous_and_rollback_retains_high_counter(self):
        first = self.install(1)
        self.install(2, content=b"MZ second inert version")
        self.assertTrue(Path(first["directory"]).is_dir())
        result = self.manager.rollback()
        self.assertEqual(result["counter"], 1)
        self.assertEqual(result["max_counter"], 2)
        self.assertEqual(self.manager.current()["max_counter"], 2)
        self.fixture(1)
        with self.assertRaisesRegex(VerificationError, "downgrade"):
            self.manager.check()

    def test_same_counter_with_different_content_is_rejected(self):
        self.install(1)
        self.fixture(1, content=b"MZ different bytes same counter")
        with self.assertRaisesRegex(VerificationError, "Mesmo contador"):
            self.manager.check()

    def test_reinstall_identical_release_is_idempotent(self):
        self.fixture(1)
        self.manager.update()
        self.now += timedelta(minutes=1)
        result = self.manager.update()
        self.assertEqual(result["status"], "CURRENT")
        self.assertIsNone(self.state()["previous"])

    def test_local_installed_release_remains_verifiable_after_metadata_expires(self):
        self.install(1, expires="2026-10-15T13:00:00Z")
        self.now += timedelta(days=2)
        self.assertEqual(self.manager.current()["counter"], 1)
        with self.assertRaisesRegex(VerificationError, "expirado"):
            self.manager.check()

    def test_commit_pointer_failure_preserves_active_and_new_watermark(self):
        self.install(1)
        self.fixture(2)
        staged = self.manager.stage(self.manager.check())
        original = self.manager._save_state
        calls = 0
        def fail_pointer(state):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated full disk on pointer replacement")
            original(state)
        with patch.object(self.manager, "_save_state", side_effect=fail_pointer):
            with self.assertRaisesRegex(VerificationError, "anterior preservada"):
                self.manager.commit(staged)
        self.assertEqual(self.manager.current()["counter"], 1)
        self.assertEqual(self.manager.current()["max_counter"], 2)
        self.assertIsNone(self.state()["pending"])

    def test_interrupted_journal_recovers_old_pointer_without_lowering_counter(self):
        self.install(1)
        self.fixture(2)
        staged = self.manager.stage(self.manager.check())
        original = self.manager._save_state
        calls = 0
        def fail_after_journal(state):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise OSError("simulated persistent disk failure")
            original(state)
        with patch.object(self.manager, "_save_state", side_effect=fail_after_journal):
            with self.assertRaises(VerificationError):
                self.manager.commit(staged)
        self.assertIsNotNone(self.state()["pending"])
        reopened = self.new_manager()
        result = reopened.current()
        self.assertEqual((result["counter"], result["max_counter"]), (1, 2))
        self.assertIsNone(self.state()["pending"])

    def test_failed_rename_preserves_previous_and_does_not_downgrade(self):
        self.install(1)
        self.fixture(2)
        staged = self.manager.stage(self.manager.check())
        with patch.object(update.os, "rename", side_effect=OSError("simulated version in use")):
            with self.assertRaises(VerificationError):
                self.manager.commit(staged)
        self.assertEqual(self.manager.current()["counter"], 1)
        self.assertEqual(self.state()["max_counter"], 2)

    def test_corrupt_missing_duplicate_state_does_not_reset_counter(self):
        self.install(1)
        original = self.manager.state_file.read_bytes()
        for raw in (b"{broken", b'{"schema":1,"schema":1}', original.replace(b'"max_counter":1', b'"max_counter":0')):
            self.manager.state_file.write_bytes(raw)
            with self.subTest(raw=raw[:50]), self.assertRaises(VerificationError):
                self.manager.current()
        self.manager.state_file.unlink()
        with self.assertRaisesRegex(VerificationError, "contador"):
            self.manager.current()

    def test_current_rechecks_extra_code_marker_and_installed_bytes(self):
        result = self.install(1)
        directory = Path(result["directory"])
        (directory / "extra.pyd").write_bytes(b"injected")
        with self.assertRaises(VerificationError):
            self.manager.current()
        (directory / "extra.pyd").unlink()
        (directory / "release.manifest").write_bytes(b"replaced")
        with self.assertRaises(VerificationError):
            self.manager.current()

    def test_roots_must_be_separate_and_empty_or_owned(self):
        with self.assertRaises(VerificationError):
            update.UpdateManager(self.root / "one", self.root / "one/state", self.public,
                                 policy=update.UpdatePolicy(BASE), transport=self.transport)
        code = self.root / "code"
        code.mkdir()
        (code / "unrelated.txt").write_text("do not touch")
        self.fixture()
        with self.assertRaisesRegex(VerificationError, "não está vazia"):
            self.manager.check()
        self.assertEqual((code / "unrelated.txt").read_text(), "do not touch")

    def test_cleanup_is_confined_to_owned_uuid_children(self):
        self.fixture()
        self.manager.check()
        outside = self.root / "do-not-delete"
        outside.mkdir()
        with self.assertRaisesRegex(VerificationError, "Limpeza fora"):
            self.manager._remove_owned(outside, self.manager.staging)
        self.assertTrue(outside.exists())
        with self.assertRaises(VerificationError):
            self.manager._remove_owned(self.manager.staging, self.manager.staging)

    def test_retained_original_archive_and_explicit_cleanup(self):
        self.fixture()
        staged = self.manager.stage(self.manager.check(), keep_archive=True)
        self.assertIsNotNone(staged.archive_path)
        self.assertTrue(staged.archive_path.is_file())
        self.manager.commit(staged)
        self.assertTrue(staged.archive_path.is_file())
        self.manager.discard_archive(staged)
        self.assertFalse(staged.archive_path.exists())

    def test_discard_uncommitted_stage_cleans_only_owned_candidate_and_archive(self):
        self.fixture()
        staged = self.manager.stage(self.manager.check(), keep_archive=True)
        self.manager.discard_stage(staged)
        self.assertFalse(staged.directory.exists())
        self.assertFalse(staged.archive_path.exists())
        self.assertIsNone(self.manager.current())

    def test_extraction_io_failure_preserves_existing_active_version(self):
        self.install(1)
        self.fixture(2)
        release = self.manager.check()
        with patch.object(self.manager, "_extract", side_effect=OSError("simulated disk full")):
            with self.assertRaises(OSError):
                self.manager.stage(release)
        self.assertEqual(self.manager.current()["counter"], 1)
        self.assertEqual(self.state()["max_counter"], 1)
        self.assertEqual(list(self.manager.staging.iterdir()), [])
        self.assertEqual(list(self.manager.downloads.iterdir()), [])

    def test_state_active_path_cannot_select_arbitrary_directory(self):
        self.install(1)
        state = self.state()
        state["active"]["directory"] = "../../external"
        self.manager.state_file.write_text(json.dumps(state))
        with self.assertRaises(VerificationError):
            self.manager.current()

    def test_tampered_previous_version_cannot_be_used_for_rollback(self):
        old = self.install(1)
        self.install(2)
        (Path(old["directory"]) / "imperioaion.exe").write_bytes(b"changed previous app")
        with self.assertRaises(VerificationError):
            self.manager.rollback()
        self.assertEqual(self.manager.current()["counter"], 2)

    def test_installer_envelope_payload_must_equal_authenticated_ed25519_bytes(self):
        envelope, _, _ = self.fixture()
        release = self.manager.check()
        rsa = {"payload": json.loads(envelope)["payload"], "signature": base64.b64encode(b"x" * 256).decode()}
        raw = json.dumps(rsa).encode()
        self.transport.routes[BASE + "release.install"] = raw
        self.assertEqual(self.manager.download_installer_envelope(release), raw)
        # This API only matches payloads. Native helper must reject the fake RSA.
        rsa["payload"] = base64.b64encode(b"changed payload").decode()
        self.transport.routes[BASE + "release.install"] = json.dumps(rsa).encode()
        with self.assertRaisesRegex(VerificationError, "difere"):
            self.manager.download_installer_envelope(release)

    def test_reparse_point_attribute_is_rejected_before_filesystem_mutation(self):
        self.fixture()
        with patch.object(update, "_plain_ancestors", side_effect=VerificationError("reparse detected")):
            with self.assertRaisesRegex(VerificationError, "reparse"):
                self.manager.check()
        self.assertFalse((self.root / "code").exists())

    def test_archive_name_hash_character_is_encoded_as_one_segment(self):
        self.fixture(name="release#one.zip")
        release = self.manager.check()
        self.assertTrue(release.archive_url.endswith("release%23one.zip"))
        self.assertEqual(self.manager.commit(self.manager.stage(release))["status"], "INSTALLED")

    def test_core_never_elevates_or_launches_application(self):
        source = Path(update.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("ShellExecute", source)
        self.assertNotIn("extractall(", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
