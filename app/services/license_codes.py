from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

LICENSE_CODE_PREFIX = "gas-license-v1"
MAX_AUTHORIZATION_CODE_LENGTH = 4096
DEFAULT_LICENSE_SIGNING_KEY = b"kunlian-gas-system-260708-license-key-v1"


def build_authorization_code(payload: dict[str, Any], signing_key: bytes) -> str:
    if not signing_key:
        raise ValueError("activation signing key is not configured")
    payload_json = canonical_payload(payload)
    payload_b64 = b64encode(payload_json.encode("utf-8"))
    signature = authorization_signature(payload_b64, signing_key)
    return f"{LICENSE_CODE_PREFIX}.{payload_b64}.{signature}"


def parse_authorization_code(authorization_code: str, signing_key: bytes) -> tuple[str, str]:
    if not signing_key:
        raise ValueError("activation signing key is not configured")
    if not isinstance(authorization_code, str) or not authorization_code.strip():
        raise ValueError("authorization code is required")
    code = authorization_code.strip()
    if len(code) > MAX_AUTHORIZATION_CODE_LENGTH:
        raise ValueError("authorization code is too long")
    parts = code.split(".")
    if len(parts) != 3 or parts[0] != LICENSE_CODE_PREFIX:
        raise ValueError("unsupported authorization code")
    payload_b64, signature = parts[1], parts[2]
    expected = authorization_signature(payload_b64, signing_key)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid signature")
    payload_json = b64decode_text(payload_b64)
    loads_payload(payload_json, strict=True)
    return payload_json, signature


def authorization_signature(payload_b64: str, signing_key: bytes) -> str:
    if not signing_key:
        raise ValueError("activation signing key is not configured")
    message = f"{LICENSE_CODE_PREFIX}.{payload_b64}".encode("utf-8")
    return b64encode(hmac.new(signing_key, message, hashlib.sha256).digest())


def canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads_payload(payload_json: str, strict: bool = False) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError("invalid payload") from exc
        return None
    if not isinstance(payload, dict):
        if strict:
            raise ValueError("invalid payload")
        return None
    return payload


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def b64decode_text(value: str) -> str:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError("invalid base64") from exc
