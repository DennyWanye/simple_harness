from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from importlib import resources
from pathlib import Path

import pytest

from deskpet.companion.evaluation_suites import (
    PackagedEvaluationSuiteError,
    PackagedEvaluationSuiteLoader,
)


def _copy_packaged_resources(target: Path) -> None:
    source = resources.files("deskpet.companion.eval_suites")
    for name in ("manifest.json", "skill_v1.json", "workflow_v1.json"):
        target.joinpath(name).write_bytes(source.joinpath(name).read_bytes())


def test_packaged_manifest_attests_skill_and_workflow_suites() -> None:
    manifest = PackagedEvaluationSuiteLoader().validate_release()

    assert manifest.manifest_id == "deskpet.companion.eval-suites.v1"
    assert [suite.suite_id for suite in manifest.suites] == [
        "skill_v1",
        "workflow_v1",
    ]
    assert {
        case.case_id for case in manifest.suite("skill_v1").cases if case.required
    } == {
        "skill.memory_recall.summary",
        "skill.no_live_memory_fallback",
    }
    assert {
        case.case_id
        for case in manifest.suite("workflow_v1").cases
        if case.required
    } == {
        "workflow.build_drift_fail_closed",
        "workflow.readonly_memory_step",
    }


def test_packaged_loader_rejects_stale_resource_hash(tmp_path: Path) -> None:
    _copy_packaged_resources(tmp_path)
    with tmp_path.joinpath("skill_v1.json").open("ab") as stream:
        stream.write(b"\n")

    with pytest.raises(PackagedEvaluationSuiteError) as exc_info:
        PackagedEvaluationSuiteLoader(resource_root=tmp_path).validate_release()

    assert exc_info.value.code == "evaluation_suite_resource_hash_mismatch"


def test_packaged_loader_rejects_unmanifested_json(tmp_path: Path) -> None:
    _copy_packaged_resources(tmp_path)
    shutil.copyfile(
        tmp_path / "skill_v1.json",
        tmp_path / "unreviewed_v1.json",
    )

    with pytest.raises(PackagedEvaluationSuiteError) as exc_info:
        PackagedEvaluationSuiteLoader(resource_root=tmp_path).validate_release()

    assert exc_info.value.code == "evaluation_suite_resource_set_mismatch"
    assert "unreviewed_v1.json" in exc_info.value.detail


def test_packaged_loader_rejects_stale_assertions_hash(tmp_path: Path) -> None:
    _copy_packaged_resources(tmp_path)
    suite_path = tmp_path / "skill_v1.json"
    suite = json.loads(suite_path.read_text("utf-8"))
    suite["cases"][0]["assertions"][0]["tool_name"] = "changed_tool"
    suite_path.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["suites"][0]["resource_hash"] = hashlib.sha256(
        suite_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PackagedEvaluationSuiteError) as exc_info:
        PackagedEvaluationSuiteLoader(resource_root=tmp_path).validate_release()

    assert exc_info.value.code == "evaluation_suite_assertions_hash_mismatch"


def test_evaluation_suite_resources_are_declared_for_both_packagers() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((backend_root / "pyproject.toml").read_text("utf-8"))

    assert pyproject["tool"]["setuptools"]["package-data"][
        "deskpet.companion.eval_suites"
    ] == ["*.json"]
    spec = (backend_root / "deskpet-backend.spec").read_text("utf-8")
    assert (
        '("deskpet/companion/eval_suites", '
        '"deskpet/companion/eval_suites")'
    ) in spec
    assert '"deskpet.companion.eval_suites"' in spec
