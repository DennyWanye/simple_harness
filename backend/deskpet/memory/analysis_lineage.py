# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 4 — analysis lineage of one foreground Run binding (design-freeze §5/§6/§8.5).

``model_config_hash = sha256(canonical{provider_id, provider_incarnation_id,
provider_config_revision, model_id, model_params, endpoint_identity})`` computed
from the durable ``SdkRunBindingV1`` record.  The same function feeds the
terminal-commit ``memory_ingestion_outbox`` row, the ``AnalysisLineage`` handed to
``ingest_committed_evidence`` and the attempt ledger, so the Memory-derived
``MemoryAnalysisRequest`` lineage can be checked against the binding bit-for-bit.

Pure module (no store imports) so both ``execution.foreground_queue`` and
``sdk_adapters.post_turn_invoker`` can use it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from deskpet.task_scope.protocol import canonical_hash

LINEAGE_SCHEMA_VERSION = 1


def binding_model_config_hash(record: Mapping[str, Any], *, endpoint_identity: str | None) -> str:
    """Immutable provider/model/config identity of one Run binding."""

    return canonical_hash(
        {
            "provider_id": str(record.get("provider_id") or ""),
            "provider_incarnation_id": str(record.get("provider_incarnation_id") or ""),
            "provider_config_revision": int(record.get("provider_config_revision") or 0),
            "model_id": str(record.get("model_id") or ""),
            "model_params": json.loads(json.dumps(record.get("model_params") or {}, sort_keys=True, default=str)),
            "endpoint_identity": endpoint_identity,
        }
    )


def analysis_lineage_payload(record: Mapping[str, Any], *, endpoint_identity: str | None) -> dict[str, Any]:
    """Durable outbox ``analysis_lineage_json``: the ``AnalysisLineage`` triple + the binding it came from.

    ``AnalysisLineage.from_json`` reads only ``schema_version``/``provider_id``/
    ``model_id``/``model_config_hash``; the extra ``run_binding`` /
    ``endpoint_identity`` keys let the analysis executor rebuild the
    ``ProductProviderAdapter`` from Host-durable facts alone.
    """

    binding = json.loads(json.dumps(dict(record), sort_keys=True, default=str))
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "provider_id": str(record.get("provider_id") or ""),
        "model_id": str(record.get("model_id") or ""),
        "model_config_hash": binding_model_config_hash(record, endpoint_identity=endpoint_identity),
        "endpoint_identity": endpoint_identity,
        "run_binding": binding,
    }


__all__ = ["LINEAGE_SCHEMA_VERSION", "analysis_lineage_payload", "binding_model_config_hash"]
