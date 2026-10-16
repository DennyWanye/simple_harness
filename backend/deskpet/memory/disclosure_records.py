"""可信披露的底层：策略常量、错误类型与当前绑定记录的读取（阶段 6E 从 trusted_disclosure 下沉，当前输入来源模块因此不再导入它）。"""

from __future__ import annotations

import json

from deskpet.task_scope.protocol import canonical_hash

POLICY = {
    "policy_id": "host-local-disclosure/v1",
    "minimum_necessary": True,
    "chat_grants_authority": False,
    "configuration_grants_input_permit": False,
}


POLICY_HASH = canonical_hash(POLICY)


class TrustedDisclosureError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _reject(code):
    raise TrustedDisclosureError("host_disclosure_" + code)


async def _one(db, sql, args=()):
    cursor = await db.execute(sql, args)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


def _decode(row, subject):
    if row is None:
        _reject("binding_missing")
    raw = json.loads(row["binding_json"])
    if (
        type(raw["policy_generation"]) is not int
        or row["subject"] != subject
        or raw["subject"] != subject
        or raw["binding_ref"] != row["binding_ref"]
        or raw["source_ref"] != row["binding_ref"]
        or raw["policy_generation"] != row["policy_generation"]
        or canonical_hash(raw) != row["binding_hash"]
        or raw["policy_hash"] != POLICY_HASH
        or raw["policy_id"] != POLICY["policy_id"]
        or raw["source_origin"] not in {"host_default", "authenticated_control"}
    ):
        _reject("binding_mismatch")
    return raw


async def current_record_tx(db, subject):
    row = await _one(
        db,
        "SELECT c.* FROM human_memory_disclosure_heads h "
        "JOIN human_memory_disclosure_configs c ON c.binding_ref=h.binding_ref WHERE h.subject=?",
        (subject,),
    )
    return None if row is None else _decode(row, subject)


def binding_token(raw):
    return {
        "binding_ref": raw["binding_ref"],
        "binding_hash": canonical_hash(raw),
        "policy_generation": raw["policy_generation"],
    }
