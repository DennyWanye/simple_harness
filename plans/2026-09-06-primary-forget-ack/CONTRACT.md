# Primary forget ACK across content invalidation

2026-09-06. plan-status: finalized under the user's scoped native P1 repair.
Base cf4d8e0a, isolated feat/primary-forget-ack. Native process/data remain main-owned.

Cause to prove: primary content invalidation clears state; parent conditional mount
removes MemoryPanel and its write correlation. Same-owner late valid ACK is lost,
then retry enters the same cycle. Leaf-only request tests did not exercise parent.

Oracle before implementation: actual PrimaryChatView + controller + bound port +
CognitiveRequests + panel; protocol fixture supplies deferred transport ACKs, no
mock of these product components. Click forget, deliver actual invalidation event,
keep primary state/history reads unresolved, then deliver exact ACK. Old history
and detail must disappear synchronously, action only clears on matching ACK, graph
unblocks only after confirmation and a fresh read. Same-action retry after a real
unknown must survive another invalidation-before-ACK. True disconnect, rechallenge,
verified owner change and wrong ACK remain fail-closed; never infer success from
an absent node/list or erase pending to make the UI green.

Minimal candidate: retain only an opaque previously verified primary reference
within the current signed connection, independently of content state; clear it on
authority invalidation. Parent uses it for mounted memory controls, with current
verified readiness (not canSend/history readiness). Content and details still clear.
No backend/SDK/hash/idempotency changes, no auto mutation retry or native Provider.
Focused frontend/typecheck/build, independent correctness review, ARCH/handoff;
main owns native exact-build proof. HUMAN grant WIP stays paused and preserved.
