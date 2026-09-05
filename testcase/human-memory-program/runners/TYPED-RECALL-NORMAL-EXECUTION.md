# Normal consumer execution oracle — 2026-09-05

Frozen before this execution batch. Inputs are compiled from the original 401-cell fixture;
no expected outcome, score, source hash or SDK result is sent to the consumer. Each case gets
a fresh database and real public evidence admission/mutation/recall. Dynamic IDs bind only
to independently checked mutation receipts; they never replace business assertions.

Eligibility: original lifecycle/epistemic/disclosure/validity rules determine inclusion.
Returned candidates must match the input source and minimal projection, exact receipt revision,
evidence-set hash, classification, full-text RRF 0.30/(60+rank), and decision/result bindings.
Excluded sources must give zero filtered candidates, zero selected/group payload, and no canary.
Constructor or mutation rejection does not prove a recall eligibility gate: preserve the actual
operation and reason, report BLOCKED where the required state cannot be publicly established.
Procedure applicability and Prospective signal authority must be established before eligible
case admission; merely creating an active/pending DTO is insufficient.

Budget: retain literal byte/token limits (156/156 and 166/150), including one-unit-lower limits.
Approved empty Semantic qualifiers become [] (same two-byte length as {}). Independently recompute
the provider envelope bytes/codepoints/tokens before execution. Exact limit must return the whole
item; lower limit must return none, preserve filtered candidate count and durable replay.
No candidate output is a gold value. Episode numeric interval projection is checked against the
existing public DTO but the original projection-field mismatch remains explicit BLOCKED.

Vector: no embedder is supplied. Requested full_text must survive unavailable vector; vector-only
must return none. Assert ordered degradation codes, RRF and empty fallback count through exact
source membership. Without a public executed-lane witness retain that remaining gate as BLOCKED.

Source faults: no-fault control must independently satisfy two-source payload/hash/receipt/rank,
count/budget/binding assertions before it is a transaction reference. Pre-commit faults allow
start/attempt rows only; final tables equal pre-state. Commit-before-ACK requires committed
control bytes and exact replay with zero candidate reads. Never accept an old-or-new disjunction.

## Contract spelling corrections after first integration (before rerun)

The first run exposed three runner assertions which were not the public contract; raw failed
receipts remain unchanged. Correct these against source definitions, not sampled results:

- Harness `recall_protocol_v4.py` explicitly rejects nonzero `filtered_candidate_count` for
  NO_RECALL/REJECTED. The initial added requirement to preserve count=1 on an empty budget
  result was erroneous. Assert zero public count plus NO_RECALL/BUDGET_EXHAUSTED and whole-item
  skip, retaining all literal byte/token thresholds. Original Task5 §5.6 defines this outcome.
- Harness `memory_protocol.py` enum is `needs_user_confirmation` (not guessed
  `needs_confirmation`); keep exactly one atomic r7/r8 group and all member assertions.
- Memory's public `TypedRecallExecution.degradation_codes` uses
  `cognitive_vector_unavailable` for the cognitive lane; the original semantic diagnostic
  VECTOR_LANE_UNAVAILABLE maps to that code. No invented `vector_lane_unavailable` string.
  Executed-lane witness remains BLOCKED; no fallback/score/payload rule is relaxed.

These are runner spelling/contract errors, not established SDK defects. No frozen oracle,
attack, ID, domain, hash vector or threshold was modified for these corrections.

## Public parser/page execution inputs (frozen before executor)

For strict-v3, mutate only the real no-fault decision schema_version to 3 and call public
RecallDecisionV4.from_json. The three source discriminant attacks use a real selected-item
carrier, changing the original discriminant/revision and required short companion fields.
These are Harness parser evidence, not Memory version admission or candidate-read receipts.
Page attacks use the real result id/hash; only the wrong-hash cell uses the frozen bad hash.
Coordinate99 maps to item_offset98 (one-based frozen coordinate); successful page keeps the
original 128-byte bound. If the full binding cannot fit, BLOCKED, never raise that bound.
For expiry, create a real context expiring at the frozen use_at; advance only the trusted
builder clock to that boundary. Preserve public exceptions and bindings; parent admits only
fully mapped original requirements. Parser-only proof cannot silently substitute Memory proof.
