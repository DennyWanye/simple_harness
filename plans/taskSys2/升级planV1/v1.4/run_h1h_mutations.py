#!/usr/bin/env python3
"""Prepare and optionally run isolated H1-H mutations; never edits the source SDK.

Default mode only validates definitions and prints the plan.  ``--run`` copies the
minimum checkout into .local-test-evidence, applies one mutation per fresh copy,
runs exactly one nodeid, and classifies only a JUnit ``failure`` as KILLED.  JUnit
``error``, collection/import failure, timeout, or missing result is INVALID.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

SDK = Path("/Users/denny/projects/simple-harness-sdk-h1h-impl")
HOST = Path("/Users/denny/projects/simple_harness")
OUT = HOST / ".local-test-evidence/2026-09-22/h1h-mutations"


@dataclass(frozen=True)
class Mutation:
    id: int
    title: str
    file: str | None
    old: str | None
    new: str | None
    nodeid: str | None
    gap: str | None = None
    extra: tuple[tuple[str, str], ...] = ()
    extra_files: tuple[tuple[str, str, str], ...] = ()


M = (
    Mutation(1, "missing grant defaults allow", "src/agent_orchestrator/orchestrator/planning_admission_commits.py",
             '''if isinstance(authority, AuthoritySourceUnavailable):
        _raise(
            authority.reason_code
            if authority.reason_code in {"REQUEST_BINDING_STALE", "AUTHORIZATION_REQUIRED"}
            else "SOURCE_UNAVAILABLE",
            f"{authority.source}: {authority.detail}",
        )''',
             '''if isinstance(authority, AuthoritySourceUnavailable):
        if authority.reason_code == "AUTHORIZATION_REQUIRED":
            authority = admission.authority  # mutation: missing current grant defaults to preview authority
        else:
            _raise(
                authority.reason_code if authority.reason_code == "REQUEST_BINDING_STALE" else "SOURCE_UNAVAILABLE",
                f"{authority.source}: {authority.detail}",
            )''',
             "tests/orchestrator/full_target/test_h1h_authority_matrix.py::test_a02_never_authorized_request_is_authorization_required_and_writes_nothing"),
    Mutation(2, "ignore scope", "src/agent_orchestrator/governance/planning_authorization.py",
             'if str(binding.get("scope_id")) != str(caller.scope_id) or str(\n        binding.get("planner_principal_id")\n    ) != str(caller.principal_id):',
             'if str(binding.get("planner_principal_id")) != str(caller.principal_id):',
             "tests/orchestrator/full_target/test_h1h_authority_matrix.py::test_a03_wrong_bound_principal_or_scope_is_refused_without_leak_or_writes[principal_changes1-SCOPE_NOT_AUTHORIZED]",
             extra_files=(("src/agent_orchestrator/orchestrator/plan_commits.py",
                 '''        if command.scope_id != principal.scope_id:
            raise PlanCommitRejected(
                "SCOPE_NOT_AUTHORIZED",
                f"{principal.principal_id!r} holds scope {principal.scope_id!r} and may not "
                f"commit into {command.scope_id!r}",
            )''',
                 '''        if False and command.scope_id != principal.scope_id:
            raise PlanCommitRejected(
                "SCOPE_NOT_AUTHORIZED",
                f"{principal.principal_id!r} holds scope {principal.scope_id!r} and may not "
                f"commit into {command.scope_id!r}",
            )'''),)),
    Mutation(3, "skip commit grant reread", "src/agent_orchestrator/orchestrator/planning_admission_commits.py",
             'authority = build_planning_authorization(\n            admission.request_id,\n            read=StorePlanningAuthorityReader(PlanningAdmissionStore(store), store),\n            caller=principal,\n            policy=planning_policy_for_mission(store, command.mission_id),\n            now_ms=now_ms,\n        )',
             'authority = admission.authority',
             "tests/orchestrator/full_target/test_h1h_commit_guard.py::test_a05_expired_grant_is_rechecked_inside_commit_and_rolls_back",
             extra=((
                 'refusal = check_planning_authorization(\n        authority, decision_key=admission.decision_key, now_ms=now_ms\n    )',
                 'refusal = None  # mutation: trust the preview grant and skip commit-time TTL/active checks',
             ),)),
    Mutation(4, "operation read error becomes empty", "src/agent_orchestrator/runtime/planning_operations.py",
             'except Exception as exc:\n                raise SourceUnavailable("operation_read_failed", detail=type(exc).__name__) from exc\n            if len(bindings)',
             'except Exception:\n                return {"complete_read": True, "bindings": (), "links": (), "actions": ()}\n            if len(bindings)',
             "tests/orchestrator/full_target/test_h1h_operation_matrix.py::test_o01_store_complete_empty_has_digest_and_read_error_is_not_empty"),
    Mutation(5, "join actions by target", "src/agent_orchestrator/runtime/planning_operations.py",
             'action = action_map.get(link.action_key)\n        if action is None:',
             'linked = action_map.get(link.action_key)\n        action = next((row for row in actions if linked is not None and row.raw.get("target") == linked.raw.get("target")), linked)\n        if action is None:',
             "tests/orchestrator/full_target/test_h1h_operation_two_real_producers.py::test_o02_two_real_t0_producers_same_target_keep_exact_action_links",
             extra=((
                 '''        if (action.action_id, action.version, action.params_hash, action.idempotency_key) != (
            link.action_id,
            link.action_version,
            link.params_hash,
            link.idempotency_key,
        ):
            raise SourceUnavailable("action_link_mismatch")''',
                 '''        # mutation: target equality is incorrectly treated as sufficient identity.
        if False and (action.action_id, action.version, action.params_hash, action.idempotency_key) != (
            link.action_id,
            link.action_version,
            link.params_hash,
            link.idempotency_key,
        ):
            raise SourceUnavailable("action_link_mismatch")''',
             ),)),
    Mutation(6, "scan only active methods", "src/agent_orchestrator/runtime/planning_operations.py",
             'actions = adapter.list_operation_actions(mission_id)\n                from .operation_reconciliation import stored_negative_proof',
             '''actions = adapter.list_operation_actions(mission_id)
                # mutation: retain only producers whose task belongs to the current active Plan.
                active_tasks = {
                    str(row[0])
                    for row in self.store.connection.execute(
                        "SELECT task_id FROM plan_memberships WHERE mission_id=? AND revision="
                        "(SELECT MAX(revision) FROM plan_revisions WHERE mission_id=? AND state='ACTIVE') "
                        "AND adopted=1",
                        (mission_id, mission_id),
                    ).fetchall()
                }
                links = [row for row in links if str(row.get("producer_task_id")) in active_tasks]
                active_operations = {str(row["operation_id"]) for row in links}
                active_actions = {str(row["action_key"]) for row in links}
                bindings = [row for row in bindings if str(row["operation_id"]) in active_operations]
                actions = [row for row in actions if str(row["action_key"]) in active_actions]
                from .operation_reconciliation import stored_negative_proof''',
             "tests/orchestrator/full_target/test_h1h_retired_method_unknown_action.py::test_o03_retired_method_unknown_action_remains_visible_and_blocks"),
    Mutation(7, "trust reconcile string", "src/agent_orchestrator/orchestrator/action_commits.py",
             '''                    if (
                        reason is None
                        and rehandoff
                        and not self._planning_rehandoff_proven(action, bridge)
                    ):
                        reason = "rehandoff_needs_authoritative_not_applied_proof"''',
             '''                    if (
                        reason is None
                        and rehandoff
                        and action.get("reconcile") != "CONFIRMED_NOT_STARTED"
                    ):
                        reason = "rehandoff_needs_authoritative_not_applied_proof"''',
             "tests/orchestrator/full_target/test_h1h_action_handoff.py::test_o06_executor_weak_reconcile_rehandoff_cannot_call_connector"),
    Mutation(8, "omit action-set digest reread", "src/agent_orchestrator/orchestrator/planning_admission_commits.py",
             '''    try:
        operations = build_operation_snapshot(
            command.mission_id,
            reader=StoreOperationReader(store),
            now_ms=now_ms,
        )
    except OperationSourceUnavailable as error:
        _raise(
            "OPERATION_SNAPSHOT_STALE"
            if error.reason == "operation_mapping_incomplete"
            else "SOURCE_UNAVAILABLE",
            str(error),
        )
    if operations.read_digest != admission.operations.read_digest:
        _raise("OPERATION_SNAPSHOT_STALE", "operation bindings/links/actions changed since preview")''',
             '''    # mutation: trust the preview operation set; omit the complete commit-time reread.
    operations = admission.operations''',
             "tests/orchestrator/full_target/test_h1h_commit_guard.py::test_o08_new_action_after_preview_is_detected_by_complete_set_reread"),
    Mutation(9, "drop refinement cycle", "src/agent_orchestrator/planning/htn/validation.py",
             'for problem in refinement_report.problems:\n        problems.append(\n            DeltaProblem(\n                kind=DeltaProblemKind.REFINEMENT_CYCLE,\n                detail=problem.detail,\n                subjects=problem.nodes,\n            )\n        )',
             'for problem in ():\n        raise AssertionError(problem)',
             "tests/orchestrator/full_target/test_h1h_p02_compiler_cycles.py::test_p02_cycle_refusal_preserves_store_budget_events_and_files[refinement-cycle]",
             extra_files=(("src/agent_orchestrator/planning/plan_preview.py",
                 '''    if error.refinement_report is not None:
        for problem in error.refinement_report.problems:
            kind = str(problem.kind).split(".")[-1].upper()
            mapped.append("REFINEMENT_CYCLE" if kind == "CYCLE" else "INTERNAL_CONTRACT_ERROR")''',
                 '''    if error.refinement_report is not None:
        for problem in error.refinement_report.problems:
            mapped.append("INTERNAL_CONTRACT_ERROR")'''),)),
    Mutation(10, "NOT_CHECKED passes", "src/agent_orchestrator/planning/plan_preview.py",
             'else:\n            try:\n                mapped.append(_DELTA_KIND_TO_CODE[problem.kind])',
             'elif problem.kind is DeltaProblemKind.NOT_CHECKED:\n            continue\n        else:\n            try:\n                mapped.append(_DELTA_KIND_TO_CODE[problem.kind])',
             "tests/orchestrator/full_target/test_h1h_preview_problem_mapping.py::test_p10_mapping_tables_cover_every_current_typed_problem_kind",
             extra=((
                 '    DeltaProblemKind.NOT_CHECKED: "INTERNAL_CONTRACT_ERROR",\n',
                 '',
             ),)),
    Mutation(11, "preview calls legacy apply path", "src/agent_orchestrator/orchestrator/hierarchical_dispatch.py",
             '''    @staticmethod
    def preview_plan_proposal(proposal: PlanProposal, *, inputs: Any) -> Any:
        """Run the H1H pure candidate preview without entering dispatch/commit.

        The live ``compile_proposal`` method remains the legacy shell.  This
        explicit seam makes it impossible for preview callers to accidentally
        invoke ``apply_planner_reply`` or its reconciliation side effects.
        """

        from ..planning.plan_preview import preview_candidate

        return preview_candidate(proposal, inputs=inputs)''',
             '''    def preview_plan_proposal(self, proposal: PlanProposal, *, inputs: Any) -> Any:
        # mutation: enter the old mutating path once, then return the real preview result.
        if not getattr(self, "_mutation_preview_applied", False):
            self.apply_plan_proposal(
                str(inputs.network.mission_id),
                proposal,
                principal=PlanPrincipal(
                    principal_id=str(inputs.system_identity_seed),
                    scope_id="mission",
                    manager_epoch=self.semantics().epoch(str(inputs.network.mission_id), "mission"),
                ),
                command_id=f"mutation-preview:{inputs.request_id}",
                source={"mutation": "legacy_apply_during_preview"},
            )
            self._mutation_preview_applied = True
        from ..planning.plan_preview import preview_candidate
        return preview_candidate(proposal, inputs=inputs)''',
             "tests/orchestrator/full_target/test_h1h_preview_purity.py::test_p05_same_frozen_production_preview_is_deterministic_and_side_effect_free"),
    Mutation(12, "state-free enters plan commit", "src/agent_orchestrator/orchestrator/event_handler.py",
             'if isinstance(pre_admitted, NoMutationDecision):\n            with self.store.transaction():',
             '''if isinstance(pre_admitted, NoMutationDecision):
            # mutation: state-free decisions incorrectly enter the real commit seam.
            new_mode.commit_preview_plan_proposal(
                mission.id, None, preview=None, admission=None,
                principal=PlanPrincipal(self._owner, "mission", 0),
                command_id=f"mutation-state-free:{decision_id}",
            )
            with self.store.transaction():''',
             "tests/orchestrator/full_target/test_h1h_nonmutating_collect.py::test_p08_state_free_decisions_use_real_collector_without_shape_or_operation_preview[WAIT]"),
)


def copy_checkout(dst: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"refusing to overwrite existing mutation evidence: {dst}")
    ignored = shutil.ignore_patterns(
        ".git", ".venv", ".local-test-evidence", "__pycache__", "*.pyc",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build",
    )
    # Copy the checkout rather than a hand-picked package subset: wheel metadata,
    # LICENSES/ and NOTICE files remain available to uv's frozen build backend.
    shutil.copytree(SDK, dst, ignore=ignored)


def apply(m: Mutation, dst: Path) -> None:
    path = dst / str(m.file)
    text = path.read_text()
    for old, new in ((str(m.old), str(m.new)), *m.extra):
        if text.count(old) != 1:
            raise RuntimeError(f"M{m.id:02}: expected exactly one source match, got {text.count(old)}")
        text = text.replace(old, new, 1)
    path.write_text(text)
    for file, old, new in m.extra_files:
        path = dst / file
        text = path.read_text()
        if text.count(old) != 1:
            raise RuntimeError(f"M{m.id:02}: expected exactly one match in {file}, got {text.count(old)}")
        path.write_text(text.replace(old, new, 1))


def classify(xml: Path, timed_out: bool, returncode: int) -> tuple[str, dict[str, str]]:
    if timed_out or not xml.exists():
        return "INVALID", {"failure_type": "timeout_or_missing_junit", "traceback": ""}
    root = ET.parse(xml).getroot()
    cases = root.findall(".//testcase")
    failures = root.findall(".//failure")
    errors = root.findall(".//error")
    if len(cases) == 1 and len(failures) == 1 and not errors and returncode == 1:
        failure = failures[0]
        declared_type = failure.get("type", "")
        message = failure.get("message", "")
        trace = (failure.text or "")[-12000:]
        combined = message + "\n" + trace
        # Rewritten bare asserts have only ``E assert ...``; pytest still
        # records the terminal exception in ``file.py:line: AssertionError``.
        terminal = re.search(r"(?:^|\n)[^\n]+:\d+: ([A-Za-z_][\w.]*)\s*$", trace)
        if terminal is not None:
            kind = terminal.group(1).rsplit(".", 1)[-1]
            return ("KILLED" if kind in {"AssertionError", "Failed"} else "INVALID"), {
                "failure_type": kind, "traceback": trace,
            }
        # Default pytest JUnit commonly omits failure.type.  The final ``E``
        # exception line is more reliable than the XML message and prevents an
        # unrelated downstream exception from being counted as a kill.
        exception_lines = re.findall(r"(?m)^E\s+([\w.]+)(?::|$)", combined)
        inferred = exception_lines[-1].rsplit(".", 1)[-1] if exception_lines else declared_type
        if inferred in {"AssertionError", "Failed"}:
            return "KILLED", {"failure_type": inferred, "traceback": trace}
        return "INVALID", {"failure_type": inferred or "unknown_failure", "traceback": trace}
    if errors or len(cases) != 1 or returncode not in (0, 1):
        trace = "\n".join((item.text or "")[-4000:] for item in errors)
        return "INVALID", {"failure_type": "junit_error_or_collection", "traceback": trace}
    return "SURVIVED", {"failure_type": "", "traceback": ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--only", type=int, action="append")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    selected = [m for m in M if not args.only or m.id in args.only]
    if not args.run:
        print(json.dumps([m.__dict__ for m in selected], ensure_ascii=False, indent=2))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:10]
    run_root = OUT / run_id
    run_root.mkdir()  # unique id plus mkdir-without-exist_ok: never overwrite evidence
    results = []
    for m in selected:
        if m.gap:
            results.append({"id": m.id, "status": "GAP", "detail": m.gap})
            continue
        dst = run_root / f"m{m.id:02d}"
        copy_checkout(dst)
        try:
            apply(m, dst)
            xml = dst / "result.xml"
            command = ["uv", "run", "--frozen", "pytest", "-q", str(m.nodeid), f"--junitxml={xml}"]
            timed_out = False
            try:
                done = subprocess.run(command, cwd=dst, text=True, capture_output=True, timeout=args.timeout)
                code, output = done.returncode, done.stdout + done.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out, code, output = True, 124, (exc.stdout or "") + (exc.stderr or "")
            (dst / "pytest.log").write_text(output)
            status, diagnostic = classify(xml, timed_out, code)
            results.append({"id": m.id, "status": status, "returncode": code, **diagnostic})
        except Exception as exc:
            results.append({"id": m.id, "status": "INVALID", "detail": str(exc)})
    (run_root / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
