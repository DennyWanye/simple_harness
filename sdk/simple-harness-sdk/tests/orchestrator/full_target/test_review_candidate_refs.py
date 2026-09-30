"""最终 / 组合审查包里的验收候选引用指向真实的验收（2026-10-01，审阅升级具名后续）。

真机第 6 局最终审阅员的疑点：它拿到的验收引用写着修订 1，而披露给它的证据里同一条验收是修订 0。
根因：根审查与组合审查两个包构造候选引用时用的是占位——修订号写死 1、哈希是"验收 id 的哈希"，
不是验收正文；而保证层给审阅员披露证据按真实正文钉（不可变行 = 修订 0、正文指纹）。现在两处
共用一个构造：修订 0 + 验收正文哈希，与披露的证据一字不差。
"""
from __future__ import annotations

from test_htn_store import MISSION, acceptance, review_package, review_record, store  # noqa: F401  (fixture)

from agent_orchestrator.assurance.codec import decode, fingerprint
from agent_orchestrator.contracts.semantic_base import Provenance, TypedRefKind, content_hash_of
from agent_orchestrator.orchestrator.root_review import acceptance_ref
from agent_orchestrator.storage.htn_store import HtnStore


def test_the_candidate_ref_names_the_stored_acceptance_body(store):  # type: ignore[no-untyped-def]
    htn = HtnStore(store)
    htn.insert_review_package(review_package())
    htn.insert_review_record(review_record(), official=True)
    htn.insert_acceptance(acceptance())
    ref = acceptance_ref(store, "acceptance-1")
    assert (ref.kind, ref.id, ref.revision, ref.produced_by) == (TypedRefKind.ACCEPTANCE, "acceptance-1", 0, Provenance.TOOL)
    assert ref.content_hash == content_hash_of(acceptance().to_json())
    # ... which is exactly how the assurance side pins the same row for the reviewer's evidence.
    row = store.connection.execute("SELECT acceptance_json FROM acceptances WHERE acceptance_id='acceptance-1'").fetchone()
    assert ref.content_hash == fingerprint(decode(row[0]))
    assert ref.content_hash != content_hash_of("acceptance-1")  # the old placeholder
