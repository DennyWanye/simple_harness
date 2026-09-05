# CREATE candidate slot correction

2026-09-05. Oracle before implementation. Actual native Run b58b21d3-74b8-55d9-b2cd-301d4c8fa9cb
reached frontend completion and said the preference was remembered, but the actual
analysis CREATE supplied an invented nonempty candidate_key. Host correctly rejected
it; result was analysis_all_operations_rejected/no_mutation, with no new visible
memory. Preserve that real failed Run and its response; never rewrite its receipt.

V2 gives candidate_key a nonempty string schema but no explicit CREATE representation.
V3 defines absent/empty string as no selected candidate for CREATE; a nonempty key
still rejects CREATE. REVISE still needs the exact actual nonempty issued key and
independent explicit USER intent. This uses the existing simple string schema
capability, without nullable/union assumptions or weakening existing target checks.
Tool field description and prompt must state this distinction. Version the prompt,
proposal schema, policy and validator; keep old committed responses/receipts readable.

Decisive expected outcomes: emitted proposal schema accepts an empty CREATE slot;
real public production-factory CREATE produces a recallable memory with omitted or
empty slot; invented nonempty slot produces zero memory; empty or invented REVISE
does not authorize a target; explicit correction cannot fall back to CREATE even
with empty slot. Reopen/replay and original evidence guards stay unchanged.

This fixes the payload representability defect. Frontend claimed success before
memory analysis is a separate existing Stage3 status/settlement requirement; until
it is implemented, an assistant assertion alone is never memory-success evidence.
The initial native remember attempt failed. A new real USER action after the v3
fix subsequently passed CREATE and REVISE; duplicate-source forget then failed.
See [current native result](NATIVE-DUPLICATE-FORGET.md); old effects were not replayed.

## Source verification

Independent source review accepted the bounded change. Existing semantic20 plus
new real-store CREATE-slot3 passed23/21.72s. Version/span and durable zero-second-
Provider replay controls passed2/0.80s. Reviewer-requested empty-key REVISE and
empty-key correction-as-CREATE were added; those2 plus strengthened CREATE3 passed
5/3.71s. Invented CREATE now asserts the actual persisted Host rejection reason,
not merely absence of a recall result. No duplicate counting across these runs.
Native CREATE/REVISE retest subsequently passed, with the separate duplicate-source
forget failure documented above. Production permissions and old action receipts are
unchanged; no live Provider calls were made for these automated checks.

Local evidence prefix `.local-test-evidence/2026-09-05/primary-candidate/`:

- semantic-create-slot-v3.log: b4af5bf9d17f6ac9381a6d0da9146bdb0459e4d386dae0a3a998f23d17f50d36
- semantic-v3-version-replay.log: 7f4eb96ec4313d05bb98d5417785a87674868d55789d0bb9a0a3408e5d3438cf
- semantic-v3-negative-oracles.log: da3c98650df91ee2b050948b04e8d40affc89ce8e5bad0f07e3fecd6403787d7

Raw native evidence in main Host `primary-ui-vncqr94i` binds backend96e44393,
binary cf2584a6eb30c2c2f4948e2db32e7a83b8358462687e5d61d20f74c82695bbd9,
actual Host Run above and SDK Run
product-sdk-be6f142cac8258f1765d97fc07899e2a9babefce8b4f2a43ff96d80f89f36061.
Analysis attempt c6886e44-1227-592c-8a56-4b20ed9b7fa1 returned the rejected CREATE.
Its recorded usage1506 input/594 output and12,748ms is analysis-only; the legacy
cost_microunits=0 field is not verified pricing/free-call evidence.
