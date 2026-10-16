# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Stateful updater backend for the v0.3 UI; no widget or JSON presentation code.

All operations are synchronous and belong in the UI's worker. on_event receives
plain snapshot dictionaries. Importing/constructing the service does no OS query,
network request or personal-data access. The default feed is fixed HTTPS on imperiobdo.com. Local loopback feeds require
an explicit test argument and never act as a fallback for the public feed.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import threading
import urllib.parse
import uuid

from update_core import UpdateError, UpdateManager, UpdatePolicy, _StandardTransport, _plain_file
from verify_core import VerificationError, _plain_ancestors, verify_envelope, verify_zip
from windows_bridge import NativeBridge, PermissionDeclined
from renewal_core import MAX_FRESHNESS_BYTES, verify_freshness


PUBLIC_FEED = "https://imperiobdo.com/aion2/releases/v2/"


class DownloadCancelled(RuntimeError):
    pass


@contextmanager
def local_origin(directory, public_key_bytes, minimum_counter):
    """Temporary loopback feed with precisely three signed-release resources."""
    directory = Path(os.path.abspath(directory))
    _plain_ancestors(directory)
    with (directory / "release.manifest").open("rb") as stream:
        envelope = stream.read(2097153)
    if len(envelope) > 2097152:
        raise UpdateError("O manifesto local excede o limite.")
    manifest = verify_envelope(envelope, public_key_bytes, minimum_counter=minimum_counter)
    allowed = {"/release.manifest": directory / "release.manifest",
               "/release.install": directory / "release.install",
               "/" + urllib.parse.quote(manifest.archive.name, safe=""): directory / manifest.archive.name}
    if manifest.schema == 2:
        freshness = _plain_file(directory / "freshness.json", maximum=MAX_FRESHNESS_BYTES)
        verify_freshness(freshness, manifest)
        allowed["/freshness.json"] = directory / "freshness.json"
    for path in allowed.values():
        _plain_ancestors(path)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in allowed:
                self.send_error(404)
                return
            try:
                path = allowed[self.path]
                _plain_ancestors(path)
                with path.open("rb") as stream:
                    self.send_response(200)
                    self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    while raw := stream.read(65536):
                        self.wfile.write(raw)
            except (OSError, ConnectionError, VerificationError):
                return
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


class _ProgressResponse:
    def __init__(self, response, owner):
        self.response = response
        self.owner = owner
        self.status = response.status
        self.headers = response.headers
    def read(self, size):
        if self.owner.enabled and self.owner.cancel.is_set():
            raise DownloadCancelled("O download foi cancelado. A instalação anterior permanece ativa.")
        raw = self.response.read(size)
        if self.owner.enabled and raw:
            self.owner.received += len(raw)
            if self.owner.received <= self.owner.total:
                self.owner.notify(self.owner.received, self.owner.total)
        return raw
    def close(self):
        self.response.close()


class _ProgressTransport:
    def __init__(self, transport, cancel, notify):
        self.transport = transport
        self.cancel = cancel
        self.notify = notify
        self.enabled = False
        self.total = 0
        self.received = 0
    def request(self, url, *, timeout):
        if self.enabled and self.cancel.is_set():
            raise DownloadCancelled("O download foi cancelado.")
        if self.transport is None:
            self.transport = _StandardTransport()
        return _ProgressResponse(self.transport.request(url, timeout=timeout), self)


class UpdateService:
    def __init__(self, data_root, local_feed=None, *, resources_root=None, on_event=None,
                 bridge=None, public_key_bytes=None, minimum_counter=None,
                 manager_factory=UpdateManager, transport=None, origin_factory=local_origin):
        if public_key_bytes is None or minimum_counter is None:
            from updater_trust import PUBLIC_KEY, MINIMUM_COUNTER
            public_key_bytes = PUBLIC_KEY if public_key_bytes is None else public_key_bytes
            minimum_counter = MINIMUM_COUNTER if minimum_counter is None else minimum_counter
        self.data_root = Path(os.path.abspath(data_root))
        self.local_feed = None if local_feed is None else Path(os.path.abspath(local_feed))
        self.on_event = on_event or (lambda event: None)
        self.bridge = bridge or NativeBridge(resources_root, on_notice=self._notice)
        self.public_key = public_key_bytes
        self.minimum_counter = minimum_counter
        self.manager_factory = manager_factory
        self.origin_factory = origin_factory
        self.transport = _ProgressTransport(transport, threading.Event(), self._progress)
        self._feed_context = None
        self._manager = None
        self._release = None
        self._staged = None
        self._installer_manifest = None
        self._installer_manifest_sha256 = None
        self._receipt = None
        self._download_verified = False
        self._installed = None
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._closed = False
        self._state = dict(phase="idle", message="Pronto para verificar a atualização.",
            installed_version=None, available_version=None, running=False,
            received_bytes=0, total_bytes=0, percent=None, channel="site" if self.local_feed is None else "local", error=None,
            install_directory=None, previous_version=None, cancelled_operation=None,
            preservation="Os arquivos do programa e os dados da sua conta ficam separados.")

    def snapshot(self):
        with self._lock:
            state = dict(self._state)
            busy = state["phase"] in ("detecting", "checking", "downloading", "verifying",
                                      "awaiting_permission", "installing", "launching")
            state.update(can_check=not busy and not self._closed,
                can_download=not busy and self._release is not None and state["phase"] != "up_to_date" and not self._closed,
                can_install=not busy and self._staged is not None and self._download_verified and not state["running"] and not self._closed,
                can_launch=not busy and self._installed is not None and self._installed.get("launch_supported", False)
                    and self._installed.get("authenticated", False) and not state["running"]
                    and self._installed.get("status") == "installed" and not self._closed,
                can_cancel=state["phase"] == "downloading" and not self._closed)
            return state

    def _emit(self, kind="state", **changes):
        with self._lock:
            self._state.update(changes)
            if self._state["phase"] != "downloading":
                self._state["percent"] = None
            event = dict(type=kind, **self.snapshot())
        self.on_event(event)
        return self.snapshot()

    def _notice(self, message):
        return self._emit("notice", message=message)

    def _progress(self, received, total):
        if received == total:
            self._emit("progress", received_bytes=received, total_bytes=total,
                percent=100, message="Download recebido. Conferindo os arquivos…")
            self._emit(phase="verifying", message="Conferindo a assinatura e todos os arquivos…")
        else:
            self._emit("progress", received_bytes=received, total_bytes=total,
                percent=int(received * 100 / total), message="Baixando a atualização…")

    @contextmanager
    def _action(self, operation=None):
        if self._closed or not self._operation.acquire(blocking=False):
            raise UpdateError("Já existe uma operação em andamento ou o atualizador foi fechado.")
        try:
            yield
        except DownloadCancelled as error:
            self._emit(phase="cancelled", message=str(error), error=None, cancelled_operation="download")
        except PermissionDeclined as error:
            self._emit(phase="cancelled", message=str(error), error=None, cancelled_operation=operation or "install")
        except Exception as error:
            offline_launch = (operation == "check" and self._installed is not None
                and self._installed.get("status") == "installed" and self._installed.get("authenticated", False))
            self._emit(phase="offline" if offline_launch else "error",
                message=("Não foi possível verificar atualizações. Você pode abrir a versão instalada e autenticada."
                         if offline_launch else str(error)), error=str(error))
            raise
        finally:
            self._operation.release()

    def _inspect(self):
        value = self.bridge.inspect_installed()
        self._installed = value
        self._emit(installed_version=value.get("version"), running=value.get("running", True))
        return value

    def detect(self):
        with self._action():
            self._emit(phase="detecting", message="Conferindo a instalação atual…", error=None)
            value = self._inspect()
            if value["status"] == "corrupted":
                self._emit(phase="corrupted", message="A instalação atual precisa ser reparada antes de abrir.", error=value.get("reason"))
            elif value["status"] == "missing":
                self._emit(phase="missing", message="O Império AION ainda não está instalado.")
            else:
                self._emit(phase="idle", message="Versão instalada conferida.")
        return self.snapshot()

    def _ensure_manager(self):
        if self._manager is None:
            if self.local_feed is None:
                # No environment variable, adjacent file or remote metadata may
                # replace the trusted HTTPS origin or enable loopback implicitly.
                self._manager = self.manager_factory(self.data_root / "download-code", self.data_root / "download-state",
                    self.public_key, policy=UpdatePolicy(base_url=PUBLIC_FEED),
                    minimum_counter=self.minimum_counter, transport=self.transport)
            else:
                self._feed_context = self.origin_factory(self.local_feed, self.public_key, self.minimum_counter)
                base = self._feed_context.__enter__()
                try:
                    self._manager = self.manager_factory(self.data_root / "download-code", self.data_root / "download-state",
                        self.public_key, policy=UpdatePolicy(base_url=base, allow_loopback_test=True),
                        minimum_counter=self.minimum_counter, transport=self.transport)
                except Exception:
                    self._feed_context.__exit__(*__import__("sys").exc_info())
                    self._feed_context = None
                    raise
        return self._manager

    def check_updates(self):
        with self._action("check"):
            # A new check starts a new offer. Never pair its signed version with
            # a previously downloaded archive, including after a failed check.
            self._release = None
            self._discard_candidate()
            self._emit(phase="checking", message="Verificando a versão disponível…", error=None,
                available_version=None, received_bytes=0, total_bytes=0, cancelled_operation=None)
            installed = self._inspect()
            manager = self._ensure_manager()
            release = manager.check()
            counter = installed.get("counter")
            if type(counter) is int and release.manifest.release_counter < counter:
                raise UpdateError("O canal de atualização oferece uma versão anterior à instalada. Nenhum arquivo foi alterado.")
            self._release = release
            self._emit(available_version=release.manifest.version)
            if installed["status"] == "installed" and release.manifest.release_counter == counter:
                self._emit(phase="up_to_date", message="Seu aplicativo já está atualizado.")
            else:
                self._emit(phase="ready", message="Atualização disponível." if installed["status"] == "installed" else "Versão disponível para instalação.")
        return self.snapshot()

    def _discard_candidate(self):
        staged = self._staged
        self._staged = None
        self._installer_manifest = None
        self._installer_manifest_sha256 = None
        self._download_verified = False
        self._receipt = None
        if staged is not None:
            self._manager.discard_stage(staged)

    def download(self):
        if self._release is None:
            self.check_updates()
        with self._action():
            if self._release is None:
                raise UpdateError("Verifique a versão disponível antes de baixar.")
            self._discard_candidate()
            self.transport.cancel.clear()
            self._download_verified = False
            self._receipt = None
            self.transport.received = 0
            self.transport.total = self._release.manifest.archive.size
            self.transport.enabled = True
            self._emit(phase="downloading", message="Baixando a atualização…", received_bytes=0,
                total_bytes=self.transport.total, percent=0, error=None, cancelled_operation=None)
            try:
                staged = self._manager.stage(self._release, keep_archive=True)
                self._staged = staged
            finally:
                self.transport.enabled = False
            self._emit(phase="verifying", message="Repetindo a verificação no instalador…")
            raw = self._manager.download_installer_envelope(self._release)
            metadata = self.data_root / ("installer-" + uuid.uuid4().hex + ".install")
            with metadata.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._installer_manifest = metadata
            self._installer_manifest_sha256 = hashlib.sha256(raw).hexdigest()
            proof = self.bridge.verify_archive(staged.archive_path, metadata)
            self._download_verified = True
            self._receipt = dict(version=self._release.manifest.version, counter=self._release.manifest.release_counter,
                archive=str(staged.archive_path), installer_manifest=str(metadata), native_verification=proof,
                local_test=self.local_feed is not None, executed=False, elevated=False)
            self._emit(phase="downloaded", message="Arquivos prontos para atualizar. Seus dados pessoais ficam na mesma pasta.")
        return self.snapshot()

    def cancel_download(self):
        with self._lock:
            if self._state["phase"] != "downloading":
                return False
            self.transport.cancel.set()
            return True

    def install(self):
        with self._action("install"):
            if self._staged is None or self._installer_manifest is None or not self._download_verified:
                raise UpdateError("Baixe e confira a atualização antes de instalar.")
            self._download_verified = False
            if self._release is None or self._staged.release != self._release:
                raise UpdateError("Os arquivos baixados não correspondem à versão anunciada. Baixe novamente.")
            installed = self._inspect()
            if installed.get("running", True):
                self._download_verified = True
                self._emit(phase="blocked_running", message="Feche o Império AION pelo X da HUD e tente novamente.")
                return self.snapshot()
            # Reverify immutable download and native metadata immediately before
            # asking for Windows permission; native helper repeats independently.
            if self._release.manifest.release_counter < (installed.get("counter") or 0):
                raise UpdateError("A versão instalada é mais recente. Nenhum downgrade foi autorizado.")
            self._manager.validate_release(self._release)
            verify_zip(self._staged.archive_path, self._release.manifest)
            installer_bytes = _plain_file(self._installer_manifest, maximum=2 * 1024 * 1024)
            if hashlib.sha256(installer_bytes).hexdigest() != self._installer_manifest_sha256:
                raise UpdateError("O manifesto do instalador mudou desde o download. Baixe novamente.")
            self.bridge.verify_archive(self._staged.archive_path, self._installer_manifest)
            self._download_verified = True
            self._emit(phase="awaiting_permission", message="Autorize a instalação na janela do Windows.")
            self.bridge.install(self._staged.archive_path, self._installer_manifest,
                on_started=lambda: self._emit(phase="installing", message="O Windows está instalando a atualização. Aguarde…"))
            confirmed = self._inspect()
            if (confirmed["status"] != "installed" or not confirmed.get("authenticated")
                    or confirmed.get("counter") != self._release.manifest.release_counter
                    or confirmed.get("version") != self._release.manifest.version):
                raise UpdateError("O Windows ainda não confirmou a nova versão instalada. Nenhum sucesso foi presumido.")
            if self._receipt is not None:
                self._receipt["protected_install_confirmed"] = True
            self._manager.discard_stage(self._staged)
            self._staged = None
            self._emit(phase="installed", previous_version=installed.get("version"),
                message="Atualização instalada e conferida. A pasta de dados do aplicativo foi mantida.")
        return self.snapshot()

    def launch(self, *, background=False):
        with self._action("launch"):
            self._emit(phase="launching", message="Conferindo o aplicativo antes de abrir…", error=None)
            installed = self._inspect()
            if installed["status"] != "installed" or not installed.get("authenticated", False):
                raise UpdateError("Instale ou repare o aplicativo antes de abrir.")
            if installed.get("running", True):
                self._emit(phase="launched", message="O Império AION já está aberto.")
                return self.snapshot()
            if not installed.get("launch_supported"):
                raise UpdateError("Atualize para 0.8.58 para continuar com este atualizador.")
            if background:self.bridge.launch_background()
            else:self.bridge.launch()
            self._emit(phase="launched", message="Solicitação de abertura enviada ao Windows. Se solicitado, autorize com a mesma conta.")
        return self.snapshot()

    def receipt(self):
        """CLI/test evidence only; UI should render the human-readable snapshot."""
        return None if self._receipt is None else dict(self._receipt)

    def close(self):
        with self._lock:
            if self._operation.locked():
                if self._state["phase"] == "downloading":
                    self.transport.cancel.set()
                return False
            self._closed = True
        self._discard_candidate()
        if self._feed_context is not None:
            self._feed_context.__exit__(None, None, None)
            self._feed_context = None
        return True
