# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Verified Knowledge (§11 layer 3, plan D4-3): the projection of VERIFIED claims that
other Agents may depend on, stored separately from the claims (30-07) and carrying
the full provenance of §11.2 (30-08): who proposed it, from which Task / Attempt /
Result, on which evidence, verified by what, depending on which knowledge, used by
which Tasks, superseding / superseded by what.

Records are written only by the Commit Service inside the accept transaction; a
claim below VERIFIED never appears here — SUPPORTED is evidence, not knowledge
(理论 04-9).  ``KnowledgeIndex`` is the read model the Verifier uses to check
``used_knowledge`` references (D4-4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

from ..contracts import ClaimStatus, ContractError
from ..contracts.models import MAX_ATTRIBUTION_TEXT, MAX_TEXT, _object, _text, _texts

if TYPE_CHECKING:
    from ..storage.store import Store

KNOWLEDGE_STATES = ("VERIFIED", "SUPERSEDED")


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    id: str  # = the VERIFIED claim's id
    mission_id: str
    claim_id: str
    content: str
    type: str
    status: str
    version: int
    key: str | None
    stance: str
    proposed_by: str
    source_task: str
    source_attempt: str
    source_result: str
    evidence: tuple[str, ...]
    verifier: Mapping[str, Any]
    created_at: float
    used_by: tuple[str, ...] = ()
    supersedes: str | None = None
    superseded_by: str | None = None
    disputed_by: tuple[str, ...] = ()
    evidence_trust: tuple[str, ...] = ()
    #: What this knowledge rests on (阶段 C), written with the row and never changed:
    #: ``{"acceptance_id", "artifacts": [{id, version, content_hash}], "knowledge": [...]}``.
    #: Whether the knowledge is still current is read from this and nothing else
    #: (``knowledge_standing``); empty means nothing vouches for it.
    support: Mapping[str, Any] = field(default_factory=dict)
    #: 原计划 §11.2 / §25.1 第 5 条的三个登记项（第 2 批 K02，迁移 45）。写方只在审阅通过入库时
    #: 从验收用的证书与任务的保证通道取值；取不到就是 None，不猜：
    #: ``validity_interval`` —— ``{"valid_from_ms", "valid_until_ms", "mission_epoch"}``，证书签发时刻、
    #: 失效时刻（None＝未定）与签发时的任务纪元；``permitted_uses`` —— 允许的用途清单；
    #: ``assurance_level`` —— 确认这条知识的审阅所在的保证通道（如 ``ASSURANCE_1_1``）。
    validity_interval: Mapping[str, Any] | None = None
    permitted_uses: tuple[str, ...] | None = None
    assurance_level: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "id",
            "mission_id",
            "claim_id",
            "source_task",
            "source_attempt",
            "source_result",
        ):
            object.__setattr__(
                self, name, _text(getattr(self, name), f"knowledge.{name}", limit=512)
            )
        object.__setattr__(
            self,
            "content",
            _text(
                self.content,
                "knowledge.content",
                limit=MAX_ATTRIBUTION_TEXT if self.type == "attribution" else MAX_TEXT,
            ),
        )
        if self.status not in KNOWLEDGE_STATES:
            raise ContractError(f"knowledge.status must be one of {list(KNOWLEDGE_STATES)}")
        object.__setattr__(self, "verifier", _object(self.verifier, "knowledge.verifier"))
        for name in (
            "evidence",
            "used_by",
            "disputed_by",
            "evidence_trust",
        ):
            object.__setattr__(self, name, _texts(getattr(self, name), f"knowledge.{name}"))
        object.__setattr__(self, "support", _support(self.support))
        object.__setattr__(self, "validity_interval", _validity_interval(self.validity_interval))
        if self.permitted_uses is not None:
            object.__setattr__(
                self, "permitted_uses", _texts(self.permitted_uses, "knowledge.permitted_uses"))
        if self.assurance_level is not None:
            object.__setattr__(
                self, "assurance_level", _text(self.assurance_level, "knowledge.assurance_level", limit=128))

    def to_json(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        for name, value in list(data.items()):
            if isinstance(value, tuple):
                data[name] = list(value)
            elif isinstance(value, Mapping):
                data[name] = dict(value)
        return data

    @classmethod
    def from_json(cls, value: object) -> KnowledgeRecord:
        data = _object(value, "knowledge")
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in data:
                raw = data[f.name]
                kwargs[f.name] = tuple(raw) if isinstance(raw, list) else raw
        return cls(**kwargs)


_SUPPORT_KEYS = ("acceptance_id", "artifacts", "knowledge")
_SUPPORT_MEMBER_KEYS = ("content_hash", "id", "version")


def _support(value: object) -> dict[str, Any]:
    """``KnowledgeRecord.support``: empty, or exactly the three keys with pinned members."""
    if not isinstance(value, Mapping):
        raise ContractError("knowledge.support must be an object")
    if not value:
        return {}
    if tuple(sorted(value)) != _SUPPORT_KEYS:
        raise ContractError(f"knowledge.support must carry exactly {list(_SUPPORT_KEYS)}")
    out: dict[str, Any] = {"acceptance_id": _text(value["acceptance_id"], "knowledge.support.acceptance_id", limit=512)}
    for name in ("artifacts", "knowledge"):
        rows = value[name]
        if not isinstance(rows, (list, tuple)) or any(
                not isinstance(row, Mapping) or tuple(sorted(row)) != _SUPPORT_MEMBER_KEYS
                or type(row["version"]) is not int for row in rows):
            raise ContractError(f"knowledge.support.{name} rows must carry exactly {list(_SUPPORT_MEMBER_KEYS)}")
        out[name] = sorted(({"id": str(row["id"]), "version": row["version"],
                             "content_hash": str(row["content_hash"])} for row in rows),
                           key=lambda row: row["id"])
    return out


_VALIDITY_KEYS = ("mission_epoch", "valid_from_ms", "valid_until_ms")


def _validity_interval(value: object) -> dict[str, Any] | None:
    """``KnowledgeRecord.validity_interval``: None, or exactly the three keys; ``valid_until_ms``
    may be None (no expiry was certified), the other two are integers."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or tuple(sorted(value)) != _VALIDITY_KEYS:
        raise ContractError(f"knowledge.validity_interval must carry exactly {list(_VALIDITY_KEYS)}")
    out: dict[str, Any] = {}
    for name in _VALIDITY_KEYS:
        item = value[name]
        if item is None and name == "valid_until_ms":
            out[name] = None
        elif type(item) is int:
            out[name] = item
        else:
            raise ContractError(f"knowledge.validity_interval.{name} must be an integer")
    return out


def knowledge_ref(knowledge_id: str, version: int) -> str:
    """How a result names knowledge it used: ``<id>@<version>`` (阶段 C)."""
    return f"{knowledge_id}@{int(version)}"


def parse_knowledge_ref(reference: str) -> tuple[str, int | None]:
    """``<id>@<version>`` → (id, version); the version is None when it is not written
    (the acceptance check refuses that — this only reads what was written)."""
    head, mark, tail = str(reference).rpartition("@")
    if mark and head and tail.isdigit():
        return head, int(tail)
    return str(reference), None


@dataclass(frozen=True, slots=True)
class KnowledgeIndex:
    """Read model of one Mission's knowledge and claim statuses (D4-4)."""

    mission_id: str
    records: Mapping[str, KnowledgeRecord]
    claim_status: Mapping[str, ClaimStatus]
    _store: Store | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, store: Store, mission_id: str) -> KnowledgeIndex:
        records = {record.id: record for record in store.list_knowledge(mission_id)}
        claims = {claim.id: claim.status for claim in store.list_mission_claims(mission_id)}
        return cls(mission_id=mission_id, records=records, claim_status=claims, _store=store)

    @classmethod
    def empty(cls, mission_id: str) -> KnowledgeIndex:
        return cls(mission_id=mission_id, records={}, claim_status={})

    def verified(self) -> list[KnowledgeRecord]:
        return [r for r in self.records.values() if r.status == "VERIFIED"]

    def check(self, used_knowledge: Sequence[str]) -> list[str]:
        """Problems with ``used_knowledge`` references (empty list = all usable).

        A reference is ``<id>@<version>``: the version the worker read is the version it
        is held to.  No version, another version, or knowledge that is no longer current
        (:func:`knowledge_standing`) are all problems."""
        from .knowledge_standing import CURRENT, knowledge_standing

        problems: list[str] = []
        for reference in used_knowledge:
            knowledge_id, version = parse_knowledge_ref(reference)
            record = self.records.get(knowledge_id)
            if record is None:
                status = self.claim_status.get(knowledge_id)
                if status is None:
                    if ":claim-" in knowledge_id:  # shaped like a claim id → another Mission's
                        problems.append(
                            f"used_knowledge {reference!r} is not in this Mission's "
                            "Verified Knowledge"
                        )
                    else:
                        problems.append(f"used_knowledge {reference!r} is unknown")
                else:
                    problems.append(
                        f"used_knowledge {reference!r} is a claim in status {status}, "
                        "not VERIFIED knowledge"
                    )
                continue
            if version is None:
                problems.append(
                    f"used_knowledge {reference!r} names no version; write it as "
                    f"{knowledge_ref(record.id, record.version)!r}"
                )
                continue
            if version != record.version:
                problems.append(
                    f"used_knowledge {reference!r} is not the current version "
                    f"({knowledge_ref(record.id, record.version)!r})"
                )
                continue
            standing = CURRENT if self._store is None else knowledge_standing(self._store, record)
            if standing == "SUPERSEDED":
                problems.append(
                    f"used_knowledge {reference!r} is SUPERSEDED; "
                    f"the current version is {record.superseded_by!r}"
                )
            elif standing != CURRENT:
                problems.append(f"used_knowledge {reference!r} is out of date ({standing})")
        return problems


__all__ = ("KNOWLEDGE_STATES", "KnowledgeIndex", "KnowledgeRecord", "knowledge_ref", "parse_knowledge_ref")
