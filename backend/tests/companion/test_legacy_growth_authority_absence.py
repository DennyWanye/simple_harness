from __future__ import annotations

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]

PRODUCTION_AUTHORITY_FILES = (
    BACKEND / "main.py",
    BACKEND / "pipeline" / "voice_pipeline.py",
    BACKEND / "deskpet" / "agent" / "run_presenter.py",
    BACKEND / "agent" / "agent_loop.py",
    BACKEND / "context.py",
    BACKEND / "agent" / "harness_feedback.py",
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


def test_voice_pipeline_has_no_private_codifier_worker_or_saved_config() -> None:
    path = BACKEND / "pipeline" / "voice_pipeline.py"
    tree = ast.parse(_source(path), filename=str(path))
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assigned_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets
            if isinstance(node, ast.Assign)
            else (node.target,)
        )
        if isinstance(target, ast.Name)
    }
    assigned_attributes = {
        target.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets
            if isinstance(node, ast.Assign)
            else (node.target,)
        )
        if isinstance(target, ast.Attribute)
    }
    assert {
        "_maybe_codify_voice",
        "_codify_worker",
    }.isdisjoint(function_names)
    assert "_codify_tasks" not in assigned_names | assigned_attributes
    assert "app_config" not in assigned_names | assigned_attributes


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
                    findings.append(
                        f"{path.relative_to(BACKEND)}:{node.lineno}:{token}"
                    )
    assert findings == []


def test_legacy_writer_modules_are_physically_absent() -> None:
    assert [str(path.relative_to(BACKEND)) for path in REMOVED_LEGACY_MODULES if path.exists()] == []


def test_turn_preparer_has_no_legacy_preference_composition_surface() -> None:
    path = BACKEND / "deskpet" / "agent" / "turn_preparer.py"
    source = _source(path)
    tree = ast.parse(source, filename=str(path))
    constructor = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "__init__"
        and any(
            isinstance(parent, ast.ClassDef)
            and parent.name == "ProductTurnPreparer"
            and node in parent.body
            for parent in ast.walk(tree)
        )
    )
    names = {
        argument.arg
        for argument in (
            *constructor.args.args,
            *constructor.args.kwonlyargs,
        )
    }
    assert {
        "preference_resolver",
        "preference_owner_provider",
        "request_preference_override_provider",
        "relevant_preference_keys_provider",
        "owner_memory_read_scope_provider",
        "companion_selection_provider",
    }.isdisjoint(names)
    assert "_preference_resolver" not in source
    assert "_preference_owner_provider" not in source


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
        node
        for node in host.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
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
        source.index("class SkillsCodifyConfig:")
        : source.index("class ProblemPipelineConfig:")
    ]
    assert "只读解析旧" in section
    assert "不能重新开启" in section
    assert "dev on / prod off" not in section


def test_completed_cutover_reconciles_process_local_reminder_registry() -> None:
    source = _source(BACKEND / "main.py")
    complete = source[
        source.index("async def _complete_growth_authority_cutover()")
        : source.index("async def _initialize_companion_projection_services()")
    ]
    assert "GrowthAuthorityPhase.COMPANION" in complete
    assert "companion_reminder_registry_reconciler" in complete
    assert "reconciler()" in complete


def test_completed_cutover_restores_reminder_specs_before_harness_recovery() -> None:
    source = _source(BACKEND / "main.py")
    initialize = source[
        source.index("async def _initialize_growth_authority()")
        : source.index("async def _complete_growth_authority_cutover()")
    ]
    assert 'router.current.phase.value == "companion"' in initialize
    assert "restore_durable_companion_reminder_tools(" in initialize


def test_cutover_plan_uses_real_capability_owner_snapshot_without_publish() -> None:
    source = _source(BACKEND / "main.py")
    authority = source[
        source.index("async def _initialize_growth_authority()")
        : source.index("async def _complete_growth_authority_cutover()")
    ]

    assert "read_capability_owner_cutover_snapshot(" in authority
    assert (
        "old_binding_generation=capability_snapshot.binding_generation"
        in authority
    )
    assert (
        "new_binding_generation=capability_snapshot.binding_generation"
        in authority
    )
    assert authority.count(
        "capability_snapshot.owner_binding_set_stamp"
    ) >= 2
    assert '"capability_mutation": "none"' in authority
    assert "old_binding_generation=0" not in authority
    assert "new_binding_generation=0" not in authority


def test_profile_bind_provisions_owner_inbox_before_identity_broadcast() -> None:
    source = _source(BACKEND / "main.py")
    control = source[source.index('if msg_type == "companion_profile_bind":') :]
    bind_branch = control[: control.index(
        'elif msg_type == "companion_profile_unbind":'
    )]
    provision = control.index("await _ensure_companion_inbox_route(")
    response = control.index('"session_id": _inbox_session_id')
    send = control.index("await ws.send_json(response)")
    broadcast = control.index('"type": "companion_identity_status"')

    assert provision < response < send < broadcast
    assert "_trigger_harness_recovery_after_identity_bind()" in bind_branch
    assert (
        bind_branch.index("_companion_identity_gate.freeze()")
        < bind_branch.index("_trigger_harness_recovery_after_identity_bind()")
    )
