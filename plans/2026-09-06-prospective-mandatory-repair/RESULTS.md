# Bounded mandatory-context repair: source results

2026-09-06. Host product `943a0d51`, contract `fe2560c8` (base `eaa210b3`).
Harness product `25221a4`, final test source `28160d6` (base H078 `bb9abfd`).
H079 version/artifact/installed/native are main-owned follow-ups; M618 unchanged.

**11 new SDK controls and 3 new Host controls passed in separate bounded batches.**
No old suites/models/native/builds were run. This is source-overlay evidence using
the existing H078/M618/S0313 target and generic Python dependencies, not an
independently installed H079 identity.

## What passed

- Actual Host ordinary `28+15` -> first actual HTTP response `43`, zero tools ->
  durable rejection/feedback -> new request in the same Run -> actual public
  `prospective_ack` -> SDK completed and Host acknowledged/settled. Main's real
  sink proxy, typed terminal authority, current snapshot and physical pending
  source guard are in this chain. Public audit pages expose the original known
  `context.no_recall` rejection and completed `context.apply` with exact feedback
  identity. This checks the used boundaries, not full Host all-operation coverage.
- The same initial zero-tool response followed by a real `context_route` but no
  ACK still fails after two repairs; no acknowledged/settled record is created.
- Public Memory forget after the second request's actual use grant and before its
  physical pending-source guard prevents the second send; no fake ACK appears.
- SDK actual SQLite invocation/checkpoint/effect controls preserve successful
  response state across four injected interruption boundaries: physical success
  before response checkpoint, response checkpoint, repair checkpoint, and feedback
  append. Close/open the DB and resume under the still-valid original lease sends
  each request once; one ACK effect succeeds and schema8/repair count persists.
  This is durable boundary/reopen evidence, not a killed process or new-owner
  production-stack recovery test.
- Repeated direct answers exhaust exactly two repairs; foreign identity and an
  unrelated error do not retry. Three protected controls first produce a real
  repair, then corrupt the next checkpoint's missing receipt / subject / intents
  with recomputed storage hashes. Cold DB reopen rejects each at ReAct attestation
  before the second physical send. These source fault controls use explicit empty
  typed intents; real Memory/pending-source evidence is in the Host controls.

## Batches and preserved failures

All raw directories are under this tree's ignored
`.local-test-evidence/2026-09-06/mandatory-context-repair/`.

| Batch | Actual result | PG / exit | Explanation |
|---|---|---|---|
| sdk-r1 | 2 PASS, 6 FAIL / .52s | 20454 / 1 | Fixture omitted real usage/price; existing UNKNOWN-cost stop correctly prevented continuation. |
| sdk-r2 | 1 PASS, 5 FAIL / .49s | 20634 / 1 | Exhaustion passed; ACK fixture lacked required handoff binding. |
| sdk-r3 | 5 PASS / .35s | 20981 / 0 | Only previous five failures; real SDK handoff receipt now bound. |
| host-r1 | 3 PASS / 6.63s | 20676 / 0 | Actual Host chain, route-without-ACK, late forget; audit identity checked in positive. |
| sdk-r4 | 3 FAIL / .57s | 21293 / 1 | New protected fixture admitted Run before configuring its authority scope. |
| sdk-r5 | 3 FAIL / .57s | 21334 / 1 | Correct SQLite admission refusal occurred before the test's expected ReAct refusal. No bypass was observed. |
| sdk-r6 | 3 PASS / .27s | 21416 / 0 | Only three previous failures; deliberate interruption/reopen exercises the ReAct layer directly. |

Every group exited with `remaining_group_members=[]`, `cleanup_error=null`, no
resource stop. Maximum RSS 413424 KiB (Host), minimum disk 4444 MiB. One sdk-r3
attempt returned BUSY75 without starting a child; the run began after Singer's
explicit release. No lock override or polling. Old failures remain intact.

Log SHA256 in batch order:

```
sdk-r1 dc3d30c74f0ca3c3a373ebab9a20e7a912f65f68e16ff3890758739d1e383269
sdk-r2 3be0e8928ec7111d4727c18e2526c6381bc9287ed4075b56f90ae37b6fcc894a
sdk-r3 51be41325630843f2c81ae879714a983055286b62d52e13bb62d5ac2a3b718e6
host-r1 7b6baa902fa63bf081407a6e5c5b2b4d75cfd1790d90544b235f4395da955244
sdk-r4 20d6560aa18b93b1fa07cabc9da8526029cacee9b2c551e31780d54585497baa
sdk-r5 c642c8af8b31e2e293d296f12a3655da9625414aa4852e5205bfcf353cbd4250
sdk-r6 6389d69777477820343db5a36fd9e9542eba8dba78b768c743d2250628d3883d
```

## Reproduction

Run serially using a NEW evidence/basetemp name. `run.py` is the ignored small
source-overlay launcher: SDK `src`, existing primary-078618 installed target and
this Host `backend`; no PYTHONPATH, installation, package copy or private Memory
source. The exact SDK filters used after failures were `not foreign and not
ordinary`, then `real_response`, then `protected_repair`; Host ran its three cases
once. There is no reason to rerun the completed source groups during packaging.

```sh
P=/Users/denny/projects/simple_harness-primary-candidate
W=/Users/denny/projects/simple_harness-typed-recall-context-use-full
PY=$P/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python
E=$W/.local-test-evidence/2026-09-06/mandatory-context-repair
"$PY" -I -B "$P/scripts/run_resource_bounded.py" --evidence-dir "$E/recheck-new" -- \
  "$PY" -I -B "$E/run.py" sdk "$E/recheck-new-tmp" -k protected_repair
# Substitute host for sdk and omit -k for the three Host cases.
```

## Remaining exact boundaries

Dirac final fixed-source/results review is limited ACCEPT for SDK28160d6 and
Hostfe2560c8 (14 unique new controls). H079 packaging/installed consumer remain
main-owned pending gates. Source schema8 decoder is strict; H078's existing
decoder rejects unknown schema8 before executing its checkpoint (source
inspection, not an old-binary smoke). Existing schema6/7 hashes/rows and frozen
H078 wheels are unchanged. No table/schema upgrade or silent old-Run repair.

Native r16 failed Run remains failed with its original evidence. Main will use
the H079/M618 candidate on the original pending item with an ordinary query,
verify actual processing, then ordinary next-turn/cold-reopen no-duplicate. No
OS notification/event publishing was added; presented is never ACK. Program /
401 / 240 / complete native journey are not marked complete by this leaf.
