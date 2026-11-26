# SPDX-License-Identifier: Apache-2.0
"""用真实底仓库、产物校验和判定副本验证多步骤交付；只有验收索引和事件接收端是替身。"""

import asyncio
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_orchestrator.artifacts.delivery import apply_change_set
from agent_orchestrator.artifacts.input_bindings import DeliveryBinding, InputManifest, TargetRules
from agent_orchestrator.artifacts.versioning import manifest_upstream_inputs
from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.artifacts.workspace_base import DirectoryBase, git_blob_id
from agent_orchestrator.contracts import Artifact
from agent_orchestrator.host.host import OrchestrationHost
from agent_orchestrator.orchestrator.commit_service_domains.results import ResultCommitsMixin
from agent_orchestrator.orchestrator.orchestrator_domains import attempt_judgment
from agent_orchestrator.orchestrator.orchestrator_domains.attempt_judgment import (
    AttemptJudgmentMixin,
)
from tests.orchestrator.full_target import test_input_manifest_resolution as bindings

BASE = b"first\na\nb\nmid\nc\nd\nlast\n"
LEFT = BASE.replace(b"first", b"FIRST")
RIGHT = BASE.replace(b"last", b"LAST")
BOTH = LEFT.replace(b"last", b"LAST")
PATH = "README.md"


def judge(
    tmp_path,
    monkeypatch,
    base,
    versions,
    *,
    inputs=(),
    linked=(),
    path=PATH,
    aliases=(),
    frozen=None,
    seed=None,
    adopted=None,
):
    manager = WorkspaceManager(tmp_path / "workspaces")
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = DirectoryBase(target_dir)
    for name, data in (base or {}).items():
        location = target_dir / name
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_bytes(data)
    original_bytes = {
        name: (target_dir / name).read_bytes() if (target_dir / name).is_file() else None
        for name in [path, *(entry[0] for entry in aliases)]
    }
    report = {"workspace_seed": seed or {}}
    if base is not None:
        report["workspace_base"] = {
            "commit": manager.bases.commit_files(base),
            "source": {"location": target.location()},
        }
    mission = SimpleNamespace(id="mission", final_report=report)
    tasks, artifacts, ports, acceptances = [], {}, [], []
    for n, (name, data) in enumerate([*((path, data) for data in versions), *aliases]):
        key, task, attempt = f"a{n}", f"t{n}", f"attempt{n}"
        filename = tmp_path / key
        filename.write_bytes(data)
        artifacts[key] = Artifact(
            key,
            mission.id,
            task,
            attempt,
            "file",
            name,
            n + 1,
            sha256(data).hexdigest(),
            len(data),
            task,
            storage_uri=str(filename),
        )
        tasks.append(
            SimpleNamespace(
                id=task,
                accepted_artifacts=[key],
                accepted_result_id=None,
                status="COMPLETED",
                dependency_ids=[],
            )
        )
        acceptances.append(SimpleNamespace(acceptance_id=key, task_id=task))
        ports.append(
            dict(acceptance_id=key, producer_task_ref=task, artifact_id=key, output_port="out")
        )
    mode = SimpleNamespace(
        root_contributions=lambda _: {
            "root": list(artifacts) if adopted is None else [f"a{i}" for i in adopted]
        },
        semantics=lambda: SimpleNamespace(
            list_acceptances=lambda _: acceptances, list_acceptance_outputs=lambda _: ports
        ),
    )
    views = dict(frozen or {})
    for child, parent in inputs:
        artifact = artifacts[f"a{parent}"]
        delivered = DeliveryBinding(
            artifact.id,
            artifact.task_id,
            artifact.id,
            1,
            artifact.id,
            artifact.path,
            artifact.content_hash,
        )
        views[f"attempt{child}"] = (
            InputManifest(f"t{child}", deliveries=(delivered,)),
            TargetRules("consumer"),
        )

    def context(_, attempt):
        manifest, rules = views.get(attempt, (InputManifest(attempt), TargetRules("consumer")))
        return SimpleNamespace(
            manifest=manifest,
            target_rules=rules,
            upstream=manifest_upstream_inputs(manifest, rules),
        )

    commit = Mock()
    commit.record_artifact_merge_not_applicable = (
        ResultCommitsMixin.record_artifact_merge_not_applicable.__get__(commit)
    )
    commit.taskgraph_attempt_context.side_effect = context
    loop = SimpleNamespace(
        assembled=SimpleNamespace(workspaces=manager),
        store=SimpleNamespace(get_artifact=artifacts.get),
        commit=commit,
        _root_review=lambda *_: SimpleNamespace(
            carried_criteria=lambda _: [SimpleNamespace(task_id=tasks[n].id) for n in linked]
        ),
        _new_mode=lambda _: mode,
        _note=lambda _: None,
        _owner="owner",
        _register_copy=Mock(),
        _reimport_unsettled=lambda _: None,
        _current_source_files=lambda _: {},
        _assured_root_grades=lambda *_: {},
        _config=SimpleNamespace(deployment_policy=SimpleNamespace(denied_path_prefixes=[])),
    )
    for name in ("_judgment_tree", "_hierarchical_judgment_inputs"):
        setattr(loop, name, getattr(AttemptJudgmentMixin, name).__get__(loop))
    monkeypatch.setattr(attempt_judgment, "current_statements", lambda *_: [f"file:{path}"])
    judgments, _ = asyncio.run(AttemptJudgmentMixin._evaluate_criteria(loop, mission, tasks))
    if not aliases and not seed:
        assert judgments[0]["met"]
    tree = manager.root / "mission-judge-owner-verify"
    event = commit._emit.call_args.kwargs["payload"]
    change = AttemptJudgmentMixin._change_set(loop, mission, tasks)
    if base is None:
        assert change is None
        return tree, event, {}
    entries = {item["path"]: item for item in change["files"]}
    if not entries and event["superseded"]:
        [record] = event["superseded"]
        facts = [
            {
                "path": path,
                "merge_conflict": record["merge_conflict"],
                "overridden_steps": record["superseded_task_ids"],
            }
        ]
        assert change["unchanged_merge_facts"] == facts
        facade = SimpleNamespace(
            _change_set=lambda _: ({"status": "COMPLETED"}, change),
            _deliveries=lambda: SimpleNamespace(receipts=lambda _: []),
        )
        view = OrchestrationHost.delivery(facade, mission.id)
        assert view["unchanged_merge_facts"] == facts and not view["files"]
        assert manager.bases.read(change["commit"], path) == base[path]

    receipt = {r["path"]: r for r in apply_change_set(target, manager.bases, change)}
    committed = manager.bases.files(change["commit"])
    for name, entry in entries.items():
        if entry["refused"] == "path_spelling":
            assert (
                receipt[name]["status"] == "refused" and receipt[name]["reason"] == "path_spelling"
            )
            [selected] = apply_change_set(target, manager.bases, change, paths=[name])
            assert selected == receipt[name]
            assert committed.get(name) == base.get(name)
            assert entry["blob"] == entry["base_blob"]
            for root in (tree, target_dir):
                location = root / name
                actual = location.read_bytes() if location.is_file() else None
                assert actual == original_bytes[name]
            continue
        assert receipt[name]["status"] == "applied"
        data = committed[name]
        assert entry["blob"] == git_blob_id(data)
        assert entry["sha256"] == sha256(data).hexdigest()
        assert (tree / name).read_bytes() == (target_dir / name).read_bytes() == data
    if path in entries and entries[path]["refused"] == "path_spelling":
        assert judgments[0]["met"] == (path in base)
    return tree, event, entries


def test_clean_merge(tmp_path, monkeypatch):
    middle = BASE.replace(b"mid", b"MID")
    tree, event, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, middle, RIGHT], linked=[0, 2]
    )
    assert (tree / PATH).read_bytes() == BOTH.replace(b"mid", b"MID")
    entry = entries[PATH]
    assert entry["merged_steps"] == ["t0", "t1", "t2"]
    assert entry["artifact_id"] is None and entry["task_id"] is None
    assert entry["overridden_steps"] == [] and not event["superseded"]
    assert event["base_merges"] == [{"path": PATH, "merged_steps": entry["merged_steps"]}]
    assert (tree / ".harness/accepted-outputs/t0/out" / PATH).read_bytes() == LEFT
    assert (tree / ".harness/accepted-outputs/t2/out" / PATH).read_bytes() == RIGHT


def test_conflict_uses_original_priority(tmp_path, monkeypatch):
    conflicting = BASE.replace(b"last", b"other")
    tree, event, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, RIGHT, conflicting], linked=[0]
    )
    assert (tree / PATH).read_bytes() == LEFT
    assert entries[PATH]["artifact_id"] == "a0"
    assert entries[PATH]["merge_conflict"] == ["t1", "t2"]
    assert set(entries[PATH]["overridden_steps"]) == {"t1", "t2"}
    assert event["superseded"][0]["merge_conflict"] == entries[PATH]["merge_conflict"]
    assert all(b"<<<<<<<" not in p.read_bytes() for p in tree.rglob("*") if p.is_file())


def test_successor_rewrites_the_upstream_line(tmp_path, monkeypatch):
    next_version = LEFT.replace(b"FIRST", b"revised")
    tree, _, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, next_version], inputs=[(1, 0)], linked=[0]
    )
    assert (tree / PATH).read_bytes() == next_version
    assert entries[PATH]["merged_steps"] == ["t0", "t1"]
    assert "merge_conflict" not in entries[PATH]


@pytest.mark.parametrize(
    ("base", "first", "second"),
    [
        ({PATH: b"\0" + BASE}, LEFT, RIGHT),
        ({PATH: b"\xff" + BASE}, LEFT, RIGHT),
        ({PATH: BASE}, b"\xff", RIGHT),
        ({}, LEFT, RIGHT),
    ],
)
def test_ineligible_inputs_keep_the_original_winner(tmp_path, monkeypatch, base, first, second):
    tree, _, entries = judge(tmp_path, monkeypatch, base, [first, second])
    assert (tree / PATH).read_bytes() == second
    assert entries[PATH]["artifact_id"] == "a1" and entries[PATH]["overridden_steps"] == ["t0"]
    assert {"merged_steps", "merge_conflict"}.isdisjoint(entries[PATH])


@pytest.mark.parametrize(
    ("path", "alias"),
    [
        ("Dir/é.md", "Dir/e\u0301.md"),
        ("Dir/é.md", "dir/other.md"),
        ("Dir/\u1fb3\u0323.md", "Dir/\u03b1\u0323\u0345.md"),
    ],
)
def test_ambiguous_spelling_is_reported(tmp_path, monkeypatch, path, alias):
    _, event, entries = judge(
        tmp_path, monkeypatch, {path: BASE}, [LEFT, RIGHT], path=path, aliases=[(alias, b"other")]
    )
    assert {name: item["refused"] for name, item in entries.items()} == {
        path: "path_spelling",
        alias: "path_spelling",
    }
    assert entries[path]["blob"] == git_blob_id(BASE)
    assert entries[path]["merge_skipped"] == "path_spelling"
    assert not event["superseded"]
    assert {r["path"] for r in event["base_merges"] if r.get("refused") == "path_spelling"} == {
        path,
        alias,
    }


def test_no_base_keeps_original_event_and_winner(tmp_path, monkeypatch):
    tree, event, _ = judge(tmp_path, monkeypatch, None, [LEFT, RIGHT])
    assert (tree / PATH).read_bytes() == RIGHT
    assert set(event) == {
        "reason",
        "subject",
        "redirect",
        "contributions",
        "artifacts",
        "superseded",
        "detail",
    }
    [record] = event["superseded"]
    assert set(record) == {
        "path",
        "kept_task_id",
        "kept_artifact_id",
        "kept_content_hash",
        "kept_by",
        "superseded_task_ids",
        "superseded",
    }
    assert record["kept_by"] == "acceptance_order" and record["superseded_task_ids"] == ["t0"]


def test_conflict_visible_when_the_winner_matches_the_base(tmp_path, monkeypatch):
    tree, event, entries = judge(
        tmp_path,
        monkeypatch,
        {PATH: BASE},
        [RIGHT, BASE.replace(b"last", b"other"), BASE],
        linked=[2],
    )
    assert (tree / PATH).read_bytes() == BASE and not entries
    assert event["superseded"][0]["merge_conflict"] == ["t0", "t1"]


def test_independent_edit_survives_upstream_rewrite(tmp_path, monkeypatch):
    successor = LEFT.replace(b"FIRST", b"new")
    tree, _, entries = judge(
        tmp_path, monkeypatch, {PATH: BASE}, [LEFT, RIGHT, successor], inputs=[(2, 0)]
    )
    assert (tree / PATH).read_bytes() == successor.replace(b"last", b"LAST")
    assert entries[PATH]["merged_steps"] == ["t0", "t1", "t2"]


@pytest.mark.parametrize("adopted", [None, (1, 2, 3)])
def test_siblings_keep_withdrawal_even_with_an_independent_head(tmp_path, monkeypatch, adopted):
    middle = BASE.replace(b"mid", b"MID")
    tail = b"end\ntail\n"
    independent = BASE + tail + b"extra\n"
    tree, _, entries = judge(
        tmp_path,
        monkeypatch,
        {PATH: BASE + tail},
        [LEFT + tail, middle + tail, independent, BOTH + tail],
        inputs=[(1, 0), (3, 0)],
        adopted=adopted,
    )
    assert (tree / PATH).read_bytes() == middle.replace(b"last", b"LAST") + tail + b"extra\n"
    assert entries[PATH]["merged_steps"] == (["t0"] if adopted is None else []) + ["t1", "t2", "t3"]


@pytest.mark.parametrize("variant", ["both", "single", "other_target", "wrong_hash", "wrong_id"])
def test_frozen_bindings_prove_each_parent_at_this_target(tmp_path, monkeypatch, variant):
    producers = ["a", "b"] if variant == "both" else ["a"]
    outputs = [
        replace(
            bindings.output(producer=p, artifact=f"a{i}", path=PATH, producer_ordinal=i + 1),
            producer_task_ref=bindings.TaskRef(f"t{i}"),
            content_hash=sha256(LEFT).hexdigest(),
        )
        for i, p in enumerate(producers)
    ]
    if variant == "wrong_hash":
        outputs[0] = replace(outputs[0], content_hash=sha256(RIGHT).hexdigest())
    if variant == "wrong_id":
        outputs[0] = replace(outputs[0], artifact_id="absent")
    consumer = bindings.consumer_binding(
        bindings.port(
            "reports",
            cardinality=bindings.PortCardinality.SET,
            ordering=bindings.PortOrdering.BY_PRODUCER_ORDINAL,
        )
    )
    manifest = bindings.resolve(
        consumer,
        [bindings.requirement(p, producer=p, input_port="reports") for p in producers],
        bindings.index_of(*outputs),
    ).manifest
    rules = TargetRules(
        "consumer", port_prefixes={"reports": "elsewhere"} if variant == "other_target" else {}
    )
    assert manifest is not None
    assert len(manifest_upstream_inputs(manifest, rules)) == 1
    child = BASE.replace(b"first", b"child")
    tree, _, entries = judge(
        tmp_path,
        monkeypatch,
        {PATH: BASE},
        [LEFT, LEFT, child],
        linked=[0],
        frozen={"attempt2": (manifest, rules)},
    )
    row = entries[PATH]
    if variant == "both":
        assert (tree / PATH).read_bytes() == child
        assert row["merged_steps"] == ["t0", "t1", "t2"]
    else:
        assert (tree / PATH).read_bytes() == LEFT
        assert row["merge_conflict"] == (
            ["t1", "t2"] if variant == "single" else ["t0", "t1", "t2"]
        )


def test_all_conflicting_heads_are_named(tmp_path, monkeypatch):
    variants = [BASE.replace(b"first", value) for value in (b"one", b"two", b"three")]
    _, event, entries = judge(tmp_path, monkeypatch, {PATH: BASE}, [*variants, RIGHT])
    assert (
        entries[PATH]["merge_conflict"]
        == event["superseded"][0]["merge_conflict"]
        == ["t0", "t1", "t2"]
    )
    assert entries[PATH]["overridden_steps"] == ["t2", "t1", "t0"]


def test_unchanged_delivery_exposes_independent_loser(tmp_path, monkeypatch):
    _, event, entries = judge(
        tmp_path,
        monkeypatch,
        {PATH: BASE},
        [RIGHT, BASE.replace(b"last", b"other"), LEFT, BASE, BASE.replace(b"mid", b"MID")],
        inputs=[(3, 2)],
        linked=[3],
    )
    assert not entries
    [record] = event["superseded"]
    assert record["merge_conflict"] == ["t0", "t1"]
    assert set(record["superseded_task_ids"]) == {"t0", "t1", "t2", "t4"}


@pytest.mark.parametrize(("name", "alias"), [("CONFIG", "config"), ("café", "cafe\u0301")])
def test_new_ambiguous_outputs_are_refused_together(tmp_path, monkeypatch, name, alias):
    tree, event, entries = judge(
        tmp_path, monkeypatch, {}, [LEFT], path=name, aliases=[(alias, RIGHT), ("safe.md", BASE)]
    )
    for spelling in (name, alias):
        assert entries[spelling]["refused"] == "path_spelling"
        assert not (tree / spelling).exists()
        assert {
            "path": spelling,
            "merge_skipped": "path_spelling",
            "refused": "path_spelling",
        } in event["base_merges"]
    assert (tree / "safe.md").read_bytes() == BASE


def test_ambiguous_seed_is_excluded_with_the_offered_path(tmp_path, monkeypatch):
    tree, _, entries = judge(tmp_path, monkeypatch, {}, [LEFT], seed={PATH.lower(): "seed"})
    assert entries[PATH]["refused"] == "path_spelling"
    assert not (tree / PATH.lower()).exists()


def test_file_criterion_for_refused_alias_cannot_pass_on_the_base_spelling(tmp_path, monkeypatch):
    tree, _, entries = judge(
        tmp_path, monkeypatch, {PATH.lower(): BASE}, [RIGHT], aliases=[("safe.md", LEFT)]
    )
    assert entries[PATH]["refused"] == "path_spelling"
    assert (tree / PATH.lower()).read_bytes() == BASE


def test_later_head_conflicts_only_with_the_discarded_head(tmp_path, monkeypatch):
    second = LEFT.replace(b"last", b"second")
    third = BASE.replace(b"first", b"third")
    _, event, entries = judge(tmp_path, monkeypatch, {PATH: BASE}, [RIGHT, second, third])
    assert entries[PATH]["merge_conflict"] == ["t0", "t1", "t2"]
    assert event["superseded"][0]["merge_conflict"] == entries[PATH]["merge_conflict"]
