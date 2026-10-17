# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded signed-update transactions; no UI, privilege changes or app execution.

The trusted caller supplies code_root, SEPARATE state_root, a 32-byte Ed25519
public key and a minimum release counter. Production roots/state must have ACLs
that a normal user cannot change; this module does not create such a privilege
boundary. Its local test sandbox deliberately runs without elevation.

API:
  manager = UpdateManager(code_root, state_root, trusted_public_key,
      policy=UpdatePolicy(base_url='https://imperiobdo.com/aion2/releases/pilot/'),
      minimum_counter=857)
  release = manager.check()             # Signed metadata; no archive execution.
  staged = manager.stage(release)        # Download, verify, bounded safe extract.
  result = manager.commit(staged)        # Versioned rename + atomic state file.
  result = manager.update()              # The previous three operations.
  manager.current()                     # Reverify installed bytes; never launch.
  manager.rollback()                    # Verified prior install; counter retained.
  manager.download_installer_envelope(release) # RSA envelope or schema-2 freshness bundle.
  manager.validate_release(release)       # Repeat validity/HWM before native install.
  manager.discard_stage(staged)           # Explicit confined cleanup after helper.

Optional transport for tests: object.request(url, timeout=seconds) returns an
object with status, headers, read(size) and close(). Core, not transport, follows
and validates redirects. HTTP is rejected except an explicit allow_loopback_test
policy and a literal 127.0.0.1 / ::1 origin. There is no network fallback.

Legacy schema 1 remains supported. Schema 2 binds short-lived online freshness
to an immutable offline-signed root (see renewal_core). archive.name is a basename; the
configured release base and manifest_name, not remote data, determine the origin.
release.install is a second envelope for the native protected installer. Its raw
payload must match the authenticated Ed25519 payload, but ONLY the separate native
helper verifies that envelope's RSA signature and applies a protected install.
Freshness counters persist separately per root payload and are not cleared when
another root or a legacy release is examined. A network/expiry failure never
authorizes a new install; an already authenticated installed version can open.

Crash policy: a prepared transaction is conservatively aborted on recovery. The
old active pointer remains, and the highest accepted counter is never lowered.
Unreferenced version directories are preserved, never silently executed/deleted.
Signature expiry controls NEW acceptance. A previously installed local release
is reverified at its persisted acceptance time, so offline launch/recovery is not
disabled merely because publication metadata later expires. State protection is
therefore essential; it is not an unsigned attacker-controlled trust anchor.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

from verify_core import Limits, VerificationError, VerifiedManifest, verify_directory, verify_envelope, verify_zip
from verify_core import _identity, _plain_ancestors, _portable_path, _special_file, _strict_json, _timestamp
from renewal_core import MAX_FRESHNESS_BYTES, verify_freshness


class UpdateError(VerificationError):
    """Update refused or transaction failed; do not execute a candidate."""


@dataclass(frozen=True)
class UpdatePolicy:
    base_url: str
    channel: str = "pilot"
    product: str = "Imperio.AION"
    manifest_name: str = "release.manifest"
    allow_loopback_test: bool = False
    max_redirects: int = 3
    socket_timeout: float = 20.0
    metadata_deadline: float = 60.0
    archive_deadline: float = 1800.0
    limits: Limits = Limits()


@dataclass(frozen=True)
class Release:
    manifest: VerifiedManifest
    envelope: bytes
    archive_url: str
    freshness: bytes | None = None


@dataclass(frozen=True)
class StagedRelease:
    release: Release
    directory: Path
    owner_id: str
    archive_path: Path | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _StandardTransport:
    def __init__(self):
        # No environment proxy/redirect handlers changing the URL policy. TLS
        # still uses the standard certificate/hostname verification context.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def request(self, url, *, timeout):
        request = urllib.request.Request(url, headers={
            "User-Agent": "ImperioAion-Updater/0.4.2", "Accept-Encoding": "identity",
            "Cache-Control": "no-cache"}, method="GET")
        try:
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as response:
            return response


_ROOT_MARKER = ".imperiobdo-owner.json"
_MARKER = "release.manifest"
_STORE_ID = re.compile(r"[0-9a-f]{32}\Z")
_PAYLOAD_HASH = re.compile(r"[0-9a-f]{64}\Z")
_VERSION_DIR = re.compile(r"r[0-9]{20}-[0-9a-f]{16}\Z")
_PART_NAME = re.compile(r"pending-[0-9a-f]{32}\Z")


def _error(message):
    raise UpdateError(message)


def _utc_stamp(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain_file(path, *, maximum):
    path = _plain_ancestors(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        _error("Arquivo de controle inválido ou grande demais.")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if _identity(info) != _identity(before):
            _error("Arquivo de controle mudou durante abertura.")
        raw = stream.read(maximum + 1)
        if (len(raw) > maximum or _identity(before) != _identity(os.fstat(stream.fileno()))
                or _identity(before) != _identity(path.lstat())):
            _error("Arquivo de controle mudou durante leitura.")
    return raw


def _ensure_dir(path):
    """Check every existing lexical ancestor before creating any directory."""
    path = Path(os.path.abspath(os.fspath(path)))
    missing = []
    cursor = path
    while not os.path.lexists(cursor):
        missing.append(cursor)
        if cursor == cursor.parent:
            _error("Raiz de pasta inválida.")
        cursor = cursor.parent
    _plain_ancestors(cursor)
    if not cursor.is_dir():
        _error("Um ancestral não é pasta regular.")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _plain_ancestors(directory)
    _plain_ancestors(path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        _error("Esperada pasta regular.")
    return path


def _atomic_json(path, data):
    """Atomic same-directory pointer write; no partial JSON becomes state.json."""
    _plain_ancestors(path.parent)
    if os.path.lexists(path):
        _plain_ancestors(path)
        if not stat.S_ISREG(path.lstat().st_mode):
            _error("Destino de estado não é arquivo regular.")
    temporary = path.parent / (".state-" + uuid.uuid4().hex + ".tmp")
    raw = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Directory fsync is unavailable on Windows, whose atomic replacement is
        # still used. The journal permits conservative interruption recovery.
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_keys(data, keys, label):
    if type(data) is not dict or set(data) != set(keys):
        _error(label + ": propriedades inválidas.")


def _origin(parts):
    try:
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UpdateError("Porta de URL inválida.") from exc


class UpdateManager:
    def __init__(self, code_root, state_root, public_key_bytes, *, policy,
                 minimum_counter=1, transport=None, clock=None, monotonic=None):
        if not isinstance(policy, UpdatePolicy):
            _error("Política de atualização explícita obrigatória.")
        if type(public_key_bytes) is not bytes or len(public_key_bytes) != 32:
            _error("Chave pública confiável Ed25519 inválida.")
        if type(minimum_counter) is not int or not 1 <= minimum_counter < 2**63:
            _error("Contador mínimo confiável inválido.")
        if (type(policy.max_redirects) is not int or not 0 <= policy.max_redirects <= 3
                or not 0 < policy.socket_timeout <= 60
                or not 0 < policy.metadata_deadline <= 300
                or not 0 < policy.archive_deadline <= 1800):
            _error("Limites de rede inválidos.")
        self.code_root = Path(os.path.abspath(os.fspath(code_root)))
        self.state_root = Path(os.path.abspath(os.fspath(state_root)))
        if (self.code_root == self.state_root or self.code_root in self.state_root.parents
                or self.state_root in self.code_root.parents):
            _error("Estado e código precisam ficar em árvores separadas.")
        self.policy = policy
        self.public_key = public_key_bytes
        self.minimum_counter = minimum_counter
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self._base = urllib.parse.urlsplit(policy.base_url)
        self._validate_base()
        _portable_path(policy.manifest_name, policy.limits)
        if "/" in policy.manifest_name:
            _error("Nome do manifesto precisa ser basename.")
        self.transport = transport if transport is not None else _StandardTransport()
        self.versions = self.code_root / "versions"
        self.staging = self.code_root / ".staging"
        self.downloads = self.code_root / ".downloads"
        self.state_file = self.state_root / "state.json"
        self.freshness_state_file = self.state_root / "freshness-state.json"
        self.owner_id = None

    def _validate_base(self):
        parts = self._base
        if (type(self.policy.base_url) is not str or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or not parts.path.endswith("/")
                or "%" in parts.path or "\\" in parts.path or "//" in parts.path
                or any(p in (".", "..") for p in parts.path.split("/"))
                or not re.fullmatch(r"/[A-Za-z0-9/_-]*/", parts.path) and parts.path != "/"):
            _error("Origem/base de atualização inválida.")
        origin = _origin(parts)
        production = origin == ("https", "imperiobdo.com", 443)
        loopback = (self.policy.allow_loopback_test is True and parts.scheme == "http"
                    and parts.hostname in ("127.0.0.1", "::1") and 1 <= origin[2] <= 65535)
        if not production and not loopback:
            _error("Produção exige HTTPS em imperiobdo.com; HTTP loopback só no teste explícito.")

    def _url(self, basename):
        _portable_path(basename, self.policy.limits)
        if "/" in basename:
            _error("Somente basename é aceito para download.")
        # Even signed archive names are encoded as one path segment: '#' and '%'
        # never become URL syntax. The fixed base is never remote-controlled.
        url = self.policy.base_url + urllib.parse.quote(basename, safe="")
        self._validate_url(url)
        return url

    def _validate_url(self, url):
        if type(url) is not str or len(url) > 2048 or any(ord(c) < 32 for c in url):
            _error("URL inválida.")
        parts = urllib.parse.urlsplit(url)
        if (parts.username is not None or parts.password is not None or parts.query or parts.fragment
                or _origin(parts) != _origin(self._base) or not parts.path.startswith(self._base.path)
                or "\\" in parts.path):
            _error("Redirecionamento/download fora da origem autorizada.")
        relative = parts.path[len(self._base.path):]
        decoded = urllib.parse.unquote(relative, errors="strict")
        # Only a single encoded basename under the configured path is allowed.
        if "/" in decoded or "\\" in decoded or urllib.parse.unquote(decoded) != decoded:
            _error("URL com caminho codificado ambíguo.")
        _portable_path(decoded, self.policy.limits)
        return url

    def _fetch(self, url, *, maximum, deadline, target=None, expected_size=None, expected_hash=None):
        started = self.monotonic()
        redirects = 0
        while True:
            self._validate_url(url)
            if self.monotonic() - started > deadline:
                _error("Prazo total do download excedido.")
            try:
                response = self.transport.request(url, timeout=self.policy.socket_timeout)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                raise UpdateError("Não foi possível obter a atualização com TLS/rede válida.") from exc
            try:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                headers = {str(k).lower(): str(v).strip() for k, v in response.headers.items()}
                if any(len(k) + len(v) > 8192 for k, v in headers.items()):
                    _error("Cabeçalho HTTP grande demais.")
                if status in (301, 302, 303, 307, 308):
                    location = headers.get("location")
                    if redirects >= self.policy.max_redirects or not location:
                        _error("Redirecionamentos HTTP inválidos/excessivos.")
                    candidate = urllib.parse.urljoin(url, location)
                    self._validate_url(candidate)
                    redirects += 1
                    url = candidate
                    continue
                if status != 200:
                    _error("O servidor não retornou uma release completa (HTTP200).")
                if headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                    _error("Compressão HTTP não permitida para metadados/pacote.")
                if "content-range" in headers:
                    _error("Resposta HTTP parcial não permitida.")
                length = headers.get("content-length")
                if length is not None:
                    if not re.fullmatch(r"[0-9]{1,12}", length):
                        _error("Content-Length inválido.")
                    length = int(length)
                    if length > maximum or expected_size is not None and length != expected_size:
                        _error("Tamanho HTTP diferente do limite/autenticado.")
                chunks = [] if target is None else None
                count = 0
                digest = hashlib.sha256()
                while True:
                    if self.monotonic() - started > deadline:
                        _error("Prazo total do download excedido.")
                    try:
                        raw = response.read(min(self.policy.limits.chunk_size, maximum - count + 1))
                    except (OSError, ValueError) as exc:
                        raise UpdateError("Download interrompido durante leitura.") from exc
                    if not raw:
                        break
                    if type(raw) is not bytes:
                        _error("Transporte retornou conteúdo não binário.")
                    count += len(raw)
                    if count > maximum or expected_size is not None and count > expected_size:
                        _error("Download excedeu o limite/tamanho autenticado.")
                    digest.update(raw)
                    if target is None:
                        chunks.append(raw)
                    else:
                        target.write(raw)
                if (self.monotonic() - started > deadline or length is not None and count != length
                        or expected_size is not None and count != expected_size):
                    _error("Download incompleto ou fora do prazo.")
                if expected_hash is not None and not hmac.compare_digest(digest.hexdigest(), expected_hash):
                    _error("SHA-256 do download não corresponde ao manifesto autenticado.")
                return b"".join(chunks) if target is None else count
            finally:
                response.close()

    def _initialize(self):
        _ensure_dir(self.code_root)
        marker = self.code_root / _ROOT_MARKER
        if not os.path.lexists(marker):
            if any(self.code_root.iterdir()):
                _error("A pasta de código não está vazia nem pertence ao atualizador.")
            owner = uuid.uuid4().hex
            _atomic_json(marker, {"schema": 1, "product": self.policy.product, "store_id": owner})
        code_owner = _strict_json(_plain_file(marker, maximum=4096), 4096, "Propriedade código")
        _json_keys(code_owner, ("schema", "product", "store_id"), "Propriedade código")
        if (type(code_owner["schema"]) is not int or code_owner["schema"] != 1
                or code_owner["product"] != self.policy.product
                or type(code_owner["store_id"]) is not str or not _STORE_ID.fullmatch(code_owner["store_id"])):
            _error("Pasta de código com marcador inválido.")
        self.owner_id = code_owner["store_id"]
        _ensure_dir(self.state_root)
        state_marker = self.state_root / _ROOT_MARKER
        if not os.path.lexists(state_marker):
            if any(self.state_root.iterdir()):
                _error("A pasta de estado não pertence a esta instalação.")
            _atomic_json(state_marker, code_owner)
        if _strict_json(_plain_file(state_marker, maximum=4096), 4096, "Propriedade estado") != code_owner:
            _error("Estado e código não pertencem à mesma instalação.")
        for directory in (self.versions, self.staging, self.downloads):
            _ensure_dir(directory)

    @contextmanager
    def _locked(self):
        self._initialize()
        lock_path = self.state_root / "update.lock"
        if os.path.lexists(lock_path):
            _plain_ancestors(lock_path)
            if not stat.S_ISREG(lock_path.lstat().st_mode):
                _error("Lock de atualização inválido.")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise UpdateError("Outra atualização está em andamento.") from exc
            yield
        finally:
            if locked:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _empty_state(self):
        return {"schema": 1, "product": self.policy.product, "store_id": self.owner_id,
                "max_counter": 0, "highest_payload_sha256": None,
                "active": None, "previous": None, "pending": None}

    def _record_valid(self, record):
        _json_keys(record, ("directory", "version", "counter", "payload_sha256", "envelope_sha256", "accepted_at"),
                   "Release instalada")
        if (type(record["counter"]) is not int or not 1 <= record["counter"] < 2**63
                or type(record["directory"]) is not str or not _VERSION_DIR.fullmatch(record["directory"])
                or type(record["version"]) is not str or len(record["version"]) > 64
                or type(record["payload_sha256"]) is not str or not _PAYLOAD_HASH.fullmatch(record["payload_sha256"])
                or type(record["envelope_sha256"]) is not str or not _PAYLOAD_HASH.fullmatch(record["envelope_sha256"])
                or record["directory"] != f"r{record['counter']:020d}-{record['payload_sha256'][:16]}"):
            _error("Registro instalado inválido.")
        _timestamp(record["accepted_at"], "accepted_at")

    def _load_state(self):
        if not os.path.lexists(self.state_file):
            if any(self.versions.iterdir()):
                _error("Estado ausente com versões existentes; contador não pode ser reiniciado.")
            state = self._empty_state()
            self._save_state(state)
            return state
        state = _strict_json(_plain_file(self.state_file, maximum=65536), 65536, "Estado")
        _json_keys(state, self._empty_state(), "Estado")
        if (type(state["schema"]) is not int or state["schema"] != 1
                or state["product"] != self.policy.product or state["store_id"] != self.owner_id
                or type(state["max_counter"]) is not int or not 0 <= state["max_counter"] < 2**63):
            _error("Estado inválido; não reiniciar contador automaticamente.")
        highest = state["highest_payload_sha256"]
        if (state["max_counter"] == 0 and highest is not None
                or state["max_counter"] > 0 and (type(highest) is not str or not _PAYLOAD_HASH.fullmatch(highest))):
            _error("Marca de maior release inválida.")
        for key in ("active", "previous"):
            if state[key] is not None:
                self._record_valid(state[key])
                if state[key]["counter"] > state["max_counter"]:
                    _error("Ponteiro de release supera contador persistido.")
        if state["pending"] is not None:
            pending = state["pending"]
            _json_keys(pending, ("release", "stage"), "Transação pendente")
            self._record_valid(pending["release"])
            if (type(pending["stage"]) is not str or not _PART_NAME.fullmatch(pending["stage"])
                    or pending["release"]["counter"] > state["max_counter"]):
                _error("Transação pendente inválida.")
        return state

    def _save_state(self, state):
        _atomic_json(self.state_file, state)

    def _recover(self, state):
        if state["pending"] is not None:
            # No unacknowledged candidate is activated after an interrupted
            # commit. Retain high watermark and old active/previous pointers.
            state["pending"] = None
            self._save_state(state)
        return state

    def _authenticate(self, envelope, state, *, now=None):
        manifest = verify_envelope(envelope, self.public_key, product=self.policy.product,
            channel=self.policy.channel, now=self.clock() if now is None else now,
            minimum_counter=max(self.minimum_counter, state["max_counter"]), limits=self.policy.limits)
        if (manifest.release_counter == state["max_counter"]
                and manifest.payload_sha256 != state["highest_payload_sha256"]):
            _error("Mesmo contador foi reutilizado para conteúdo diferente.")
        if any(item.path.casefold() == _MARKER.casefold() for item in manifest.files):
            _error("Inventário usa o nome reservado ao manifesto externo da instalação.")
        remembered = self._load_freshness_state()["roots"]
        if any(record["release_counter"] > manifest.release_counter for record in remembered.values()):
            _error("Versão anterior à autorização renovável já observada.")
        if any(record["release_counter"] == manifest.release_counter and digest != manifest.payload_sha256
               for digest, record in remembered.items()):
            _error("Contador da versão foi reutilizado para autorização diferente.")
        return manifest

    def _load_freshness_state(self):
        if not os.path.lexists(self.freshness_state_file):
            return {"schema": 1, "product": self.policy.product, "store_id": self.owner_id, "roots": {}}
        value = _strict_json(_plain_file(self.freshness_state_file, maximum=512 * 1024),
                             512 * 1024, "Estado de renovação")
        _json_keys(value, ("schema", "product", "store_id", "roots"), "Estado de renovação")
        if (type(value["schema"]) is not int or value["schema"] != 1
                or value["product"] != self.policy.product or value["store_id"] != self.owner_id
                or type(value["roots"]) is not dict or len(value["roots"]) > 1024):
            _error("Estado de renovação inválido; não reiniciar contadores.")
        counters = set()
        for digest, record in value["roots"].items():
            _json_keys(record, ("release_counter", "renewal_counter", "payload_sha256"), "Raiz renovada")
            if (not _PAYLOAD_HASH.fullmatch(digest) or type(record["release_counter"]) is not int
                    or not 1 <= record["release_counter"] < 2**63
                    or record["release_counter"] in counters
                    or type(record["renewal_counter"]) is not int or not 1 <= record["renewal_counter"] < 2**63
                    or type(record["payload_sha256"]) is not str or not _PAYLOAD_HASH.fullmatch(record["payload_sha256"])):
                _error("Marca de renovação inválida.")
            counters.add(record["release_counter"])
        return value

    def _check_freshness(self, manifest, freshness):
        if manifest.schema == 1:
            if freshness is not None:
                _error("Manifesto legado não permite renovação adicional.")
            return None
        verified = verify_freshness(freshness, manifest, now=self.clock())
        state = self._load_freshness_state()
        prior = state["roots"].get(manifest.payload_sha256)
        if prior is not None and (verified.renewal_counter < prior["renewal_counter"]
                or verified.renewal_counter == prior["renewal_counter"]
                   and verified.payload_sha256 != prior["payload_sha256"]):
            _error("Renovação anterior ou contador reutilizado para conteúdo diferente.")
        if prior is None and len(state["roots"]) >= 1024:
            _error("Limite de histórico de renovação atingido; não descartar proteção automaticamente.")
        record = {"release_counter": manifest.release_counter, "renewal_counter": verified.renewal_counter,
                  "payload_sha256": verified.payload_sha256}
        if prior != record:
            state["roots"][manifest.payload_sha256] = record
            _atomic_json(self.freshness_state_file, state)
        return verified

    def _release_checked(self, release, state):
        if not isinstance(release, Release):
            _error("Release autenticada obrigatória.")
        manifest = self._authenticate(release.envelope, state)
        if manifest != release.manifest or release.archive_url != self._url(manifest.archive.name):
            _error("Release foi modificada desde sua verificação.")
        self._check_freshness(manifest, release.freshness)
        return manifest

    def validate_release(self, release):
        """Repeat time, scope and rollback checks immediately before installation."""
        with self._locked():
            state = self._recover(self._load_state())
            return self._release_checked(release, state)

    def check(self):
        with self._locked():
            state = self._recover(self._load_state())
            envelope = self._fetch(self._url(self.policy.manifest_name),
                maximum=self.policy.limits.max_envelope_bytes, deadline=self.policy.metadata_deadline)
            manifest = self._authenticate(envelope, state)
            freshness = None
            if manifest.schema == 2:
                freshness = self._fetch(self._url("freshness.json"), maximum=MAX_FRESHNESS_BYTES,
                                        deadline=self.policy.metadata_deadline)
            self._check_freshness(manifest, freshness)
            return Release(manifest, envelope, self._url(manifest.archive.name), freshness)

    def download_installer_envelope(self, release):
        """Return matching installer envelope; signature still requires native helper."""
        with self._locked():
            state = self._recover(self._load_state())
            self._release_checked(release, state)
            raw = self._fetch(self._url("release.install"), maximum=2 * 1024 * 1024,
                              deadline=self.policy.metadata_deadline)
            envelope = _strict_json(raw, 2 * 1024 * 1024, "Envelope do instalador")
            _json_keys(envelope, ("payload", "signature"), "Envelope do instalador")
            try:
                payload = base64.b64decode(envelope["payload"], validate=True)
                signature = base64.b64decode(envelope["signature"], validate=True)
                original = base64.b64decode(json.loads(release.envelope)["payload"], validate=True)
            except (ValueError, TypeError) as exc:
                raise UpdateError("Envelope do instalador inválido.") from exc
            if not 128 <= len(signature) <= 1024 or not hmac.compare_digest(payload, original):
                _error("Payload do instalador difere da release Ed25519 autenticada.")
            if release.manifest.schema == 2:
                return json.dumps({"release": envelope,
                    "freshness": _strict_json(release.freshness, MAX_FRESHNESS_BYTES, "Renovação")},
                    separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            return raw

    def _remove_owned(self, path, container):
        # Only UUID-named children created by this manager; never arbitrary input
        # or the container itself. Verify absolute target stays in managed scope.
        path = Path(os.path.abspath(os.fspath(path)))
        container = _plain_ancestors(container)
        if path.parent != container or not _PART_NAME.fullmatch(path.name):
            _error("Limpeza fora do staging/download pertencente ao atualizador.")
        if os.path.lexists(path):
            _plain_ancestors(path)
            if path.is_dir():
                # Reparse points in any descendant are refused, not followed.
                for parent, dirs, files in os.walk(path, followlinks=False):
                    for name in dirs + files:
                        if _special_file(os.lstat(Path(parent) / name)):
                            _error("Limpeza recusada: reparse/link no staging.")
                shutil.rmtree(path)
            else:
                path.unlink()

    def _extract(self, archive_path, destination, manifest):
        expected = {manifest.archive.prefix + item.path: item for item in manifest.files}
        allowed_dirs = {manifest.archive.prefix}
        for name in expected:
            pieces = name.split("/")
            allowed_dirs.update("/".join(pieces[:i]) + "/" for i in range(1, len(pieces)))
        seen = set()
        total = 0
        with zipfile.ZipFile(archive_path) as archive:
            if len(archive.infolist()) > manifest.limits.max_entries:
                _error("ZIP contém entradas em excesso.")
            for entry in archive.infolist():
                if entry.is_dir():
                    if entry.orig_filename not in allowed_dirs:
                        _error("Diretório de ZIP fora do inventário.")
                    continue
                record = expected.get(entry.orig_filename)
                if record is None or entry.orig_filename in seen:
                    _error("Arquivo ZIP extra/duplicado durante extração.")
                seen.add(entry.orig_filename)
                _portable_path(record.path, manifest.limits)
                target = destination / Path(record.path)
                if destination not in target.parents:
                    _error("Extração fora do staging.")
                _ensure_dir(target.parent)
                if os.path.lexists(target):
                    _error("Extração não sobrescreve arquivos existentes.")
                digest = hashlib.sha256()
                size = 0
                with archive.open(entry) as source, target.open("xb") as output:
                    while True:
                        raw = source.read(manifest.limits.chunk_size)
                        if not raw:
                            break
                        size += len(raw)
                        total += len(raw)
                        if size > record.size or total > manifest.limits.max_unpacked_bytes:
                            _error("Expansão real excedeu limites autenticados.")
                        digest.update(raw)
                        output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                if size != record.size or digest.hexdigest() != record.sha256:
                    _error("Conteúdo extraído diferente do inventário autenticado.")
        if seen != set(expected):
            _error("Extração ficou incompleta.")

    def stage(self, release, *, keep_archive=False):
        """Stage signed bytes; optionally retain the original ZIP for native helper.

        A retained archive remains untrusted until the independent helper verifies
        its signature/size/hash. Use discard_archive after that helper finishes.
        """
        if type(keep_archive) is not bool:
            _error("keep_archive deve ser booleano explícito.")
        with self._locked():
            state = self._recover(self._load_state())
            manifest = self._release_checked(release, state)
            download = self.downloads / ("pending-" + uuid.uuid4().hex)
            destination = self.staging / ("pending-" + uuid.uuid4().hex)
            completed = False
            try:
                with download.open("xb") as target:
                    self._fetch(release.archive_url, maximum=manifest.limits.max_archive_bytes,
                        deadline=self.policy.archive_deadline, target=target,
                        expected_size=manifest.archive.size, expected_hash=manifest.archive.sha256)
                    target.flush()
                    os.fsync(target.fileno())
                verify_zip(download, manifest)
                destination.mkdir(mode=0o700)
                self._extract(download, destination, manifest)
                with (destination / _MARKER).open("xb") as marker:
                    marker.write(release.envelope)
                    marker.flush()
                    os.fsync(marker.fileno())
                verify_directory(destination, manifest, metadata_files={_MARKER: release.envelope})
                # Also catches a replaced ZIP between initial verification and
                # extraction. Every extracted byte was independently hashed.
                verify_zip(download, manifest)
                completed = True
                return StagedRelease(release, destination, self.owner_id,
                                     download if keep_archive else None)
            except Exception:
                if destination.exists():
                    self._remove_owned(destination, self.staging)
                raise
            finally:
                if download.exists() and not (completed and keep_archive):
                    self._remove_owned(download, self.downloads)

    def discard_archive(self, staged):
        """Remove only this manager's UUID-named retained original archive."""
        with self._locked():
            if not isinstance(staged, StagedRelease) or staged.owner_id != self.owner_id:
                _error("Archive não pertence a esta instalação.")
            if staged.archive_path is not None:
                self._remove_owned(staged.archive_path, self.downloads)

    def discard_stage(self, staged):
        """Explicitly discard a local candidate after/canceling a native install.

        Already committed version directories are outside staging and never
        removed by this method. It does not delete an installed app or settings.
        """
        with self._locked():
            if not isinstance(staged, StagedRelease) or staged.owner_id != self.owner_id:
                _error("Staging não pertence a esta instalação.")
            self._remove_owned(staged.directory, self.staging)
            if staged.archive_path is not None:
                self._remove_owned(staged.archive_path, self.downloads)

    def _installed_record(self, manifest, accepted):
        return {"directory": f"r{manifest.release_counter:020d}-{manifest.payload_sha256[:16]}",
                "version": manifest.version, "counter": manifest.release_counter,
                "payload_sha256": manifest.payload_sha256, "envelope_sha256": manifest.envelope_sha256,
                "accepted_at": _utc_stamp(accepted)}

    def _verify_installed(self, record):
        self._record_valid(record)
        directory = self.versions / record["directory"]
        _plain_ancestors(directory)
        envelope = _plain_file(directory / _MARKER, maximum=self.policy.limits.max_envelope_bytes)
        if hashlib.sha256(envelope).hexdigest() != record["envelope_sha256"]:
            _error("Marcador da versão instalada mudou.")
        manifest = verify_envelope(envelope, self.public_key, product=self.policy.product,
            channel=self.policy.channel, minimum_counter=0,
            now=_timestamp(record["accepted_at"], "accepted_at"), limits=self.policy.limits)
        if (manifest.release_counter != record["counter"] or manifest.version != record["version"]
                or manifest.payload_sha256 != record["payload_sha256"]):
            _error("Estado e manifesto instalado divergem.")
        verify_directory(directory, manifest, metadata_files={_MARKER: envelope})
        return manifest, directory

    def commit(self, staged):
        with self._locked():
            state = self._recover(self._load_state())
            if (not isinstance(staged, StagedRelease) or staged.owner_id != self.owner_id
                    or Path(os.path.abspath(staged.directory)).parent != self.staging
                    or not _PART_NAME.fullmatch(staged.directory.name)):
                _error("Staging não pertence a esta instalação.")
            manifest = self._release_checked(staged.release, state)
            _plain_ancestors(staged.directory)
            verify_directory(staged.directory, manifest, metadata_files={_MARKER: staged.release.envelope})
            accepted = self.clock()
            record = self._installed_record(manifest, accepted)
            destination = self.versions / record["directory"]
            if (state["active"] is not None
                    and state["active"]["counter"] == manifest.release_counter
                    and state["active"]["payload_sha256"] == manifest.payload_sha256):
                self._verify_installed(state["active"])
                self._remove_owned(staged.directory, self.staging)
                return {"status": "CURRENT", "version": manifest.version, "directory": str(destination),
                        "counter": manifest.release_counter, "executed": False}
            # Validate any existing immutable version before deciding to reuse it.
            existing = os.path.lexists(destination)
            if existing:
                _plain_ancestors(destination)
                verify_directory(destination, manifest, metadata_files={_MARKER: staged.release.envelope})
            before_active = state["active"]
            if manifest.release_counter > state["max_counter"]:
                state["max_counter"] = manifest.release_counter
                state["highest_payload_sha256"] = manifest.payload_sha256
            state["pending"] = {"release": record, "stage": staged.directory.name}
            # Persist the watermark and old selection BEFORE touching the final
            # version. Even a failed commit must not enable a remote downgrade.
            self._save_state(state)
            try:
                if not existing:
                    os.rename(staged.directory, destination)
                self._verify_installed(record)
                committed = dict(state, active=record,
                    previous=before_active if before_active != record else state["previous"], pending=None)
                self._save_state(committed)
            except Exception as exc:
                # The old active pointer was never modified in the prepared
                # state. If storage is still failing, next startup recovers that
                # journal while retaining the newer watermark.
                state["pending"] = None
                try:
                    self._save_state(state)
                except OSError:
                    pass
                raise UpdateError("Atualização não ativada; seleção anterior preservada.") from exc
            if existing:
                self._remove_owned(staged.directory, self.staging)
            return {"status": "INSTALLED", "version": manifest.version,
                    "directory": str(destination), "counter": manifest.release_counter,
                    "previous_retained": before_active is not None,
                    "executed": False, "elevated": False}

    def update(self):
        return self.commit(self.stage(self.check()))

    def current(self):
        with self._locked():
            state = self._recover(self._load_state())
            if state["active"] is None:
                return None
            manifest, directory = self._verify_installed(state["active"])
            return {"status": "VERIFIED", "version": manifest.version,
                    "counter": manifest.release_counter, "max_counter": state["max_counter"],
                    "directory": str(directory), "executed": False}

    def rollback(self):
        with self._locked():
            state = self._recover(self._load_state())
            if state["previous"] is None:
                _error("Não há versão anterior verificada para recuperar.")
            manifest, directory = self._verify_installed(state["previous"])
            state["active"], state["previous"] = state["previous"], state["active"]
            self._save_state(state)
            return {"status": "ROLLED_BACK", "version": manifest.version,
                    "counter": manifest.release_counter, "max_counter": state["max_counter"],
                    "directory": str(directory), "executed": False}
