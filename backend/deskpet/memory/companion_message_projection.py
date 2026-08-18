# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — companion_message_projection。

原模块已移除，等待 simple-harness-memory-sdk 集成。
此 stub 仅防止 companion/notifications.py 在导入时崩溃。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


COMPANION_REDACTION_TOMBSTONE: str = "__redacted__"
COMPANION_REDACTION_TOMBSTONE_HASH: str = hashlib.sha256(
    COMPANION_REDACTION_TOMBSTONE.encode()
).hexdigest()


@dataclass(frozen=True)
class TrustedCompanionOwner:
    profile_id: str
    profile_generation: int
    binding_epoch: int


@dataclass(frozen=True)
class TrustedCompanionProjectionRoute:
    owner: TrustedCompanionOwner
    route_version: int = 0


@dataclass(frozen=True)
class CurrentCompanionProjection:
    owner: TrustedCompanionOwner
    sequence: int = 0


def canonical_json(obj: object) -> str:
    """将对象序列化为规范 JSON 字符串（用于 hash 计算）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(values: list) -> str:
    """对值列表计算规范 hash（SHA-256 前16位）。"""
    raw = canonical_json(values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
