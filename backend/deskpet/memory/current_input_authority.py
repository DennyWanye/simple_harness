"""SDK current-input port over actual Host execution and immutable admission facts."""
from pathlib import Path
import aiosqlite

from deskpet.memory.current_input_source import read_current_input_source, CurrentInputSourceError
from deskpet.memory.current_input_visibility import (
    LIVE_CLAIM_STATES, LIVE_CLAIM_PLACEHOLDERS, same_physical_claim,
)
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.trusted_disclosure import resolve_current_disclosure


class HostCurrentInputAuthority:
    def __init__(self, path, *, principal=None):
        from deskpet.memory.human_memory_v7 import local_memory_principal
        self.path = Path(path)
        self.principal = local_memory_principal() if principal is None else principal

    async def resolve_current_input(self, *, principal, disclosure_context, binding):
        from simple_harness_memory import CurrentInputAuthorityV1, CurrentInputBindingV1
        from simple_harness.runtime import (
            AdmittedEvidenceAuthority, EvidenceItemAuthority, EvidenceActorRole,
            EvidenceProvenance, PrivacyClass, EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
            EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        )
        from deskpet.execution.foreground_runtime import _execution_session_id
        from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

        if (type(binding) is not CurrentInputBindingV1 or principal != self.principal
                or principal.actor_id != disclosure_context.subject):
            return None
        # A declaration on an arbitrary old turn is not current input authority.
        async with aiosqlite.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            rows = await (await db.execute(
                "SELECT h.*,t.turn_hash FROM foreground_run_heads h "
                "JOIN foreground_turns t ON t.turn_id=h.turn_id AND t.subject=h.subject "
                f"WHERE h.subject=? AND h.turn_id=? AND h.current_state IN "
                f"({LIVE_CLAIM_PLACEHOLDERS}) LIMIT 2",
                (principal.actor_id, binding.turn_id, *LIVE_CLAIM_STATES),
            )).fetchall()
            if len(rows) != 1:
                return None
            run = rows[0]
            # Same allocation function as the real foreground runtime. Before
            # SDK start there is a real durable Host claim, not a fabricated SDK Run.
            expected_run = SdkRuntimeIngress._compute_run_id(_execution_session_id(run["host_run_id"]),
                f"foreground-request-{binding.turn_id}", binding.turn_id).value
            if (disclosure_context.run_id != expected_run
                    or run["sdk_run_id"] not in (None, expected_run)):
                return None
        try:
            fact = await read_current_input_source(db_path=self.path, subject=principal.actor_id, turn_id=binding.turn_id)
        except CurrentInputSourceError:
            return None
        envelope, receipt = fact["envelope"], fact["receipt"]
        if (envelope != binding.evidence.envelope or receipt != binding.evidence.receipt
                or fact["turn_hash"] != run["turn_hash"]):
            return None
        origin = await HostHistorySourceAuthority(self.path).resolve_history_source(
            principal=principal, envelope=envelope, receipt=receipt)
        if origin is None or origin.proof_kind != "atomic":
            return None
        current = await resolve_current_disclosure(db_path=self.path, subject=principal.actor_id,
            run_id=expected_run, request_id=binding.request_id, turn_id=binding.turn_id)
        if current != disclosure_context:
            return None
        # Re-read the ORIGINAL claim after every slow external/source read. Do
        # not replace it with a new current Run or a newer owner generation.
        async with aiosqlite.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            final = await (await db.execute("SELECT * FROM foreground_run_heads WHERE host_run_id=?",
                (run["host_run_id"],))).fetchone()
        # This slow span sits inside the Host's own hand-off window, so the two
        # writes it straddles are this claim starting, not another holder: the
        # ``CLAIMED->RUNNING`` transition, and the ``bind_sdk_run`` this reader
        # already admitted as absent above. Everything else still fails closed.
        if not same_physical_claim(run, final, may_bind_sdk_run_id=expected_run):
            return None
        kind = fact["input_use"]["declaration"]["kind"]
        item = EvidenceItemAuthority(
            schema_version=EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
            authority_id="host:current-input:" + fact["fact_hash"], evidence_id=envelope.evidence_id,
            envelope_hash=envelope.envelope_hash, sanitized_hash=envelope.sanitized_hash,
            source_hash=envelope.source_hash, source_kind=envelope.source_kind,
            item_ordinal=1, item_id=envelope.sanitized_payload["delivery_key"], item_json_pointer="/text",
            normalization_version=EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
            actor_role=EvidenceActorRole.USER, provenance=EvidenceProvenance.AUTHENTICATED_USER,
            required_privacy_class=PrivacyClass.PERSONAL if kind == "current_user" else PrivacyClass.PUBLIC,
            required_information_attributes=(),
            classification_authority_ref="host:current-input-classification/v1",
            issuer_ref="host:current-input-authority/v1")
        return CurrentInputAuthorityV1(binding.binding_hash, current,
            AdmittedEvidenceAuthority(envelope, receipt, item), origin, kind,
            fact["fact_hash"], fact["turn_hash"], self.principal)
