# P1 correction pending revalidation

2026-09-06: c11 first-IDLE success was challenged by Dirac: unclaimable jobs
(backoff/active lease/dead_letter) also yield IDLE. Current source rejects first
IDLE with graph_fixture_settlement_unconfirmed; only actual APPLIED confirms this
invocation. Historical r4/r5 positives below are not P1 closure. New negative
first produced actual dead_letter because fixture injected generic RuntimeError;
it now uses public transient delivery error for the intended backoff path.
Corrected negative revalidation BUSY75/no child. Main's separate bootstrap had
actual APPLIED and is not based on first-IDLE success. Do not use this helper to
certify an already-settled job on reopen without separate SDK proof.

# Graph synthetic seed analysis drain

2026-09-06. Fixture bootstrap only; not a quality-model/native result.

## Public seam

Create `GraphFixtureDeliveryAuthority()` BEFORE constructing the bootstrap
MemoryManager. Pass that exact object as public `build_human_memory_v7(...,
analysis_delivery_authority=authority)` (Host runtime wrapper calls it
`analysis_authority`). Do not hot-swap an already composed production manager.
Use the real local principal, ordinary production classification policy and
HostEvidenceAuthority over the fresh userdata state DB. No model/worker starts.

After `apply_graph_seed` returns `(plan, applied, view, graph)`, call:

```python
await drain_graph_seed_analysis(
    delivery_authority=authority, path=state_path, manager=manager,
    principal=principal, seed=seed, envelope=envelope, receipt=receipt,
    plan=plan, applied=applied, clock=clock,
)
```

Import both names from `deskpet.quality.corpus_graph_drain`.
The executor checks actual admitted source and SDK public strict-atomic receipt,
all three operations and resolved relation payload hash before returning an
explicit fixture `no_mutation`. Request/result delivery is persisted in existing
Host S1 and read back by the constructor-bound authority. The SDK public durable
runner performs claim/admission/application/finalization. No SQL state changes,
no job deletion, no fake Harness terminal, no production worker disabling.
Provider/model metadata is `corpus-fixture-plan` / `no-language-model`, zero
language-model tokens/cost. This is deterministic fixture acknowledgement of
already committed data, not an LLM deciding that the source carries no memory.

Close bootstrap manager, then start native with normal production authority.
Use fresh isolated Memory DB: helper intentionally does not drain arbitrary
jobs, and an interrupted/failed drain is not completion. A fresh bootstrap on reopen can report unconfirmed if no job is claimable;
do not infer settlement from this. Reusing the same source does not authorize
creating a new analysis job or retrying a dead-letter job.
The raw source and fixture delivery remain present. Do not enqueue the setup as
an actual foreground turn when preparing native data; publicappendS1 is enough.

## New verification only

Installed H078/M618, no SDK overlay or models. One unique new control:
actual atomic graph seed, rehashed foreign-plan rejection before claim,
public no-mutation drain, next claim IDLE, close/reopen IDLE with zero fixture
executions, graph nodes/edge unchanged. r5: 1 passed / 0.79s; PG14162 exit0,
remaining[], 162528KiB peak, default resource lock released.

r1: adapter used plan.ordered_evidence_refs instead of evidence_refs.
r2: relation hash needed resolved actual endpoints, not CreatedByOperationTarget.
r3: missing constructor-bound delivery authority was correctly rejected by SDK.
r4: first positive / reopen passed. r5 adds foreign-plan rejection and checks
extracted minimal delivery module; not a repeated old graph matrix.

No full native restart/production worker invocation asserted by this control;
actual SDK reopen+claim IDLE establishes this seed job has no pending work.
Source/history isolation and generic runtime setup job adapter remain separate WIP.

Command: main `scripts/run_resource_bounded.py --rss-mib 2048 --seconds 180 --`
existing `primary-m0615/venv/bin/python -I -B` own ignored `run_batch.py`
`<batch-dir> tests/quality/test_corpus_graph_drain.py`.

## Raw index

- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r1/command.log` SHA256 `a9890f5df5cd20031578a0d18f0980db7d1ebecc09f787e726a91c8439a2fd66`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r1/resource.json` SHA256 `a8e531e3c1a65c79d8d4a4985686f626ac0124a59ad0a096028807377697a8b1`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r2/command.log` SHA256 `cd97a00f61182de7a28b7bff64549719ad735a9ce46a34167b636f98f95deca8`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r2/resource.json` SHA256 `7600c7d765ebef9cdc0f8becf7b9ac5229df71cfead4c11072ee2c1d5f00e833`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r3/command.log` SHA256 `0517620e8a4d8dc98ead49021a09f480c7b8dded86959a140991526da77cb2af`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r3/resource.json` SHA256 `e3fbc8ea8516b64d4c053dff14d6fc88f5e7a1174e9f90ed9fe9a5d50452dc28`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r4/command.log` SHA256 `fedceb118a93f8e9bed4f5b262246c28f6bfdace0d13713f5045fa2fb6554486`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r4/resource.json` SHA256 `60b6309a36639676f8262b91078ee410e8180c1c5fa88e039777c02dfbd24bc2`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r5/command.log` SHA256 `ac0988b070cfb8585ee6cce3a7e8654f43da14bf7e316bc0b9277c6ad43db855`
- `.local-test-evidence/2026-09-06/corpus-public-seed/graph-drain-r5/resource.json` SHA256 `3971038d9ee91da61f2d42c61aa857c628d4f8302760e00e00a1d5b4eedadf34`
