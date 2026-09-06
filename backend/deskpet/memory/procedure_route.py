"""Resolve actual primary routing; initial projectless Run facts stay immutable."""
from dataclasses import dataclass

from deskpet.memory.procedure_applicability import ProcedureUseRejected
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore, WorkspaceBindingError
from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskScopeNotFound
from deskpet.task_scope.protocol import canonical_hash


@dataclass(frozen=True, slots=True)
class ProcedureRouteSnapshot:
    task_scope_id: str
    route_receipt_id: str
    route_receipt_hash: str
    binding_receipt_id: str
    binding_receipt_hash: str
    environment: str


async def resolve_procedure_route(path, db, *, run_id, subject, envelope=None):
    ledger = ContextRouteLedgerStore(path)
    if envelope is not None:
        if envelope.run_id.value != run_id or not envelope.route_receipt_id:
            raise ProcedureUseRejected("procedure_actual_route_required")
        receipt_id = envelope.route_receipt_id
    else:
        async with db.execute(
            "SELECT receipt_id FROM context_route_decisions WHERE sdk_run_id=? "
            "ORDER BY provider_turn_ordinal DESC, recorded_at DESC, decision_id DESC LIMIT 1",
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ProcedureUseRejected("procedure_actual_route_required")
        receipt_id = row[0]
    receipt = await ledger.read_route_receipt(run_id, receipt_id, db=db)
    if receipt is None or not receipt.task_scope_id or (
        envelope is not None and envelope.route_receipt_hash != receipt.receipt_hash
    ):
        raise ProcedureUseRejected("procedure_actual_scope_route_required")
    async with db.execute("SELECT subject FROM task_scopes WHERE task_scope_id=?",
                          (receipt.task_scope_id,)) as cursor:
        owner = await cursor.fetchone()
    if owner is None or owner[0] != subject:
        raise ProcedureUseRejected("procedure_foreign_task_scope")
    from deskpet.sdk_adapters.effect_gate import PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES
    if await CanonicalTaskScopeStore(path).read_head_status(receipt.task_scope_id, db=db) not in PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES:
        raise ProcedureUseRejected("procedure_scope_not_active")
    bindings = WorkspaceBindingAuthorityStore(path)
    try:
        exact = await bindings.verify_route_binding(receipt, db=db)
        head = await bindings.current_receipt(receipt.task_scope_id, db=db)
        if exact != head or len(exact.root_identity_hashes) != 1:
            raise ProcedureUseRejected("procedure_current_single_root_required")
        verified = await bindings.verify_effect_authority(
            task_scope_id=receipt.task_scope_id, binding_set_revision=exact.binding_set_revision,
            binding_set_receipt_id=exact.receipt_id, binding_set_receipt_hash=exact.receipt_hash,
            root_identity_hash=exact.root_identity_hashes[0], db=db)
    except (WorkspaceBindingError, TaskScopeNotFound) as error:
        raise ProcedureUseRejected("procedure_current_workspace_unavailable") from error
    # Scope-local root IDs and binding counters are not execution environment.
    # Actual canonical path and filesystem identity must still match every use.
    environment = canonical_hash({"domain": "host-procedure-environment/v1",
        "canonical_path": verified.root.canonical_path,
        "filesystem_identity": verified.root.filesystem_identity.to_json()})
    return ProcedureRouteSnapshot(receipt.task_scope_id, receipt.receipt_id, receipt.receipt_hash,
                                  exact.receipt_id, exact.receipt_hash, environment)
