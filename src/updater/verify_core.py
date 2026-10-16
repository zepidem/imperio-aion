# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only, offline release verifier for the Império AION security pilot.

No downloader, installer, extraction, process execution, elevation or key storage.
The caller supplies a TRUSTED Ed25519 public key and expected product/channel.
An attacker-editable key next to the package is not a trust anchor.

Envelope (strict JSON, UTF-8): {"payload": base64(raw_payload), "signature": base64}
Signature: Ed25519(raw_payload), using cryptography's maintained implementation.
Payload example (all properties required; unknown properties are rejected):
{
  "schema": 1, "product": "Imperio.AION", "channel": "pilot",
  "version": "0.8.56", "release_counter": 56,
  "not_before": "2026-10-15T00:00:00Z", "expires": "2026-11-15T00:00:00Z",
  "archive": {"name": "imperio-aion-v0.8.56.zip", "size": 123,
              "sha256": "64 lowercase hex characters", "prefix": "release/"},
  "files": [{"path": "imperioaion.exe", "size": 123, "sha256": "..."}]
}
archive.prefix is one directory plus slash, removed before comparing ZIP names.
files.path is relative to the extracted release root. ASCII-only portable Windows
names are intentional: all current release names comply. Reserved Windows names,
case aliases, special files, extra entries and ambiguous paths fail closed.

verify_directory may explicitly allow one marker file containing the EXACT already
verified envelope: metadata_files={"marker.manifest": envelope_bytes}. Its bytes
are checked too; no generic ignore list exists. Leave metadata_files absent for a
plain extracted ZIP. A VerifiedManifest is an immutable trusted result, not JSON
supplied directly to the archive/directory verifier.

This is a pilot, not TUF or protection against malware/admin replacing the verifier
or racing user-writable files. No read-only verifier can guarantee future execution
will observe unchanged bytes. Public updates need a trusted bootstrap, publisher
key custody, distribution configuration, atomic installation and rollback policy.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import struct
from typing import Mapping
import zipfile
import zlib


class VerificationError(ValueError):
    """Unauthenticated, malformed, changed or out-of-policy release; do not use it."""


@dataclass(frozen=True)
class Limits:
    max_metadata_bytes: int = 1024 * 1024
    max_archive_bytes: int = 200 * 1024 * 1024
    max_unpacked_bytes: int = 300 * 1024 * 1024
    max_file_bytes: int = 32 * 1024 * 1024
    max_entries: int = 2000
    max_compression_ratio: int = 100
    max_path_chars: int = 240
    chunk_size: int = 64 * 1024

    @property
    def max_envelope_bytes(self):
        return self.max_metadata_bytes * 2 + 4096


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ArchiveRecord:
    name: str
    size: int
    sha256: str
    prefix: str


@dataclass(frozen=True)
class RenewalPolicy:
    ed25519_public_key: str
    rsa_public_xml: str
    max_lifetime_seconds: int


@dataclass(frozen=True)
class VerifiedManifest:
    product: str
    channel: str
    version: str
    release_counter: int
    not_before: datetime
    expires: datetime
    archive: ArchiveRecord
    files: tuple[FileRecord, ...]
    envelope_sha256: str
    payload_sha256: str
    limits: Limits
    schema: int = 1
    renewal: RenewalPolicy | None = None


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
_RESERVED.update(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10))
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _fail(message):
    raise VerificationError(message)


def _integer(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail(f"{name}: inteiro fora do limite.")
    return value


def _hash(value, name):
    if type(value) is not str or not _HASH.fullmatch(value):
        _fail(f"{name}: SHA-256 inválido.")
    return value


def _keys(value, expected, name):
    if type(value) is not dict or set(value) != set(expected):
        _fail(f"{name}: propriedades inválidas.")


def _strict_json(raw, maximum, name):
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        _fail(f"{name}: tamanho inválido.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail(f"{name}: propriedade duplicada.")
            result[key] = value
        return result
    def constant(value):
        _fail(f"{name}: número não finito.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except VerificationError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise VerificationError(f"{name}: JSON inválido.") from exc


def _b64(value, maximum, name):
    if type(value) is not str or len(value) > maximum * 2:
        _fail(f"{name}: base64 inválido.")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise VerificationError(f"{name}: base64 inválido.") from exc
    if not result or len(result) > maximum:
        _fail(f"{name}: tamanho inválido.")
    return result


def _timestamp(value, name):
    if type(value) is not str or not _TIME.fullmatch(value):
        _fail(f"{name}: data UTC inválida.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise VerificationError(f"{name}: data UTC inválida.") from exc


def _portable_path(value, limits):
    if type(value) is not str or not value or len(value) > limits.max_path_chars:
        _fail("Caminho inválido ou longo demais.")
    if not value.isascii() or value.startswith("/") or "\\" in value:
        _fail("Caminho não portátil/absoluto.")
    parts = value.split("/")
    for part in parts:
        if (not part or part in (".", "..") or part[-1:] in (".", " ")
                or any(ord(ch) < 32 or ord(ch) == 127 or ch in '<>:"|?*' for ch in part)
                or part.partition(".")[0].rstrip(" ").upper() in _RESERVED):
            _fail("Caminho Windows inválido ou reservado.")
    return value


def _parents(name):
    parts = name.split("/")
    return {"/".join(parts[:i]) for i in range(1, len(parts))}


def _validate_paths(records, limits):
    names = set()
    files = set()
    spellings = {}
    for record in records:
        name = _portable_path(record.path, limits)
        folded = name.casefold()
        if folded in names:
            _fail("Manifesto contém nomes duplicados ou aliases de caixa.")
        names.add(folded)
        files.add(folded)
        for ancestor in _parents(name):
            prior = spellings.setdefault(ancestor.casefold(), ancestor)
            if prior != ancestor:
                _fail("Manifesto contém aliases de diretório.")
    if files & set(spellings):
        _fail("Um caminho é simultaneamente arquivo e diretório.")


def verify_envelope(envelope_bytes, public_key_bytes, *, product="Imperio.AION",
                    channel="pilot", now=None, minimum_counter=0, limits=Limits()):
    """Verify a bounded signed manifest; public_key_bytes must be a trusted 32B key."""
    _integer(minimum_counter, "minimum_counter", 0, 2**63 - 1)
    if type(public_key_bytes) is not bytes or len(public_key_bytes) != 32:
        _fail("A chave pública confiável deve ter 32 bytes.")
    envelope = _strict_json(envelope_bytes, limits.max_envelope_bytes, "Envelope")
    _keys(envelope, ("payload", "signature"), "Envelope")
    raw = _b64(envelope["payload"], limits.max_metadata_bytes, "Payload")
    signature = _b64(envelope["signature"], 64, "Assinatura")
    if len(signature) != 64:
        _fail("Assinatura Ed25519 inválida.")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, raw)
    except (InvalidSignature, ValueError) as exc:
        raise VerificationError("Assinatura não corresponde à chave pública confiável.") from exc
    data = _strict_json(raw, limits.max_metadata_bytes, "Manifesto")
    schema = data.get("schema") if type(data) is dict else None
    if type(schema) is not int or schema not in (1, 2):
        _fail("Schema de manifesto não suportado.")
    fields = ("schema", "product", "channel", "version", "release_counter",
              "not_before", "expires", "archive", "files")
    _keys(data, fields + (("renewal",) if schema == 2 else ()), "Manifesto")
    renewal = None
    if schema == 2:
        from renewal_core import parse_renewal_policy
        renewal = parse_renewal_policy(data["renewal"])
        if renewal.ed25519_public_key == public_key_bytes.hex():
            _fail("A chave online de renovação deve ser diferente da chave offline da versão.")
    if data["product"] != product or data["channel"] != channel:
        _fail("Produto/canal não autorizado.")
    version = data["version"]
    if type(version) is not str or len(version) > 64 or not _VERSION.fullmatch(version):
        _fail("Versão inválida.")
    counter = _integer(data["release_counter"], "release_counter", 1, 2**63 - 1)
    if counter < minimum_counter:
        _fail("Release anterior ao mínimo confiável (downgrade).")
    not_before = _timestamp(data["not_before"], "not_before")
    expires = _timestamp(data["expires"], "expires")
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _fail("Relógio de verificação deve ter fuso horário explícito.")
    if not not_before < expires or not not_before <= now < expires:
        _fail("Manifesto ainda não válido ou expirado.")
    if schema == 2 and (expires - not_before).total_seconds() > 365 * 86400:
        _fail("Autorização da versão excede 365 dias.")
    item = data["archive"]
    _keys(item, ("name", "size", "sha256", "prefix"), "Archive")
    name = _portable_path(item["name"], limits)
    if "/" in name or not name.lower().endswith(".zip"):
        _fail("Nome do ZIP inválido.")
    prefix = item["prefix"]
    if type(prefix) is not str or not prefix.endswith("/"):
        _fail("Prefixo do ZIP inválido.")
    prefix_name = _portable_path(prefix[:-1], limits)
    if "/" in prefix_name:
        _fail("Prefixo deve conter apenas uma pasta raiz.")
    archive = ArchiveRecord(name, _integer(item["size"], "archive.size", 1, limits.max_archive_bytes),
                            _hash(item["sha256"], "archive.sha256"), prefix)
    if type(data["files"]) is not list or not 1 <= len(data["files"]) <= limits.max_entries:
        _fail("Quantidade de arquivos fora do limite.")
    records = []
    total = 0
    for item in data["files"]:
        _keys(item, ("path", "size", "sha256"), "Arquivo")
        size = _integer(item["size"], "file.size", 0, limits.max_file_bytes)
        total += size
        if total > limits.max_unpacked_bytes:
            _fail("Tamanho total extraído fora do limite.")
        records.append(FileRecord(_portable_path(item["path"], limits), size,
                                  _hash(item["sha256"], "file.sha256")))
    _validate_paths(records, limits)
    return VerifiedManifest(product, channel, version, counter, not_before, expires,
                            archive, tuple(records), hashlib.sha256(envelope_bytes).hexdigest(),
                            hashlib.sha256(raw).hexdigest(), limits, schema, renewal)


def _special_file(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _plain_ancestors(path):
    """Use abspath, not resolve: resolution would hide the very links being rejected."""
    path = Path(os.path.abspath(os.fspath(path)))
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except OSError as exc:
            raise VerificationError("Arquivo/pasta ausente ou inacessível.") from exc
        if _special_file(info):
            _fail("Links, junctions e reparse points não são aceitos.")
    return path


def _identity(info):
    # Windows Python lstat/fstat can report different historical meanings for
    # st_ctime (creation vs change time). Birth time is explicit and consistent.
    # Device, file ID, size and mtime remain checked on both platforms.
    stable_time = getattr(info, "st_birthtime_ns", None) if os.name == "nt" else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stable_time)


def _checked_stream(stream, expected_size, expected_hash, limits, name):
    size = 0
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(limits.chunk_size)
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            _fail(f"{name}: tamanho real excede o valor autenticado.")
        digest.update(chunk)
    if size != expected_size or not hmac.compare_digest(digest.hexdigest(), expected_hash):
        _fail(f"{name}: arquivo ausente, corrompido ou alterado.")
    return size


def _zip_directory_preflight(stream, limits, archive_size):
    """Bound entry count/central-directory bytes before zipfile allocates its index.

    This small pilot intentionally rejects multi-volume and global ZIP64 archives.
    Its byte/entry limits are far below when those formats would be required.
    """
    tail_size = min(archive_size, 65535 + 22)
    stream.seek(archive_size - tail_size)
    tail = stream.read(tail_size)
    start = len(tail)
    record = None
    while True:
        start = tail.rfind(b"PK\x05\x06", 0, start)
        if start < 0:
            break
        if start + 22 <= len(tail):
            fields = struct.unpack_from("<4s4H2LH", tail, start)
            if start + 22 + fields[-1] == len(tail):
                record = fields
                break
    if record is None:
        _fail("ZIP sem diretório final válido.")
    _, disk, directory_disk, disk_count, count, directory_size, directory_offset, _ = record
    if (disk != 0 or directory_disk != 0 or disk_count != count
            or count in (0, 65535) or directory_size == 0xffffffff or directory_offset == 0xffffffff):
        _fail("ZIP64 global, arquivo vazio ou multivolume não suportado no piloto.")
    if count > limits.max_entries or directory_size > limits.max_metadata_bytes * 4:
        _fail("Diretório do ZIP excede os limites.")
    absolute_end = archive_size - tail_size + start
    if directory_offset + directory_size != absolute_end:
        _fail("Estrutura ZIP ambígua ou dados adicionais.")
    stream.seek(0)
    return count


def verify_zip(path, manifest):
    """Hash and inspect every archive entry in streaming mode, without extraction."""
    if not isinstance(manifest, VerifiedManifest):
        _fail("É necessário um manifesto autenticado.")
    limits = manifest.limits
    path = _plain_ancestors(path)
    try:
        original = path.lstat()
        if not stat.S_ISREG(original.st_mode) or original.st_size != manifest.archive.size:
            _fail("Tamanho/tipo do ZIP diferente do manifesto autenticado.")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if _identity(original) != _identity(before):
                _fail("ZIP mudou durante sua abertura.")
            _checked_stream(stream, manifest.archive.size, manifest.archive.sha256, limits, "ZIP")
            count = _zip_directory_preflight(stream, limits, manifest.archive.size)
            expected = {record.path: record for record in manifest.files}
            allowed_dirs = {manifest.archive.prefix[:-1]}
            for name in expected:
                allowed_dirs.update(manifest.archive.prefix + part for part in _parents(name))
            seen = set()
            files = set()
            total = 0
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if len(entries) != count or len(entries) > limits.max_entries:
                    _fail("Contagem ZIP inválida.")
                for entry in entries:
                    raw_name = entry.orig_filename
                    is_dir = entry.is_dir()
                    name = raw_name[:-1] if is_dir else raw_name
                    _portable_path(name, limits)
                    folded = name.casefold()
                    if folded in seen:
                        _fail("ZIP contém nomes duplicados ou aliases de caixa.")
                    seen.add(folded)
                    mode = (entry.external_attr >> 16) & 0xffff
                    kind = stat.S_IFMT(mode)
                    if (entry.flag_bits & 1 or entry.external_attr & _REPARSE
                            or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                            or kind == stat.S_IFDIR and not is_dir
                            or kind == stat.S_IFREG and is_dir):
                        _fail("ZIP criptografado ou entrada especial/link/reparse.")
                    if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        _fail("Método de compressão não permitido.")
                    if (entry.file_size > limits.max_file_bytes
                            or entry.file_size > max(entry.compress_size, 1) * limits.max_compression_ratio):
                        _fail("Expansão do ZIP fora do limite.")
                    if is_dir:
                        if name not in allowed_dirs or entry.file_size != 0:
                            _fail("Diretório ZIP não declarado ou com conteúdo.")
                        continue
                    if not raw_name.startswith(manifest.archive.prefix):
                        _fail("Arquivo fora da pasta raiz do ZIP.")
                    relative = raw_name[len(manifest.archive.prefix):]
                    if relative not in expected:
                        _fail("ZIP contém arquivo extra não autenticado.")
                    record = expected[relative]
                    if entry.file_size != record.size:
                        _fail("Tamanho declarado pelo ZIP não corresponde ao manifesto.")
                    with archive.open(entry, "r") as member:
                        total += _checked_stream(member, record.size, record.sha256, limits, relative)
                    if total > limits.max_unpacked_bytes:
                        _fail("Expansão total do ZIP fora do limite.")
                    files.add(relative)
            if files != set(expected):
                _fail("ZIP não contém todos os arquivos autenticados.")
            if _identity(before) != _identity(os.fstat(stream.fileno())) or _identity(before) != _identity(path.lstat()):
                _fail("ZIP mudou durante a verificação.")
    except VerificationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, ValueError, zlib.error) as exc:
        raise VerificationError("Não foi possível verificar o ZIP integralmente.") from exc
    return {"status": "PASS", "kind": "archive", "version": manifest.version,
            "release_counter": manifest.release_counter, "files_checked": len(files),
            "bytes_checked": total, "archive_sha256": manifest.archive.sha256,
            "extracted": False, "executed": False, "network_used": False}


def verify_directory(root, manifest, *, metadata_files: Mapping[str, bytes] | None = None):
    """Check an extracted release and explicit signed marker, without changing it."""
    if not isinstance(manifest, VerifiedManifest):
        _fail("É necessário um manifesto autenticado.")
    limits = manifest.limits
    expected = {record.path: record for record in manifest.files}
    metadata_files = {} if metadata_files is None else dict(metadata_files)
    if len(metadata_files) > 1:
        _fail("Só é permitido um marcador de manifesto autenticado.")
    for name, raw in metadata_files.items():
        _portable_path(name, limits)
        if "/" in name or name.casefold() in {p.casefold() for p in expected}:
            _fail("Nome do marcador inválido ou conflitante.")
        if (type(raw) is not bytes or len(raw) > limits.max_envelope_bytes
                or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), manifest.envelope_sha256)):
            _fail("Marcador deve conter exatamente o envelope autenticado.")
        expected[name] = FileRecord(name, len(raw), hashlib.sha256(raw).hexdigest())
    allowed_dirs = set()
    for name in expected:
        allowed_dirs.update(_parents(name))
    root = _plain_ancestors(root)
    try:
        root_info = root.lstat()
        if not stat.S_ISDIR(root_info.st_mode):
            _fail("Pasta raiz inválida.")
        files = set()
        seen = set()
        total = 0
        count = 0
        def walk(directory, prefix=""):
            nonlocal total, count
            with os.scandir(directory) as scan:
                for entry in scan:
                    count += 1
                    if count > limits.max_entries + len(allowed_dirs) + len(metadata_files):
                        _fail("A pasta contém entradas em excesso.")
                    name = prefix + entry.name
                    _portable_path(name, limits)
                    folded = name.casefold()
                    if folded in seen:
                        _fail("A pasta contém nomes duplicados/aliases de caixa.")
                    seen.add(folded)
                    # Windows DirEntry.stat can return zero st_dev/st_ino from
                    # FindFirstFile metadata. Fresh lstat supplies the actual file
                    # identity and does not follow links/reparse points.
                    info = os.lstat(entry.path)
                    if _special_file(info):
                        _fail("A pasta contém link, junction ou reparse point.")
                    if stat.S_ISDIR(info.st_mode):
                        if name not in allowed_dirs:
                            _fail("Diretório extra não autenticado.")
                        walk(Path(entry.path), name + "/")
                    elif stat.S_ISREG(info.st_mode):
                        if name not in expected:
                            _fail("Arquivo extra não autenticado.")
                        record = expected[name]
                        if info.st_size != record.size:
                            _fail("Arquivo local com tamanho divergente.")
                        with open(entry.path, "rb") as stream:
                            before = os.fstat(stream.fileno())
                            if _identity(info) != _identity(before):
                                _fail("Arquivo mudou durante sua abertura.")
                            total += _checked_stream(stream, record.size, record.sha256, limits, name)
                            if _identity(before) != _identity(os.fstat(stream.fileno())):
                                _fail("Arquivo mudou durante a leitura.")
                        after = os.lstat(entry.path)
                        if _special_file(after) or _identity(before) != _identity(after):
                            _fail("Arquivo mudou durante a verificação.")
                        files.add(name)
                        if total > limits.max_unpacked_bytes + limits.max_envelope_bytes:
                            _fail("Tamanho local total fora do limite.")
                    else:
                        _fail("Entrada local não é arquivo/diretório regular.")
        walk(root)
        if files != set(expected):
            _fail("A pasta não contém todos os arquivos autenticados.")
        after_root = root.lstat()
        if _special_file(after_root) or (root_info.st_dev, root_info.st_ino) != (after_root.st_dev, after_root.st_ino):
            _fail("A pasta raiz mudou durante a verificação.")
    except VerificationError:
        raise
    except (OSError, RuntimeError, RecursionError) as exc:
        raise VerificationError("Não foi possível verificar a pasta integralmente.") from exc
    return {"status": "PASS", "kind": "directory", "version": manifest.version,
            "release_counter": manifest.release_counter, "files_checked": len(files),
            "metadata_files_checked": len(metadata_files), "bytes_checked": total,
            "modified": False, "executed": False, "network_used": False}
