# SPDX-License-Identifier: Apache-2.0
"""2026-09-25 desktop run (ledger.py + pytest + README): the reviewer turn's input
manifest was 230 KB, under the 256 KB JSON limit, but read_exact_metadata also
serialised the whole row — which carries the same body again as an escaped string —
into lifecycle_json (275 KB), so every review import raised JSON_BYTES_LIMIT, the
recheck budget ran out and the leaf went to manual resolution.  The lifecycle part
now excludes the body column; the body is still checked against its hash on its own.
"""

from __future__ import annotations

from agent_orchestrator.assurance.codec import AssuranceError, canonical, decode, fingerprint
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.contracts.models import Budget, Mission, MissionStatus
from agent_orchestrator.storage.assurance_reads import AssuranceReader
from agent_orchestrator.storage.store import Store

TENANT = "tenant-large"
MISSION = "mission-large"


def test_a_large_manifest_body_under_the_limit_reads_exactly(tmp_path):
    store = Store.open(tmp_path / "orchestrator.db")
    try:
        store.insert_mission(Mission(MISSION, "goal", ("c",), (), (), "low", Budget(), TENANT,
                                     MissionStatus.CREATED, 1.0, 1, "key-large"), spec_hash="b" * 64)
        # Many small quoted fields, like a real provider request: escaping the body as a
        # string inside the row grows it by ~20 %, past the limit.
        body = {"messages": [{"role": "tool", "name": f"t{i}", "content": f"line {i}"} for i in range(4200)]}
        size = len(canonical(body).encode("utf-8"))
        assert 200 * 1024 < size < 256 * 1024  # the body alone is within the limit
        digest = fingerprint(body)
        store.connection.execute(
            "INSERT INTO input_manifests(manifest_hash, origin_mission_id, manifest_json, created_at) VALUES (?,?,?,?)",
            (digest, MISSION, canonical(body), 1.0))
        reader = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
        metadata = reader.read_exact_metadata(AssuranceRef("input_manifest", Pin(digest, 0, digest)))
        assert decode(metadata.body_json) == body
        lifecycle = decode(metadata.lifecycle_json)
        assert "manifest_json" not in lifecycle  # the body is never carried twice
        assert lifecycle["manifest_hash"] == digest and lifecycle["origin_mission_id"] == MISSION
    finally:
        store.close()


def test_a_manifest_past_the_json_limit_is_hashed_under_the_record_limit(tmp_path):
    """2026-10-06 真实模型验收（编程题，5 份验收）：审阅员输入清单 276 KB。读取器按记录上限（8 MiB）解码
    它没问题，但核对哈希时又按 256 KB 的默认上限重编码一次，于是每次导入终审都报 JSON_BYTES_LIMIT，
    重查 33 次后转人工，任务失败。哈希要按解码时的同一个上限算。

    **改坏检验**：核对哈希时不带上限（AS-M17）→ 变红。"""
    from agent_orchestrator.assurance.codec import MAX_BYTES, MAX_RECORD_BYTES

    store = Store.open(tmp_path / "orchestrator.db")
    try:
        store.insert_mission(Mission(MISSION, "goal", ("c",), (), (), "low", Budget(), TENANT,
                                     MissionStatus.CREATED, 1.0, 1, "key-large"), spec_hash="b" * 64)
        body = {"messages": [{"role": "tool", "name": f"t{i}", "content": f"line {i}"} for i in range(5600)]}
        stored = canonical(body, limit=MAX_RECORD_BYTES)
        assert MAX_BYTES < len(stored.encode("utf-8")) < MAX_RECORD_BYTES
        digest = fingerprint(body, limit=MAX_RECORD_BYTES)
        store.connection.execute(
            "INSERT INTO input_manifests(manifest_hash, origin_mission_id, manifest_json, created_at) VALUES (?,?,?,?)",
            (digest, MISSION, stored, 1.0))
        reader = AssuranceReader(store, tenant_id=TENANT, mission_id=MISSION)
        try:
            metadata = reader.read_exact_metadata(AssuranceRef("input_manifest", Pin(digest, 0, digest)))
        except AssuranceError as error:
            raise AssertionError(f"a manifest within the record limit must read: {error.code}") from error
        assert decode(metadata.body_json, limit=MAX_RECORD_BYTES) == body
    finally:
        store.close()
