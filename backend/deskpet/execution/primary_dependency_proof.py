"""主对话可见性依赖证明：规范化与解析（阶段 6E 从 primary_dependencies 下沉，供页面准入与依赖校验共用）。"""

from __future__ import annotations

from collections.abc import Mapping

from deskpet.task_scope.protocol import canonical_json, digest, identifier


def dependencies(
    evidence=(), recall=(), short_horizon=(), *, schema_version=None, procedure_drafts=()
):
    """Canonical bounded proof; v1 remains readable without rewriting archives."""
    short_horizon = tuple(short_horizon)
    procedure_drafts = tuple(procedure_drafts)
    version = (
        (3 if procedure_drafts else 2 if short_horizon else 1)
        if schema_version is None
        else schema_version
    )
    if (
        type(version) is not int
        or version not in {1, 2, 3}
        or (version == 1 and short_horizon)
        or (version != 3 and procedure_drafts)
    ):
        raise ValueError("primary_dependencies_invalid")
    rows = {"evidence": {}, "recall": {}}
    fields_by_kind = [
        ("evidence", evidence, ("evidence_id", "envelope_hash")),
        ("recall", recall, ("result_id", "result_hash", "item_id", "item_hash")),
    ]
    if version >= 2:
        rows["short_horizon"] = {}
        fields_by_kind.append(
            ("short_horizon", short_horizon, ("audit_id", "chunk_ref", "content_hash"))
        )
    if version == 3:
        rows["procedure_drafts"] = {}
        fields_by_kind.append(
            ("procedure_drafts", procedure_drafts, ("memory_id", "revision", "candidate_hash"))
        )
    for kind, values, fields in fields_by_kind:
        for value in values:
            if not isinstance(value, Mapping) or set(value) != set(fields):
                raise ValueError("primary_dependencies_invalid")
            value = dict(value)
            for key in fields:
                if key == "revision":
                    if type(value[key]) is not int or value[key] < 1:
                        raise ValueError("primary_dependencies_invalid")
                else:
                    (digest if key.endswith("hash") else identifier)(value[key], key)
            rows[kind][canonical_json(value)] = value
    if sum(map(len, rows.values())) > 256:
        raise ValueError("primary_dependencies_limit")
    return {
        "schema_version": version,
        **{kind: [items[key] for key in sorted(items)] for kind, items in rows.items()},
    }


def parse_dependencies(value):
    if not isinstance(value, Mapping):
        raise ValueError("primary_dependencies_missing")
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, 2, 3}:
        raise ValueError("primary_dependencies_missing")
    fields = (
        {"schema_version", "evidence", "recall"}
        | ({"short_horizon"} if version >= 2 else set())
        | ({"procedure_drafts"} if version == 3 else set())
    )
    if set(value) != fields or not all(
        isinstance(value[k], (tuple, list)) for k in fields - {"schema_version"}
    ):
        raise ValueError("primary_dependencies_missing")
    return dependencies(
        value["evidence"],
        value["recall"],
        value.get("short_horizon", ()),
        schema_version=version,
        procedure_drafts=value.get("procedure_drafts", ()),
    )
