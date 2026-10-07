# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""推后必补第 2 批车道 Q1：保证通道合同与引用（A16、A14、A12、A23）。

- A16：命题键的哈希不截断，哈希对象按原计划字段名 ``{predicate, typed_args}``
  （``ASSURANCE-EXEC-1.1`` §8.2）。namespace / scope 见车道记录偏差-1。
- A14：检查规格在本任务保证通道建立时由系统一次写定（§3.1 check_spec 行）；用到时只读。
- A12：引用种类只有一个来源：随包 ``common.schema.json``（§3.1、§12.1）。
- A23：反向依赖索引（queries Q09，"只是优化"）没有读方，删掉写入和表。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import agent_orchestrator
from agent_orchestrator.assurance import refs
from agent_orchestrator.contracts.semantic_base import VersionedRef, content_hash_of
from agent_orchestrator.knowledge.predicates import (
    PredicateParameter,
    PredicateSignature,
    proposition_key,
    proposition_key_of,
)
from agent_orchestrator.orchestrator.assurance_local_checks import AssuranceLocalChecks
from agent_orchestrator.storage import assurance_store
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

PACKAGE = Path(agent_orchestrator.__file__).parent
SCHEMA = PACKAGE / "assurance" / "schemas" / "common.schema.json"
CONTRACT_COPY = PACKAGE / "assurance" / "contracts" / "common.schema.json"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ---------------------------------------------------------------- A16 命题键


def _signature() -> PredicateSignature:
    return PredicateSignature(
        predicate_ref=VersionedRef("demo.flag", 1, "a" * 64),
        parameters=(PredicateParameter("x", "integer", required=False),
                    PredicateParameter("y", "boolean", required=False)),
    )


def test_a16_proposition_key_keeps_the_full_hash_of_the_plan_fields():
    signature = _signature()
    key = proposition_key(signature, {"x": 1})
    expected = content_hash_of(
        {"predicate": signature.predicate_ref.to_json(), "typed_args": {"x": 1}}
    )
    assert key == f"demo.flag@1#{expected}"
    assert len(key.rpartition("#")[2]) == 64
    assert proposition_key_of(signature.predicate_ref.to_json(), {"x": 1}) == key


def test_a16_proposition_key_does_not_merge_one_with_true_or_missing_with_null():
    signature = _signature()
    assert proposition_key(signature, {"x": 1}) != proposition_key(signature, {"x": True})
    assert proposition_key(signature, {}) != proposition_key(signature, {"x": None})


# ---------------------------------------------------------------- A12 种类唯一来源


def _schema_kinds(path: Path) -> frozenset[str]:
    document = json.loads(path.read_bytes())
    return frozenset(document["$defs"]["ref"]["properties"]["kind"]["enum"])


def test_a12_ref_kinds_are_read_from_the_common_schema_not_copied():
    assert refs.REF_KINDS == _schema_kinds(SCHEMA)
    source = inspect.getsource(refs)
    # 手抄表删掉：模块里不再逐个写出种类名
    assert '"tool_receipt"' not in source and '"review_package"' not in source
    assert "common.schema.json" in source


def test_a12_packaged_schema_copies_are_byte_identical():
    assert SCHEMA.read_bytes() == CONTRACT_COPY.read_bytes()


def test_a12_packaged_schema_matches_the_plan_contract_in_the_host_repo():
    plan = PACKAGE.parents[3] / "plans" / "Assurance" / "specs" / "1.1" / "contracts" / "common.schema.json"
    if not plan.exists():
        pytest.skip("SDK 单独检出时没有 Host 仓库里的原计划合同")
    assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == hashlib.sha256(plan.read_bytes()).hexdigest()


# ---------------------------------------------------------------- A23 反向依赖索引


def test_a23_certificate_writer_has_no_reverse_index():
    source = inspect.getsource(assurance_store.AssuranceStore.record_certificate)
    assert "assurance_dependency_index" not in source


# ---------------------------------------------------------------- A14 + A23 产品同形


def test_a14_check_specs_are_written_at_creation_and_only_read_on_use(tmp_path):
    # 用到时临时登记的入口删掉
    assert not hasattr(AssuranceLocalChecks, "_registry")

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            assert store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='assurance_dependency_index'"
            ).fetchone() is None
            mission_id = world.create({"goal": "写一份 NOTES.md，列出三条要点。",
                                       "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "q1-a14"})["mission_id"]
            # 建任务即写定：还没跑任何检查，三层登记都已在
            rows = store.connection.execute(
                "SELECT type FROM events WHERE mission_id=? AND type='AssuranceCheckSpecRegistered'",
                (mission_id,),
            ).fetchall()
            assert len(rows) == 3
            local = world.loop.commit._assurance_check_importer
            registry = local._read_registry(mission_id)
            assert set(registry) == {"format_check", "rule_check", "code_test"}

            done = await world.run_until_settled(mission_id, timeout=60)
            assert str(done.status.value) == "COMPLETED", done.final_report
            # 跑完也没有再登记
            assert store.connection.execute(
                "SELECT COUNT(*) FROM events WHERE mission_id=? AND type='AssuranceCheckSpecRegistered'",
                (mission_id,),
            ).fetchone()[0] == 3
            # 证书照发，read_set 完整存在证书里
            assert store.connection.execute(
                "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", (mission_id,)
            ).fetchone()[0] > 0

    asyncio.run(case())
