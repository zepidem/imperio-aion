# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Strict, release-scoped freshness verification; no network, keys or filesystem.

Root authorization is immutable and offline-signed. An online key can only renew
freshness for its exact root payload, never change files, version or permissions.
Python verifies Ed25519; the native helper independently verifies RSA-3072.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import xml.etree.ElementTree as ET

from verify_core import (RenewalPolicy, VerificationError, _b64, _hash, _integer,
                         _keys, _strict_json, _timestamp)

MAX_FRESHNESS_BYTES = 16384


def rsa_public_key(xml):
    if type(xml) is not str or not 1 <= len(xml) <= 4096 or "<!" in xml:
        raise VerificationError("Chave RSA de renovação inválida.")
    try:
        root = ET.fromstring(xml)
        if (root.tag != "RSAKeyValue" or root.attrib or (root.text or "").strip()
                or [child.tag for child in root] != ["Modulus", "Exponent"]
                or any(child.attrib or len(child) or (child.tail or "").strip() for child in root)):
            raise ValueError("Estrutura inválida")
        modulus = _b64(root[0].text, 384, "Modulus")
        exponent = _b64(root[1].text, 8, "Exponent")
        if len(modulus) != 384 or int.from_bytes(modulus, "big").bit_length() != 3072 or exponent != b"\x01\x00\x01":
            raise ValueError("RSA-3072/65537 obrigatório")
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        return RSAPublicNumbers(65537, int.from_bytes(modulus, "big")).public_key()
    except (ValueError, ET.ParseError, TypeError) as error:
        raise VerificationError("Chave RSA de renovação inválida.") from error


def parse_renewal_policy(data):
    _keys(data, ("ed25519_public_key", "rsa_public_xml", "max_lifetime_seconds"), "Política de renovação")
    key = _hash(data["ed25519_public_key"], "ed25519_public_key")
    rsa_public_key(data["rsa_public_xml"])
    lifetime = _integer(data["max_lifetime_seconds"], "max_lifetime_seconds", 1, 2592000)
    return RenewalPolicy(key, data["rsa_public_xml"], lifetime)


@dataclass(frozen=True)
class VerifiedFreshness:
    release_payload_sha256: str
    release_counter: int
    renewal_counter: int
    not_before: datetime
    expires: datetime
    payload_sha256: str
    envelope_sha256: str


def verify_freshness(envelope_bytes, manifest, *, now=None, verify_rsa=False):
    if manifest.schema != 2 or not isinstance(manifest.renewal, RenewalPolicy):
        raise VerificationError("A versão não autoriza renovação.")
    envelope = _strict_json(envelope_bytes, MAX_FRESHNESS_BYTES, "Renovação")
    _keys(envelope, ("payload", "ed25519_signature", "rsa_signature"), "Renovação")
    raw = _b64(envelope["payload"], 4096, "Payload de renovação")
    ed_signature = _b64(envelope["ed25519_signature"], 64, "Assinatura Ed25519 de renovação")
    rsa_signature = _b64(envelope["rsa_signature"], 384, "Assinatura RSA de renovação")
    if len(ed_signature) != 64 or len(rsa_signature) != 384:
        raise VerificationError("Tamanho de assinatura de renovação inválido.")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(manifest.renewal.ed25519_public_key)).verify(ed_signature, raw)
        if verify_rsa:
            rsa_public_key(manifest.renewal.rsa_public_xml).verify(rsa_signature, raw, padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, InvalidSignature) as error:
        raise VerificationError("Assinatura de renovação inválida.") from error
    data = _strict_json(raw, 4096, "Payload de renovação")
    _keys(data, ("schema", "product", "channel", "release_counter", "release_payload_sha256",
                 "renewal_counter", "not_before", "expires"), "Payload de renovação")
    if (type(data["schema"]) is not int or data["schema"] != 1
            or data["product"] != manifest.product or data["channel"] != manifest.channel
            or type(data["release_counter"]) is not int or data["release_counter"] != manifest.release_counter
            or _hash(data["release_payload_sha256"], "release_payload_sha256") != manifest.payload_sha256):
        raise VerificationError("Renovação não corresponde à versão autorizada.")
    counter = _integer(data["renewal_counter"], "renewal_counter", 1, 2**63 - 1)
    begins = _timestamp(data["not_before"], "not_before")
    expires = _timestamp(data["expires"], "expires")
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise VerificationError("Relógio de renovação deve ter fuso explícito.")
    if (not manifest.not_before <= begins < expires <= manifest.expires
            or (expires - begins).total_seconds() > manifest.renewal.max_lifetime_seconds
            or not begins <= now < expires):
        raise VerificationError("Renovação ainda não válida, expirada ou fora da autorização.")
    return VerifiedFreshness(manifest.payload_sha256, manifest.release_counter, counter, begins, expires,
                             hashlib.sha256(raw).hexdigest(), hashlib.sha256(envelope_bytes).hexdigest())
