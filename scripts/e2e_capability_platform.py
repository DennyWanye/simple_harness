#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Real local integration smoke for DeskPet's capability platform.

This check uses the production registry, store, pack manager, immutable Godot
pack, JSON worker process, and catalog projection.  It intentionally does not
fake a Godot installation: a machine without Godot must still install the
detector safely and return ``godot_executable_not_found`` as structured data.

Run from the repository root:

    backend/.venv/Scripts/python.exe scripts/e2e_capability_platform.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
GODOT_PACK_ROOT = REPOSITORY_ROOT / "capability-packs" / "godot"
sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.capabilities.contracts import CapabilityScope  # noqa: E402
from deskpet.capabilities.platform import CapabilityPlatform  # noqa: E402
from deskpet.capabilities.store import (  # noqa: E402
    CapabilityStore,
    initialize_capability_database,
)
from deskpet.tools.capabilities import ToolExecutionContext  # noqa: E402
from deskpet.tools.registry import ToolRegistry  # noqa: E402


CONTROL_TOOLS = frozenset(
    {
        "capability_list",
        "capability_search",
        "capability_catalog_refresh",
        "capability_install",
        "capability_update",
        "capability_uninstall",
        "capability_rollback",
    }
)
GODOT_TOOLS = frozenset({"godot__detect", "godot__project_check"})


def _context(workspace: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="capability-smoke-scope",
        session_id="capability-smoke-session",
        request_id="capability-smoke-request",
        root_run_id="capability-smoke-root",
        turn_id="capability-smoke-turn",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash="capability-smoke-capability",
        scope_hash="capability-smoke-scope-hash",
        run_id="capability-smoke-run",
        call_id="capability-smoke-call",
        effect_id="capability-smoke-effect",
        trace_id="capability-smoke-trace",
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _run_smoke() -> dict[str, Any]:
    temporary_root = Path(tempfile.mkdtemp(prefix="deskpet-cap-")).resolve()
    platform: CapabilityPlatform | None = None
    cleanup_reports: tuple[Any, ...] = ()
    try:
        database = await initialize_capability_database(
            temporary_root / "workflow.db"
        )
        store = CapabilityStore(database)
        registry = ToolRegistry()
        platform = CapabilityPlatform(
            registry=registry,
            store=store,
            user_data_root=temporary_root / "user-data",
            first_party_pack_roots=(GODOT_PACK_ROOT,),
            python_executable=sys.executable,
        )

        first = await platform.initialize()
        first_revision = registry.catalog_snapshot().revision
        second = await platform.initialize()
        _require(first is second, "initialize must be process-idempotent")
        _require(
            registry.catalog_snapshot().revision == first_revision,
            "repeated initialize unexpectedly changed registry revision",
        )
        _require(
            len(first.first_party_installs) == 1,
            "the immutable first-party Godot pack was not installed exactly once",
        )
        _require(
            first.control_tools_registered,
            "the model-facing capability control surface was not registered",
        )

        registry_names = {
            spec.name for spec in registry.catalog_snapshot().specs
        }
        _require(
            CONTROL_TOOLS <= registry_names,
            f"missing control tools: {sorted(CONTROL_TOOLS - registry_names)}",
        )
        _require(
            GODOT_TOOLS <= registry_names,
            f"missing Godot tools: {sorted(GODOT_TOOLS - registry_names)}",
        )

        binding = await store.get_binding("builtin", "builtin", "godot")
        _require(
            binding is not None and binding.active,
            "Godot builtin binding is not active",
        )
        projected = (await platform.hub.snapshot(CapabilityScope())).get("godot")
        _require(
            projected is not None and projected.executable,
            "Godot pack is not executable in the stamped catalog",
        )

        install = first.first_party_installs[0]
        evidence = await store.get_phase_evidence(
            install.operation.operation_id,
            "environment_ready",
        )
        _require(evidence is not None, "environment evidence was not persisted")
        healthchecks = list(evidence.get("healthchecks") or ())
        _require(
            bool(healthchecks) and healthchecks[0].get("status") == "success",
            "the real Godot JSON worker healthcheck did not succeed",
        )

        context = _context(temporary_root)
        prepared = registry.prepare_call(
            "godot__detect",
            {},
            context.session_id,
            context.call_id,
            execution_context=context,
        )
        _require(
            prepared.tool_spec_fingerprint,
            "the canonical prepared call is missing its ToolSpec fingerprint",
        )
        detect_spec = next(
            spec
            for spec in registry.catalog_snapshot().specs
            if spec.name == "godot__detect"
        )
        _require(
            detect_spec.context_handler is not None,
            "Godot detector has no trusted context handler",
        )
        outcome = await detect_spec.context_handler({}, context)
        payload = outcome.to_dict()
        _require(
            payload.get("state") == "success",
            f"Godot detector worker failed: {payload}",
        )
        detection = payload["value"]["value"]
        _require(
            isinstance(detection, dict),
            "Godot detector returned a non-object payload",
        )
        if detection.get("found"):
            _require(
                detection.get("compatible") is True,
                "an installed Godot executable is below the supported major version",
            )
            environment_state = "godot_ready"
        else:
            _require(
                detection.get("reason") == "godot_executable_not_found",
                f"missing Godot was not reported structurally: {detection}",
            )
            environment_state = "godot_not_installed"

        return {
            "status": "PASS",
            "pack": {
                "id": binding.capability_id,
                "version": binding.version,
                "active": binding.active,
                "manifest_hash": binding.manifest_hash,
            },
            "registry": {
                "revision": first_revision,
                "control_tools": sorted(CONTROL_TOOLS),
                "godot_tools": sorted(GODOT_TOOLS),
            },
            "worker": {
                "healthcheck": healthchecks[0],
                "detect": detection,
                "environment_state": environment_state,
            },
            "idempotent_initialize": True,
        }
    finally:
        if platform is not None:
            cleanup_reports = await platform.shutdown()
        shutil.rmtree(temporary_root, ignore_errors=False)
        if cleanup_reports:
            raise AssertionError(
                "capability smoke leaked owned worker processes: "
                f"{cleanup_reports!r}"
            )


def main() -> int:
    try:
        result = asyncio.run(_run_smoke())
    except Exception as exc:  # noqa: BLE001 - executable acceptance boundary
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
