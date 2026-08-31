#!/usr/bin/env python3
"""Clean-wheel black-box runner for sealed audit and canonical manifests.

The child consumer imports only ``simple_harness`` and
``simple_harness_memory`` package roots.  No source checkout, private module,
repository object, direct SQL, or product test helper is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


BLOCKED_EXIT = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _python_in_venv(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fixture.get("fixture_revision") != 1:
        errors.append("fixture revision must be 1")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("semantic quality must remain NOT_RUN/BLOCKED")
    evidence = fixture.get("evidence", {})
    if _sha256_bytes(_canonical_bytes(evidence.get("raw_source_payload"))) != evidence.get(
        "raw_source_sha256"
    ):
        errors.append("raw source known-answer hash mismatch")
    if _sha256_bytes(_canonical_bytes(evidence.get("sanitized_payload"))) != evidence.get(
        "sanitized_sha256"
    ):
        errors.append("sanitized payload known-answer hash mismatch")
    serialized_sanitized = _canonical_bytes(evidence.get("sanitized_payload")).decode(
        "utf-8"
    )
    for canary in evidence.get("forbidden_canaries", []):
        if canary in serialized_sanitized:
            errors.append(f"forbidden canary survived sanitized payload: {canary}")
    for candidate in fixture.get("candidate_identity", {}).values():
        if not HEX64.fullmatch(candidate.get("wheel_sha256", "")):
            errors.append("candidate wheel SHA must be lowercase hex64")
        if not candidate.get("distribution") or not candidate.get("version"):
            errors.append("candidate distribution/version missing")
    memory = fixture["candidate_identity"]["memory"]
    if memory.get("wheel_sha256") != memory.get(
        "reproducible_second_wheel_sha256"
    ):
        errors.append("Task 7 reproducible wheel hashes differ")
    if fixture.get("audit", {}).get("scope_kind") != "subject":
        errors.append("sealed audit fixture must use subject scope")
    if errors:
        return {"status": "FAIL", "errors": errors}
    return {
        "status": "PASS",
        "fixture_revision": fixture["fixture_revision"],
        "fixture_sha256": _sha256_bytes(fixture_path.read_bytes()),
        "quality_gate": fixture["quality_gate"],
        "raw_source_hash_bound_without_raw_disclosure": True,
        "forbidden_canaries": len(evidence["forbidden_canaries"]),
        "product_execution": "NOT_RUN/BLOCKED",
    }


PUBLIC_CONSUMER = r'''
import asyncio
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import time

import simple_harness as harness
import simple_harness_memory as memory


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def main():
    config = json.loads(sys.argv[1])
    db_path = Path(sys.argv[2])
    principal_data = config["principal"]
    evidence_data = config["evidence"]
    audit_data = config["audit"]
    now = time.time()
    principal = memory.MemoryPrincipal(
        principal_data["deployment_id"],
        principal_data["household_id"],
        principal_data["actor_id"],
        principal_data["session_id"],
    )
    subject = principal_data["subject"]
    ordinary_context = harness.DisclosureContext.from_json({
        "schema_version": 1,
        "run_id": evidence_data["run_id"],
        "subject": subject,
        "recipient": "user_self",
        "recipient_id": principal_data["actor_id"],
        "intended_audience": "user_self",
        "purpose": "user_review",
        "source": "authenticated_host",
        "trust": "trusted_authority",
        "generation": "current",
        "authority_ref": "host-evidence-authority-1",
        "reason_codes": ["disclosure_minimum_necessary"],
    })
    envelope = harness.SanitizedEvidenceEnvelope(
        evidence_id=evidence_data["evidence_id"],
        run_id=evidence_data["run_id"],
        subject=subject,
        source_kind=harness.EvidenceSourceKind.USER_MESSAGE,
        source_ref=evidence_data["source_ref"],
        source_hash=evidence_data["raw_source_sha256"],
        sanitized_payload=evidence_data["sanitized_payload"],
        sanitized_hash=evidence_data["sanitized_sha256"],
        filter_policy_version=evidence_data["filter_policy_version"],
        removed_spans=(
            harness.RemovedSpanSummary(harness.RemovedSpanType.API_KEY, 1),
        ),
        disclosure_context=ordinary_context,
        evidence_refs=(),
    )
    admission = harness.SanitizedEvidenceReceipt(
        receipt_id="admission-sealed-1",
        run_id=evidence_data["run_id"],
        subject=subject,
        evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,
        source_hash=evidence_data["raw_source_sha256"],
        sanitized_hash=evidence_data["sanitized_sha256"],
        filter_policy_version=evidence_data["filter_policy_version"],
        accepted=True,
        reason_codes=(harness.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=ordinary_context,
        evidence_refs=(),
        admitted_at=now,
    )
    audit_context = harness.DisclosureContext.from_json({
        "schema_version": 1,
        "run_id": "audit-run-1",
        "subject": subject,
        "recipient": "audit_reviewer",
        "recipient_id": principal_data["actor_id"],
        "intended_audience": "auditor",
        "purpose": "audit",
        "source": "audit_access_decision",
        "trust": "trusted_authority",
        "generation": "current",
        "authority_ref": audit_data["authority_id"],
        "reason_codes": ["disclosure_audit_grant_required"],
    })
    decision = memory.SealedAuditAccessDecision(
        decision_id=audit_data["decision_id"],
        subject=subject,
        scope_kind=audit_data["scope_kind"],
        scope_ref=subject,
        reason_code=audit_data["reason_code"],
        disclosure_context=audit_context,
        max_reads=audit_data["max_reads"],
        issued_at=now + audit_data["issued_offset_seconds"],
        expires_at=now + audit_data["expires_offset_seconds"],
    )
    authority_ref = memory.AuditAccessAuthorityRefV1(
        authority_id=audit_data["authority_id"],
        issuer_ref="host-audit-authority",
        nonce=audit_data["nonce"],
        replay_identity=audit_data["replay_identity"],
        requester_deployment_id=principal.deployment_id,
        requester_household_id=principal.household_id,
        requester_actor_id=principal.actor_id,
        requester_session_id=principal.session_id,
        target_deployment_id=principal.deployment_id,
        target_household_id=principal.household_id,
        target_actor_id=principal.actor_id,
        target_subject=subject,
        decision_id=decision.decision_id,
        decision_hash=decision.decision_hash,
        scope_kind=audit_data["scope_kind"],
        scope_ref=subject,
        issued_at=decision.issued_at,
        expires_at=decision.expires_at,
    )

    class Authority:
        async def resolve_audit_access(self, reference):
            if reference.ref_hash != authority_ref.ref_hash:
                raise ValueError("audit authority reference mismatch")
            return decision

    async def build():
        return await memory.build_human_memory_v6(db_path, audit_access_authority=Authority())

    manager = await build()
    try:
        ingestion = await manager.ingest_committed_evidence(envelope, admission)
        access_receipt = await manager.authorize_audit_access(
            principal=principal, authority_ref=authority_ref
        )
        sealed_evidence = await manager.export_sealed_evidence(
            requester=principal,
            evidence_id=envelope.evidence_id,
            access_receipt=access_receipt,
        )
        trace_query = memory.AuditTraceQuery(
            subject=subject,
            selector=memory.AuditTraceSelector(audit_data["trace_selector"]),
            selector_ref=envelope.evidence_id,
        )
        sealed_trace = await manager.export_sealed_audit_trace(
            requester=principal,
            query=trace_query,
            access_receipt=access_receipt,
            limit=20,
        )
        first_access = await manager.export_canonical_state_manifest(
            requester=principal,
            target_principal=principal,
            access_receipt=access_receipt,
        )
    finally:
        await manager.close()

    reopened = await build()
    try:
        second_access = await reopened.export_canonical_state_manifest(
            requester=principal,
            target_principal=principal,
            access_receipt=access_receipt,
        )
    finally:
        await reopened.close()

    def manifest_payload(access):
        manifest = access.manifest
        return {
            "manifest": manifest.to_json(),
            "payload_hash": manifest.payload_hash,
            "access_event_hash": access.access_event_hash,
        }

    identity = {}
    for root, distribution in (
        ("simple_harness", config["candidate_identity"]["harness"]["distribution"]),
        ("simple_harness_memory", config["candidate_identity"]["memory"]["distribution"]),
    ):
        module = importlib.import_module(root)
        identity[root] = {
            "distribution": distribution,
            "version": metadata.version(distribution),
            "module_origin": str(module.__file__),
        }
    result = {
        "status": "PASS",
        "identity": identity,
        "ingestion": {
            "evidence_id": ingestion.evidence_id,
            "source_hash": ingestion.source_hash,
            "sanitized_hash": ingestion.sanitized_hash,
            "envelope_hash": ingestion.envelope_hash,
            "receipt_hash": ingestion.receipt_hash,
        },
        "access_receipt": {
            **access_receipt.to_json(),
            "receipt_hash": access_receipt.receipt_hash,
        },
        "sealed_evidence": {
            "envelope": sealed_evidence.envelope.to_json(),
            "admission_receipt": sealed_evidence.admission_receipt.to_json(),
            "ingestion_receipt_hash": sealed_evidence.ingestion_receipt.receipt_hash,
            "span_count": len(sealed_evidence.spans),
        },
        "sealed_trace": {
            "item_count": len(sealed_trace.items),
            "page_hash": sealed_trace.page_hash,
            "has_next_cursor": sealed_trace.next_cursor is not None,
        },
        "first_manifest": manifest_payload(first_access),
        "reopened_manifest": manifest_payload(second_access),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


asyncio.run(main())
'''


def _validate_manifest(
    payload: dict[str, Any], oracle: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    manifest = payload.get("manifest", {})
    if not HEX64.fullmatch(payload.get("payload_hash", "")):
        errors.append("manifest payload hash is not hex64")
    if not HEX64.fullmatch(payload.get("access_event_hash", "")):
        errors.append("manifest access event hash is not hex64")
    if manifest.get("payload_hash") not in {None, payload.get("payload_hash")}:
        errors.append("manifest payload hash field differs")
    for field in ("schema_checksum", "initialization_receipt_hash", "principal_ref_hash"):
        if not HEX64.fullmatch(manifest.get(field, "")):
            errors.append(f"manifest {field} is not hex64")
    roots = manifest.get("table_roots")
    if not isinstance(roots, list) or len(roots) < oracle["minimum_table_roots"]:
        errors.append("manifest table roots missing")
        return {}, errors
    by_name: dict[str, dict[str, Any]] = {}
    required = set(oracle["required_table_root_fields"])
    for row in roots:
        if set(row) != required:
            errors.append("manifest table root fields differ")
            continue
        name = row.get("table_name")
        if not isinstance(name, str) or not name or name in by_name:
            errors.append("manifest table root name invalid or duplicate")
            continue
        if not isinstance(row.get("row_count"), int) or row["row_count"] < 0:
            errors.append(f"manifest row count invalid: {name}")
        for field in ("root_hash", "first_leaf_hash", "last_leaf_hash"):
            value = row.get(field)
            if value is not None and not HEX64.fullmatch(value):
                errors.append(f"manifest {field} invalid: {name}")
        by_name[name] = row
    if manifest.get("total_row_count", 0) < oracle["minimum_total_row_count"]:
        errors.append("manifest total row count below frozen minimum")
    if sum(row["row_count"] for row in by_name.values()) != manifest.get(
        "total_row_count"
    ):
        errors.append("manifest total row count differs from table roots")
    return by_name, errors


def _validate_result(
    result: dict[str, Any], fixture: dict[str, Any], isolated_root: Path
) -> list[str]:
    errors: list[str] = []
    if result.get("status") != "PASS":
        return ["child public consumer did not PASS"]
    identities = result.get("identity", {})
    mapping = {"simple_harness": "harness", "simple_harness_memory": "memory"}
    for root, key in mapping.items():
        identity = identities.get(root, {})
        pin = fixture["candidate_identity"][key]
        if identity.get("distribution") != pin["distribution"] or identity.get(
            "version"
        ) != pin["version"]:
            errors.append(f"installed identity mismatch: {root}")
        try:
            Path(identity["module_origin"]).resolve().relative_to(isolated_root.resolve())
        except (KeyError, TypeError, ValueError):
            errors.append(f"module origin escaped isolated venv: {root}")
    evidence = fixture["evidence"]
    ingestion = result.get("ingestion", {})
    if ingestion.get("source_hash") != evidence["raw_source_sha256"]:
        errors.append("ingestion source hash lost raw binding")
    if ingestion.get("sanitized_hash") != evidence["sanitized_sha256"]:
        errors.append("ingestion sanitized hash mismatch")
    sealed = result.get("sealed_evidence", {})
    if sealed.get("envelope", {}).get("sanitized_payload") != evidence[
        "sanitized_payload"
    ]:
        errors.append("sealed evidence payload differs from sanitized input")
    if sealed.get("span_count", 0) < 1:
        errors.append("sealed evidence spans missing")
    for value in (
        ingestion.get("receipt_hash", ""),
        result.get("access_receipt", {}).get("receipt_hash", ""),
        sealed.get("ingestion_receipt_hash", ""),
        result.get("sealed_trace", {}).get("page_hash", ""),
    ):
        if not HEX64.fullmatch(value):
            errors.append("sealed receipt/trace hash is not hex64")
    first, first_errors = _validate_manifest(
        result.get("first_manifest", {}), fixture["manifest_oracle"]
    )
    reopened, reopened_errors = _validate_manifest(
        result.get("reopened_manifest", {}), fixture["manifest_oracle"]
    )
    errors.extend(first_errors)
    errors.extend(reopened_errors)
    if set(first) != set(reopened):
        errors.append("reopened manifest table set differs")
    for name in set(first) & set(reopened):
        before, after = first[name], reopened[name]
        if after["row_count"] < before["row_count"]:
            errors.append(f"reopened canonical row count decreased: {name}")
        if after["row_count"] == before["row_count"] and after["root_hash"] != before[
            "root_hash"
        ]:
            errors.append(f"root changed without row-count change: {name}")
        if after["row_count"] > before["row_count"] and after["root_hash"] == before[
            "root_hash"
        ]:
            errors.append(f"root unchanged after row-count growth: {name}")
    return errors


def execute(args: argparse.Namespace, fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    paths = {
        "harness": Path(args.harness_wheel).resolve(),
        "memory": Path(args.memory_wheel).resolve(),
    }
    supplied = {
        "harness": (args.harness_wheel_sha256, args.harness_source_commit),
        "memory": (args.memory_wheel_sha256, args.memory_source_commit),
    }
    for key, wheel in paths.items():
        if not wheel.is_file() or wheel.suffix != ".whl":
            return {"status": "NOT_RUN/BLOCKED", "reason": f"candidate wheel missing: {key}"}
        actual = _sha256_bytes(wheel.read_bytes())
        pin = fixture["candidate_identity"][key]
        if actual != supplied[key][0] or actual != pin["wheel_sha256"]:
            return {"status": "FAIL", "reason": f"candidate wheel hash mismatch: {key}"}
        if supplied[key][1] != pin["source_commit"]:
            return {"status": "FAIL", "reason": f"candidate source commit mismatch: {key}"}
    artifact_dir = Path(args.artifact_dir).resolve()
    if artifact_dir.exists():
        return {"status": "FAIL", "reason": "artifact run directory must not pre-exist"}
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir()
    with tempfile.TemporaryDirectory(prefix="hm-sealed-audit-consumer-") as tmp:
        temp_root = Path(tmp)
        venv = temp_root / "venv"
        uv = shutil.which("uv")
        if uv is None:
            return {"status": "NOT_RUN/BLOCKED", "reason": "isolated installer unavailable: uv"}
        create = subprocess.run(
            [uv, "venv", "--python", sys.executable, str(venv)],
            cwd=temp_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if create.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "isolated venv creation failed"}
        python = _python_in_venv(venv)
        install = subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                str(paths["harness"]),
                str(paths["memory"]),
            ],
            cwd=temp_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "candidate wheel install failed"}
        child_config = {
            "candidate_identity": fixture["candidate_identity"],
            "principal": fixture["principal"],
            "evidence": {
                key: value
                for key, value in fixture["evidence"].items()
                if key not in {"raw_source_payload", "forbidden_canaries"}
            },
            "audit": fixture["audit"],
        }
        env = os.environ.copy()
        env["PYTHONPATH"] = ""
        env["PYTHONNOUSERSITE"] = "1"
        run = subprocess.run(
            [str(python), "-c", PUBLIC_CONSUMER, json.dumps(child_config), str(artifact_dir / "canonical.sqlite")],
            cwd=temp_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            return {
                "status": "FAIL",
                "reason": "public sealed consumer executed but failed",
                "candidate_exit_code": run.returncode,
                "stderr_tail": run.stderr[-1000:],
            }
        for canary in fixture["evidence"]["forbidden_canaries"]:
            if canary in run.stdout or canary in run.stderr:
                return {"status": "FAIL", "reason": "forbidden canary leaked from child consumer"}
        try:
            result = json.loads(run.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"status": "FAIL", "reason": "public sealed consumer emitted invalid JSON"}
        errors = _validate_result(result, fixture, venv)
        if errors:
            return {"status": "FAIL", "reason": "sealed audit oracle mismatch", "errors": errors}
        result_bytes = _canonical_bytes(result)
        for canary in fixture["evidence"]["forbidden_canaries"]:
            if canary.encode("utf-8") in result_bytes:
                return {"status": "FAIL", "reason": "forbidden canary persisted in result artifact"}
        result_path = artifact_dir / "sealed-audit-result.json"
        result_path.write_bytes(result_bytes)
        return {
            "status": "PASS",
            "fixture_revision": fixture["fixture_revision"],
            "fixture_sha256": _sha256_bytes(fixture_path.read_bytes()),
            "result_sha256": _sha256_bytes(result_bytes),
            "manifest_total_rows": result["reopened_manifest"]["manifest"]["total_row_count"],
            "manifest_table_roots": len(result["reopened_manifest"]["manifest"]["table_roots"]),
            "sealed_trace_items": result["sealed_trace"]["item_count"],
            "quality_gate": fixture["quality_gate"],
        }


def main() -> int:
    default_fixture = Path(__file__).resolve().parents[1] / "fixtures" / "sealed-audit-v1.json"
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=default_fixture)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--harness-wheel")
    parser.add_argument("--harness-wheel-sha256")
    parser.add_argument("--harness-source-commit")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--memory-wheel-sha256")
    parser.add_argument("--memory-source-commit")
    parser.add_argument("--artifact-dir", default=".local-test-evidence/sealed-audit-task7")
    args = parser.parse_args()
    checked = self_check(args.fixture.resolve())
    if checked["status"] != "PASS" or args.self_check:
        print(json.dumps(checked, ensure_ascii=False, sort_keys=True))
        return 0 if checked["status"] == "PASS" else 1
    required = (
        args.harness_wheel,
        args.harness_wheel_sha256,
        args.harness_source_commit,
        args.memory_wheel,
        args.memory_wheel_sha256,
        args.memory_source_commit,
    )
    if not all(required):
        result = {"status": "NOT_RUN/BLOCKED", "reason": "exact candidate identity required"}
    else:
        result = execute(args, args.fixture.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else BLOCKED_EXIT if result["status"] == "NOT_RUN/BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
