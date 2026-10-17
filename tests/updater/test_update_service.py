# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Backend state/consent/cancel tests; inert files, mocked network/helper, no UAC."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from update_core import UpdateError
from verify_core import VerificationError
from update_service import UpdateService
from windows_bridge import PermissionDeclined, safe_environment

BASE = "http://127.0.0.1:38573/"


class Response:
    def __init__(self, raw, hook=None):
        self.status = 200
        self.headers = {"Content-Length": str(len(raw))}
        self.body = io.BytesIO(raw)
        self.hook = hook
    def read(self, size):
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        return self.body.read(min(size, 128))
    def close(self):
        pass


class Transport:
    def __init__(self):
        self.routes = {}
        self.calls = []
        self.archive_hook = None
    def request(self, url, *, timeout):
        self.calls.append(url)
        return Response(self.routes[url], self.archive_hook if url.endswith(".zip") else None)


class Bridge:
    def __init__(self):
        self.version = "0.8.57"
        self.counter = 857
        self.status = "installed"
        self.running = False
        self.cancel = False
        self.fail_verify = False
        self.installs = 0
        self.launches = 0
        self.on_install = None
        self.confirm_wrong_version = False
    def inspect_installed(self):
        return dict(status=self.status, version=self.version if self.status == "installed" else None,
            counter=self.counter if self.status == "installed" else None, running=self.running,
            running_state="present" if self.running else "absent", authenticated=self.status == "installed",
            launch_supported=self.counter >= 858, reason=None)
    def verify_archive(self, archive, metadata):
        if self.fail_verify:
            raise UpdateError("fake native verification refused")
        return "PASS inert fixture"
    def install(self, archive, metadata, *, on_started=None):
        if self.cancel:
            raise PermissionDeclined("Permissão cancelada pelo usuário.")
        if on_started:
            on_started()
        if self.on_install:
            self.on_install()
        self.installs += 1
        if not self.confirm_wrong_version:
            self.version, self.counter, self.status = "0.8.58", 858, "installed"
    def launch(self):
        self.launches += 1
        return "launch requested, not executed in test"


class ServiceTests(unittest.TestCase):
    def test_offline_startup_authenticates_without_creating_network_manager(self):
        from network_preferences import start_service
        self.bridge.counter=858
        start_service(self.service,False)
        self.assertEqual(self.service.snapshot()['phase'],'offline_ready')
        self.assertTrue(self.service.snapshot()['can_launch'])
        self.assertIsNone(self.service._manager)
        self.assertEqual(self.transport.calls,[])

    def test_offline_startup_keeps_corrupt_installation_blocked(self):
        from network_preferences import start_service
        self.bridge.status='corrupted'
        start_service(self.service,False)
        self.assertEqual(self.service.snapshot()['phase'],'corrupted')
        self.assertFalse(self.service.snapshot()['can_launch'])
        self.assertIsNone(self.service._manager)
        self.assertEqual(self.transport.calls,[])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="service-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bridge = Bridge()
        self.transport = Transport()
        self.events = []
        self.key = Ed25519PrivateKey.generate()
        @contextmanager
        def origin(directory, key, counter):
            yield BASE
        self.service = UpdateService(self.root / "updater-data", self.root / "feed",
            on_event=self.events.append, bridge=self.bridge,
            public_key_bytes=self.key.public_key().public_bytes_raw(), minimum_counter=857,
            transport=self.transport, origin_factory=origin)
        self.addCleanup(self.service.close)
        self.make_release()

    def make_release(self, counter=858, version="0.8.58"):
        output = io.BytesIO()
        content = b"MZ inert mock bytes" * 20
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("release/imperioaion.exe", content)
        archive = output.getvalue()
        now = datetime.now(timezone.utc)
        stamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = json.dumps(dict(schema=1, product="Imperio.AION", channel="pilot", version=version,
            release_counter=counter, not_before=stamp(now-timedelta(minutes=5)), expires=stamp(now+timedelta(days=2)),
            archive=dict(name="release.zip", prefix="release/", size=len(archive), sha256=hashlib.sha256(archive).hexdigest()),
            files=[dict(path="imperioaion.exe", size=len(content), sha256=hashlib.sha256(content).hexdigest())]), separators=(",", ":")).encode()
        envelope = lambda signature: json.dumps(dict(payload=base64.b64encode(raw).decode(),
            signature=base64.b64encode(signature).decode())).encode()
        self.transport.routes = {BASE+"release.manifest": envelope(self.key.sign(raw)),
            BASE+"release.install": envelope(b"x"*384), BASE+"release.zip": archive}

    def test_constructor_has_no_os_data_or_network_side_effect(self):
        self.assertFalse((self.root / "updater-data").exists())
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.service.snapshot()["phase"], "idle")

    def test_download_is_not_protected_install_and_never_calls_uac(self):
        result = self.service.download()
        self.assertEqual(result["phase"], "downloaded")
        self.assertEqual(result["installed_version"], "0.8.57")
        self.assertEqual(self.bridge.installs, 0)
        self.assertEqual(self.service.receipt()["version"], "0.8.58")
        self.assertNotIn("protected_install_confirmed", self.service.receipt())

    def test_progress_measures_real_signed_archive_bytes_only(self):
        self.service.download()
        progress = [event for event in self.events if event["type"] == "progress"]
        total = len(self.transport.routes[BASE+"release.zip"])
        self.assertGreater(len(progress), 0)
        self.assertEqual(progress[-1]["received_bytes"], total)
        self.assertEqual(progress[-1]["total_bytes"], total)
        self.assertEqual(progress[-1]["percent"], 100)
        self.assertTrue(all(event["phase"] == "downloading" for event in progress))
        self.assertTrue(all(event["percent"] is None for event in self.events if event["phase"] != "downloading"))
        self.assertTrue(all(url.startswith(BASE) for url in self.transport.calls))

    def test_cancel_stops_download_before_any_install_and_cleans_partial(self):
        self.transport.archive_hook = lambda: self.assertTrue(self.service.cancel_download())
        result = self.service.download()
        self.assertEqual(result["phase"], "cancelled")
        self.assertEqual(result["cancelled_operation"], "download")
        self.assertEqual(self.bridge.installs, 0)
        self.assertIsNone(self.service.receipt())
        self.assertEqual(list((self.root/"updater-data/download-code/.downloads").iterdir()), [])

    def test_cancel_not_allowed_outside_actual_download(self):
        self.assertFalse(self.service.cancel_download())
        self.service.download()
        self.assertFalse(self.service.cancel_download())

    def test_hud_running_blocks_install_and_retry_reinspects(self):
        self.service.download()
        self.bridge.running = True
        result = self.service.install()
        self.assertEqual(result["phase"], "blocked_running")
        self.assertEqual(self.bridge.installs, 0)
        self.bridge.running = False
        self.assertEqual(self.service.install()["phase"], "installed")

    def test_permission_decline_never_emits_installing_or_success(self):
        self.service.download()
        self.events.clear()
        self.bridge.cancel = True
        result = self.service.install()
        self.assertEqual(result["phase"], "cancelled")
        self.assertEqual(result["cancelled_operation"], "install")
        self.assertTrue(result["can_install"])
        self.assertEqual(self.bridge.installs, 0)
        self.assertIn("awaiting_permission", [e["phase"] for e in self.events])
        self.assertNotIn("installing", [e["phase"] for e in self.events])
        self.assertNotIn("installed", [e["phase"] for e in self.events])

    def test_success_requires_post_install_native_version_confirmation(self):
        self.service.download()
        self.bridge.confirm_wrong_version = True
        with self.assertRaisesRegex(UpdateError, "não confirmou"):
            self.service.install()
        self.assertEqual(self.service.snapshot()["phase"], "error")
        self.assertNotIn("protected_install_confirmed", self.service.receipt())

    def test_install_success_and_launch_are_distinct_truthful_states(self):
        self.service.download()
        result = self.service.install()
        self.assertEqual((result["installed_version"], result["previous_version"]), ("0.8.58", "0.8.57"))
        self.assertEqual(self.bridge.launches, 0)
        result = self.service.launch()
        self.assertEqual(result["phase"], "launched")
        self.assertIn("Solicitação", result["message"])
        self.assertEqual(self.bridge.launches, 1)

    def test_first_install_missing_and_corrupt_are_distinct(self):
        self.bridge.status = "missing"
        self.assertEqual(self.service.detect()["phase"], "missing")
        self.bridge.status = "corrupted"
        self.assertEqual(self.service.detect()["phase"], "corrupted")

    def test_closed_app_check_and_legacy_identity_guard_before_launch(self):
        with self.assertRaisesRegex(UpdateError, "0.8.58"):
            self.service.launch()
        self.bridge.counter = 858
        self.bridge.running = True
        self.assertEqual(self.service.launch()["phase"], "launched")
        self.assertEqual(self.bridge.launches, 0)

    def test_metadata_older_than_real_install_is_not_offered(self):
        self.bridge.counter, self.bridge.version = 859, "0.8.59"
        with self.assertRaisesRegex(UpdateError, "anterior"):
            self.service.check_updates()
        self.assertFalse(self.service.snapshot()["can_download"])

    def test_identical_current_version_is_up_to_date(self):
        self.bridge.counter, self.bridge.version = 858, "0.8.58"
        self.assertEqual(self.service.check_updates()["phase"], "up_to_date")

    def test_bad_native_signature_cannot_be_installed(self):
        self.bridge.fail_verify = True
        with self.assertRaises(UpdateError):
            self.service.download()
        self.assertFalse(self.service.snapshot()["can_install"])
        with self.assertRaisesRegex(UpdateError, "Confira|Baixe"):
            self.service.install()
        self.assertEqual(self.bridge.installs, 0)

    def test_recheck_discards_download_before_offering_another_version(self):
        self.service.download()
        archive = self.service._staged.archive_path
        self.make_release(counter=859, version="0.8.59")
        result = self.service.check_updates()
        self.assertEqual(result["available_version"], "0.8.59")
        self.assertFalse(result["can_install"])
        self.assertIsNone(self.service.receipt())
        self.assertFalse(archive.exists())
        with self.assertRaisesRegex(UpdateError, "Baixe"):
            self.service.install()
        self.assertEqual(self.bridge.installs, 0)

    def test_failed_recheck_cannot_reuse_stale_offer_or_stage(self):
        self.service.download()
        self.transport.routes[BASE+"release.manifest"] = b"invalid metadata"
        with self.assertRaises((UpdateError, VerificationError)):
            self.service.check_updates()
        result = self.service.snapshot()
        self.assertIsNone(result["available_version"])
        self.assertFalse(result["can_download"])
        self.assertFalse(result["can_install"])
        self.assertEqual(self.bridge.installs, 0)

    def test_release_stage_mismatch_is_refused_before_permission(self):
        self.service.download()
        self.make_release(counter=859, version="0.8.59")
        self.service._release = self.service._manager.check()
        self.events.clear()
        with self.assertRaisesRegex(UpdateError, "correspondem"):
            self.service.install()
        self.assertFalse(self.service.snapshot()["can_install"])
        self.assertNotIn("awaiting_permission", [e["phase"] for e in self.events])
        self.assertEqual(self.bridge.installs, 0)

    def test_changed_saved_rsa_envelope_is_refused_before_permission(self):
        self.service.download()
        self.service._installer_manifest.write_bytes(b"changed saved envelope")
        self.events.clear()
        with self.assertRaisesRegex(UpdateError, "manifesto.*mudou"):
            self.service.install()
        self.assertFalse(self.service.snapshot()["can_install"])
        self.assertNotIn("awaiting_permission", [e["phase"] for e in self.events])
        self.assertEqual(self.bridge.installs, 0)

    def test_changed_saved_zip_is_refused_before_permission(self):
        self.service.download()
        archive = self.service._staged.archive_path
        raw = bytearray(archive.read_bytes())
        raw[len(raw)//2] ^= 1
        archive.write_bytes(raw)
        self.events.clear()
        with self.assertRaises(VerificationError):
            self.service.install()
        self.assertFalse(self.service.snapshot()["can_install"])
        self.assertNotIn("awaiting_permission", [e["phase"] for e in self.events])
        self.assertEqual(self.bridge.installs, 0)

    def test_close_during_native_install_does_not_cancel_helper(self):
        self.service.download()
        self.bridge.on_install = lambda: self.assertFalse(self.service.close())
        self.assertEqual(self.service.install()["phase"], "installed")

    def test_launch_permission_cancel_is_not_reported_as_install_cancel(self):
        self.bridge.version, self.bridge.counter = "0.8.58", 858
        with patch.object(self.bridge, "launch", side_effect=PermissionDeclined("opening canceled")):
            result = self.service.launch()
        self.assertEqual(result["phase"], "cancelled")
        self.assertEqual(result["cancelled_operation"], "launch")
        self.assertTrue(result["can_launch"])

    def test_default_constructor_defers_tls_os_initialization(self):
        with patch("update_service._StandardTransport", side_effect=RuntimeError("too early")):
            service = UpdateService(self.root/"unused-data", self.root/"unused-feed", bridge=self.bridge,
                public_key_bytes=self.key.public_key().public_bytes_raw(), minimum_counter=857)
        self.assertFalse((self.root/"unused-data").exists())
        self.assertTrue(service.close())

    def test_existing_personal_sentinels_never_changed(self):
        personal = self.root / "synthetic-original-user/ImperioAION"
        personal.mkdir(parents=True)
        files = {"preferencias-hud.json": b'{"opacity":65}', "cache-personagens/test.json": b'{"schema":1}',
                 "exportacoes/saved.csv": b"inert old export\n"}
        for name, raw in files.items():
            path = personal/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.service.download()
        self.service.install()
        self.assertEqual({name:(personal/name).read_bytes() for name in files}, files)

    def test_safe_environment_excludes_runtime_injection_fields(self):
        with patch.dict(os.environ, {"COR_ENABLE_PROFILING":"1", "CORECLR_PROFILER":"x",
              "COMPLUS_Version":"x", "DOTNET_STARTUP_HOOKS":"x", "DEVPATH":"x", "PATH":"safe"}):
            clean = safe_environment()
            self.assertEqual(clean["PATH"], "safe")
            self.assertNotIn("COR_ENABLE_PROFILING", clean)
            self.assertNotIn("DOTNET_STARTUP_HOOKS", clean)
            self.assertNotIn("DEVPATH", clean)


if __name__ == "__main__":
    unittest.main(verbosity=2)
