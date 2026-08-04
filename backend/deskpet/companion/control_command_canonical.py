"""Cross-language canonical encoding for privileged Companion commands.

The renderer may construct a command, but it does not get to define how that
command is hashed.  Rust, TypeScript and Python implement this exact binary
contract and consume the same checked-in vectors.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

CONTROL_COMMAND_SCHEMA = "control-command-canonical-v1"
CONTROL_COMMAND_DOMAIN = b"control-command-canonical-v1\0"
WINDOW_CREDENTIAL_DOMAIN = b"window-control-credential-v1\0"
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_U64 = (1 << 64) - 1
_U64_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")


class CanonicalCommandError(ValueError):
    """Input is not representable by ``control-command-canonical-v1``."""


def parse_canonical_u64(value: str, *, field: str) -> int:
    if not isinstance(value, str) or _U64_RE.fullmatch(value) is None:
        raise CanonicalCommandError(f"{field}:non_canonical_u64")
    parsed = int(value)
    if parsed > MAX_U64:
        raise CanonicalCommandError(f"{field}:u64_overflow")
    return parsed


def _u32(value: int) -> bytes:
    if value < 0 or value > 0xFFFF_FFFF:
        raise CanonicalCommandError("length_or_count_overflow")
    return value.to_bytes(4, "big", signed=False)


def _u64(value: int) -> bytes:
    if value < 0 or value > MAX_U64:
        raise CanonicalCommandError("u64_overflow")
    return value.to_bytes(8, "big", signed=False)


def _utf8(value: str) -> bytes:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CanonicalCommandError("lone_surrogate") from exc
    return encoded


def _lp_string(value: str) -> bytes:
    encoded = _utf8(value)
    return _u32(len(encoded)) + encoded


def encode_control_body(value: object) -> bytes:
    """Encode a JSON-like value using the v1 binary TLV contract."""

    if value is None:
        return b"\x00"
    if value is False:
        return b"\x01"
    if value is True:
        return b"\x02"
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalCommandError("integer_outside_safe_range")
        return b"\x03" + value.to_bytes(8, "big", signed=True)
    if isinstance(value, float):
        raise CanonicalCommandError("float_not_allowed")
    if isinstance(value, str):
        encoded = _utf8(value)
        return b"\x04" + _u32(len(encoded)) + encoded
    if isinstance(value, Mapping):
        items: list[tuple[bytes, str, object]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalCommandError("object_key_not_string")
            encoded_key = _utf8(key)
            items.append((encoded_key, key, item))
        items.sort(key=lambda item: item[0])
        payload = bytearray(b"\x06")
        payload.extend(_u32(len(items)))
        for _encoded_key, key, item in items:
            payload.extend(encode_control_body(key))
            payload.extend(encode_control_body(item))
        return bytes(payload)
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        payload = bytearray(b"\x05")
        payload.extend(_u32(len(value)))
        for item in value:
            payload.extend(encode_control_body(item))
        return bytes(payload)
    raise CanonicalCommandError(f"unsupported_body_type:{type(value).__name__}")


def canonical_request_bytes(
    command_kind: str,
    request_seq: str,
    binding_epoch: str,
    body: object,
) -> bytes:
    return b"".join(
        (
            CONTROL_COMMAND_DOMAIN,
            _lp_string(command_kind),
            _u64(parse_canonical_u64(request_seq, field="request_seq")),
            _u64(parse_canonical_u64(binding_epoch, field="binding_epoch")),
            encode_control_body(body),
        )
    )


def canonical_request_hash(
    command_kind: str,
    request_seq: str,
    binding_epoch: str,
    body: object,
) -> str:
    return hashlib.sha256(
        canonical_request_bytes(command_kind, request_seq, binding_epoch, body)
    ).hexdigest()


def credential_signed_payload(
    *,
    backend_process_instance_id: str,
    connection_id: str,
    control_epoch: str,
    window_label: str,
    scope: str,
    challenge_hash: str,
    request_seq: str,
    command_kind: str,
    canonical_request_hash_hex: str,
    nonce: str,
    issued_at: str,
    expires_at: str,
) -> bytes:
    """Build the fixed-order Ed25519 signed payload."""

    if (
        len(canonical_request_hash_hex) != 64
        or any(ch not in "0123456789abcdef" for ch in canonical_request_hash_hex)
    ):
        raise CanonicalCommandError("canonical_request_hash:not_lower_hex_sha256")
    return b"".join(
        (
            WINDOW_CREDENTIAL_DOMAIN,
            _lp_string(backend_process_instance_id),
            _lp_string(connection_id),
            _u64(parse_canonical_u64(control_epoch, field="control_epoch")),
            _lp_string(window_label),
            _lp_string(scope),
            _lp_string(challenge_hash),
            _u64(parse_canonical_u64(request_seq, field="request_seq")),
            _lp_string(command_kind),
            bytes.fromhex(canonical_request_hash_hex),
            _lp_string(nonce),
            _u64(parse_canonical_u64(issued_at, field="issued_at")),
            _u64(parse_canonical_u64(expires_at, field="expires_at")),
        )
    )


def parse_canonical_json(raw: str | bytes) -> object:
    """Parse JSON while rejecting duplicate keys, floats and invalid UTF-8."""

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CanonicalCommandError("invalid_utf8") from exc
    else:
        text = raw

    def reject_float(_value: str) -> object:
        raise CanonicalCommandError("float_not_allowed")

    def reject_constant(_value: str) -> object:
        raise CanonicalCommandError("non_finite_not_allowed")

    def parse_integer(value: str) -> int:
        if value == "-0":
            raise CanonicalCommandError("negative_zero")
        return int(value)

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalCommandError("duplicate_object_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_int=parse_integer,
            parse_float=reject_float,
            parse_constant=reject_constant,
        )
    except CanonicalCommandError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CanonicalCommandError("invalid_json") from exc
    encode_control_body(value)
    return value


__all__ = [
    "CONTROL_COMMAND_SCHEMA",
    "CanonicalCommandError",
    "canonical_request_bytes",
    "canonical_request_hash",
    "credential_signed_payload",
    "encode_control_body",
    "parse_canonical_json",
    "parse_canonical_u64",
]
