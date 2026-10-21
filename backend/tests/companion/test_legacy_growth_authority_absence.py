from __future__ import annotations

import ast
from pathlib import Path

from tests.host_source import (
    host_function_source,
    host_module_source,
    host_source_files,
    host_source_text,
)

BACKEND = Path(__file__).resolve().parents[2]

PRODUCTION_AUTHORITY_FILES = (
    *host_source_files(),
    BACKEND / "deskpet" / "agent" / "run_presenter.py",
    BACKEND / "context.py",
)

FORBIDDEN_PRODUCTION_REFERENCES = (
    "ToolPathRecorder",
    "SkillCodifier",
    "tool_path_recorder",
    "skill_codifier",
    "skill_candidate_confirm",
    "skill_candidate_proposed",
    "codify_skill",
)

REMOVED_LEGACY_MODULES = (
    BACKEND / "deskpet" / "agent" / "tool_path.py",
    BACKEND / "deskpet" / "skills" / "candidate_proposal.py",
    BACKEND / "deskpet" / "skills" / "skill_codifier.py",
    BACKEND / "tools" / "reminder.py",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_legacy_codifier_and_tool_path_authority_is_absent_from_production() -> None:
    findings: list[str] = []
    for path in PRODUCTION_AUTHORITY_FILES:
        source = _source(path)
        for token in FORBIDDEN_PRODUCTION_REFERENCES:
            if token in source:
                findings.append(f"{path.relative_to(BACKEND)}:{token}")
    assert findings == []


def test_removed_authority_names_are_not_reintroduced_as_dynamic_strings() -> None:
    """Prevent getattr/service lookup from bypassing the direct-name check."""

    findings: list[str] = []
    for path in PRODUCTION_AUTHORITY_FILES:
        tree = ast.parse(_source(path), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for token in FORBIDDEN_PRODUCTION_REFERENCES:
                if token in node.value:
                    findings.append(f"{path.relative_to(BACKEND)}:{node.lineno}:{token}")
    assert findings == []


def test_legacy_writer_modules_are_physically_absent() -> None:
    assert [
        str(path.relative_to(BACKEND)) for path in REMOVED_LEGACY_MODULES if path.exists()
    ] == []


def test_legacy_skill_loader_has_no_implicit_production_source() -> None:
    source = _source(BACKEND / "deskpet" / "skills" / "loader.py")
    assert "_default_skill_dirs" not in source
    assert "DESKPET_SKILLS_DIR" not in source
    assert "legacy_read_only_source = True" in source


def test_builder_has_no_optional_dormant_candidate_output_port() -> None:
    source = _source(BACKEND / "deskpet" / "capabilities" / "builder.py")
    tree = ast.parse(source, filename="builder.py")
    host = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CapabilityBuilderHost"
    )
    constructor = next(
        node for node in host.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    names = {
        argument.arg
        for argument in (
            *constructor.args.args,
            *constructor.args.kwonlyargs,
        )
    }
    assert "candidate_output_port" not in names
    assert "CandidateDraftReceiptBuildOutput" in source


def test_legacy_codify_config_is_compatibility_only() -> None:
    source = _source(BACKEND / "config.py")
    section = source[
        source.index("class SkillsCodifyConfig:") : source.index("class ProblemPipelineConfig:")
    ]
    assert "只读解析旧" in section
    assert "不能重新开启" in section
    assert "dev on / prod off" not in section


def test_completed_cutover_reconciles_process_local_reminder_registry() -> None:
    complete = host_function_source("_complete_growth_authority_cutover")
    assert "GrowthAuthorityPhase.COMPANION" in complete
    assert "companion_reminder_registry_reconciler" in complete
    assert "reconciler()" in complete


def test_completed_cutover_restores_reminder_specs_before_harness_recovery() -> None:
    initialize = host_module_source("growth_authority")
    assert 'router.current.phase.value == "companion"' in initialize
    assert "restore_durable_companion_reminder_tools(" in initialize


def test_cutover_plan_uses_real_capability_owner_snapshot_without_publish() -> None:
    authority = host_module_source("growth_authority")

    assert "read_capability_owner_cutover_snapshot(" in authority
    assert "old_binding_generation=capability_snapshot.binding_generation" in authority
    assert "new_binding_generation=capability_snapshot.binding_generation" in authority
    assert authority.count("capability_snapshot.owner_binding_set_stamp") >= 2
    assert '"capability_mutation": "none"' in authority
    assert "old_binding_generation=0" not in authority
    assert "new_binding_generation=0" not in authority


def test_profile_bind_provisions_owner_inbox_before_identity_broadcast() -> None:
    source = host_source_text()
    control = source[source.index('if msg_type == "companion_profile_bind":') :]
    # The first bind branch only records the human-memory control binding;
    # the identity freeze / inbox projection branch is the next one.
    settle = control.index(
        'if msg_type == "companion_profile_bind":',
        len('if msg_type == "companion_profile_bind":'),
    )
    # 绑定分支到下一段"就绪帧广播"（if msg_type in {...}）为止；旧的 elif unbind 分支已随旧语音通道删除
    bind_branch = control[settle : control.index("if msg_type in {", settle)]
    # Since 00b2d0829 (事件 AK) provisioning is bounded inside
    # _settle_companion_bind_projection, which receives the inbox-route
    # provisioner; the routed session id is then folded into the response.
    provision = control.index("await _settle_companion_bind_projection(")
    assert "ensure_route=_ensure_companion_inbox_route" in bind_branch
    response = control.index('_bind_payload["session_id"] = _inbox_session_id')
    send = control.index("await ws.send_json(response)")
    broadcast = control.index('"type": "companion_identity_status"')

    assert provision < response < send < broadcast
    assert "_trigger_harness_recovery_after_identity_bind()" in bind_branch
    assert bind_branch.index("host_state.companion_identity_gate.freeze()") < bind_branch.index(
        "_trigger_harness_recovery_after_identity_bind()"
    )
