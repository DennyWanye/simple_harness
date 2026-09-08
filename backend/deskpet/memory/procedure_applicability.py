"""Actual frozen tool/environment snapshots, never model-declared versions."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from simple_harness.runtime import ProcedureApplicabilityContext, ProcedureHazard
from deskpet.task_scope.protocol import canonical_hash


class ProcedureUseRejected(ValueError):
    pass


@dataclass(frozen=True)
class ProcedureToolSnapshot:
    name: str
    version: str
    schema_hash: str
    execution_identity: str
    effect_class: str
    dangerous: bool
    requires_confirmation: bool

    def to_json(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def current_snapshot(authority, registry, *, tool_names: Sequence[str], route):
    """Use the same Run authority and registry as physical ProductEffectExecutor.

    The caller retains the active workspace execution fence while using this
    snapshot. A mapping supplied by a model is not accepted as an authority.
    """
    from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityV1

    if type(authority) is not SdkRunToolAuthorityV1:
        raise ProcedureUseRejected("procedure_actual_run_authority_required")
    names = tuple(tool_names)
    if not names or len(names) > 16 or any(type(name) is not str for name in names):
        raise ProcedureUseRejected("procedure_step_tools_invalid")
    authority.assert_workspace_current()
    snapshots = []
    for name in names:
        # Order matters for the model. ``_validate_execution_identity`` folds a
        # name this Run never froze (KeyError) into the same catalog error as a
        # genuine mid-Run identity/schema change, and run-01j C06-14 then told
        # the model "identity changed" six times for a tool it had simply never
        # activated. Decide "absent" first so the two get distinct codes.
        spec = authority.specs.get(name)
        if spec is None:
            raise ProcedureUseRejected("procedure_tool_unavailable")
        try:
            registry._validate_execution_identity(name, str(authority.run_id))
        except (RuntimeError, KeyError) as error:
            raise ProcedureUseRejected("procedure_current_tool_identity_changed") from error
        snapshots.append(ProcedureToolSnapshot(
            name, spec.spec_version, spec.schema_hash, spec.execution_identity,
            # Capability.dangerous also defaults true for all staged/control
            # dispatch. It is an execution-confirmation condition, not proof
            # of publish/delete/payment hazard. Preserve it in the fingerprint
            # and leave physical authorization unchanged.
            str(spec.effect_class), bool(spec.manifest_dangerous), bool(spec.dangerous),
        ))
    from deskpet.memory.procedure_route import ProcedureRouteSnapshot
    if type(route) is not ProcedureRouteSnapshot:
        raise ProcedureUseRejected("procedure_actual_route_snapshot_required")
    manifest = [snapshot.to_json() for snapshot in snapshots]
    applicability = ProcedureApplicabilityContext(
        "host-procedure-tool-sequence", route.environment,
        canonical_hash([snapshot.version for snapshot in snapshots]), canonical_hash(manifest),
    )
    return applicability, tuple(snapshots)


def inferred_hazard(snapshots):
    """Only proven read-only/reversible tool classes can qualify automatically."""
    effects = {snapshot.effect_class for snapshot in snapshots}
    if effects <= {"read_only", "draft_only", "reversible_local"} and not any(
        snapshot.dangerous for snapshot in snapshots
    ):
        return ProcedureHazard.NONE
    for effect, hazard in (("payment", ProcedureHazard.PAYMENT),
                           ("destructive", ProcedureHazard.DELETE),
                           ("external_send", ProcedureHazard.PUBLISH)):
        if effect in effects:
            return hazard
    # The public hazard vocabulary has no truthful "unknown" value. Do not
    # invent one or disguise an unclassified/dangerous effect as reversible.
    raise ProcedureUseRejected("procedure_hazard_classification_unavailable")
