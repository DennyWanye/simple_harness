"""Benchmark-only exact user decisions; no model input or policy mutation."""
from simple_harness import thaw_json

from deskpet.quality.corpus_trace import digest


class CorpusApprovalBlocked(RuntimeError):
    pass


class ReadOnlyMemoryApproval:
    def __init__(self, *, ingress, persist):
        self.ingress, self.persist = ingress, persist
        self.attempted = set()
        self.ordinal = 0

    def record(self, value):
        self.ordinal += 1
        self.persist(f"approval-{self.ordinal:03d}", value)

    async def allow_request(self, *, request, sdk_run_id, decision_id, queued, service):
        """Default remains the original memory-only benchmark permission."""
        args = request.get("arguments")
        return (request.get("tool_name") == "context_route" and isinstance(args, dict)
            and args.get("route") == "memory_standalone")

    async def __call__(self, *, service, queued):
        state = await service.read_primary_state(request_id="corpus-approval-state")
        current = state.get("current_run")
        if current is None:
            return False
        sdk = current["sdk_run_ref"]
        run_state = self.ingress.query(sdk).state.value
        if run_state in {"completed", "failed", "cancelled", "stopped"}:
            return False
        target = dict(primary_ref=state["primary_ref"],
            expected_run_ref=current["run_ref"], expected_generation=current["generation"])
        if run_state != "waiting":
            self.record(dict(status="BLOCKED", reason="provider_or_run_not_idle", sdk_run_ref=sdk))
            raise CorpusApprovalBlocked("corpus_approval_not_permission_waiting")
        listed = await service.list_primary_decisions(request_id="corpus-approval-list", **target)
        pending = listed["pending"]
        exact_targets = {item.decision_id: item for item in self.ingress.list_open_authorizations(
            run_id=sdk, session_id=current["execution_session_ref"])}
        if not pending:
            self.record(dict(status="BLOCKED", reason="no_exact_tool_decision", sdk_run_ref=sdk))
            raise CorpusApprovalBlocked("corpus_approval_unknown_wait")
        verified = []
        for item in pending:
            decision_id = item["decision_id"]
            projected = exact_targets.get(decision_id)
            if (projected is None or projected.turn_id != queued["turn_ref"]
                or projected.run_id != current["run_ref"] or projected.sdk_run_id != sdk):
                raise CorpusApprovalBlocked("corpus_approval_turn_mismatch")
            record = self.ingress.read_authorization_decision(run_id=sdk, decision_id=decision_id)
            request = thaw_json(record.request) if record is not None else None
            if (record is None or str(record.run_id) != sdk or record.kind != "tool_authorization"
                or record.state.value != "open" or record.version != item["version"]
                or request.get("nonce") != item["nonce"]):
                raise CorpusApprovalBlocked("corpus_approval_exact_identity_mismatch")
            # Never authorize from the display-only truncated arguments_preview.
            allowed = await self.allow_request(request=request, sdk_run_id=sdk,
                decision_id=decision_id, queued=queued, service=service)
            fact = dict(**target, sdk_run_ref=sdk, decision_id=decision_id,
                version=record.version, request_hash=digest(request),
                tool_name=request.get("tool_name"))
            if not allowed or decision_id in self.attempted or len(self.attempted) >= 50:
                self.record(dict(**fact, status="BLOCKED", reason="not_whitelisted_or_already_attempted"))
                raise CorpusApprovalBlocked("corpus_approval_not_authorized")
            verified.append((item, fact))
        # Do not partially approve a batch containing unrelated decisions.
        for item, fact in verified:
            self.attempted.add(item["decision_id"])
            self.record(dict(**fact, status="RESPONSE_ATTEMPTED"))
            try:
                response = await service.respond_primary_decision(
                    request_id="corpus-approval-respond", **target,
                    decision_id=item["decision_id"], nonce=item["nonce"],
                    version=item["version"], decision="allow")
                if response["outcome"] != "allowed" or response["duplicate"]:
                    raise CorpusApprovalBlocked("corpus_approval_unexpected_ack")
            except BaseException:
                self.record(dict(**fact, status="RESPONSE_UNCONFIRMED"))
                raise
            self.record(dict(**fact, status="ALLOWED", response=response))
        return True
