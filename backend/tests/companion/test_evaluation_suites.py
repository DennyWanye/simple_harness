from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path


def _copy_packaged_resources(target: Path) -> None:
    source = resources.files("deskpet.companion.eval_suites")
    for name in ("manifest.json", "skill_v1.json", "workflow_v1.json"):
        target.joinpath(name).write_bytes(source.joinpath(name).read_bytes())


def test_evaluation_suite_resources_are_declared_for_both_packagers() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((backend_root / "pyproject.toml").read_text("utf-8"))

    assert pyproject["tool"]["setuptools"]["package-data"]["deskpet.companion.eval_suites"] == [
        "*.json"
    ]
    spec = (backend_root / "deskpet-backend.spec").read_text("utf-8")
    # Since 304a57a73 the spec collects startup resources relative to the
    # repository root (``backend/...`` source, package-relative destination).
    assert ('("backend/deskpet/companion/eval_suites", "deskpet/companion/eval_suites")') in spec
    assert '"deskpet.companion.eval_suites"' in spec
