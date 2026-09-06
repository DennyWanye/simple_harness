# Selected short-source Host leaf

2026-09-05; isolated from `54aa2f88`. Main owns main.py, HumanMemoryV7Runtime,
primary_context, startup/terminal registration hookup and final provider boundary.
This leaf performs no registration, analysis, new Run, source admission or SDK writes.

## Integration surface

```python
authority = PrimaryConversationAuthority(host_db, subject=actual_subject,
                                         primary_ref=actual_primary_ref)
reader = SelectedShortSourceReader(authority, manager=actual_v7_manager,
                                   principal=actual_principal)
observed = await reader.resolve(
    disclosure_context=actual_current_request_disclosure,
    bindings=tuple(actual_selected_HistoryShortHorizonBinding),
)
# observed.items: tuple[SelectedShortSourceItem, ...], exact input order
# item.binding / visible / reason / evidence: tuple[(evidence_id, envelope_hash), ...]
# observed.accepted_bindings: accepted exact original bindings, preserving duplicates
# observed.visibility_dependencies: schema2 evidence/recall/short_horizon
```

Main must filter the actual selected hits by the corresponding `items[i].visible`
before constructing content, then carry `observed.visibility_dependencies` into
`RecallLanes.short_history_dependencies`. Do not retain rejected hit text. Empty
accepted results mean no short content, not a replacement for ordinary history.
`recall` remains empty: standalone short bindings are never fabricated typed recall.

Source batch comes exclusively from the public Memory0.6.9
`resolve_short_horizon_sources(principal, disclosure_context, bindings)` keyword
API. Root-exported DTOs are consumed by exact type. Its public positional contract
binds one response item to each input, including duplicates; Host does not recompute
SDK chunk IDs, binding hashes or request hashes. Snapshot subject, version and item
count are checked; missing capability/malformed snapshot is an explicit unavailable
exception with no indexed-roots/private-SQL fallback. No cache or epoch shortcut.

For each visible/complete result, resolve every returned registration ref through
`PrimaryConversationAuthority`. Compare all eleven source ref fields to the Host's
verified S1/metadata: evidence and envelope IDs/hashes, source ref/hash, sanitized
hash, admission receipt ID/hash, registration ID/hash, ordinal and role. Require
ordered unique sources, exact group count, subject/primary, and common group ID,
sequence and manifest. A partial or mixed group has no usable evidence output.

After these potentially slow reads, reuse `PrimaryHistoryPolicy.check_dependencies`
for that hit's exact evidence roots and original short binding. It recursively
validates Host USER/terminal/evidence_refs, terminal identity and inherited proof,
then makes a fresh public SDK visibility call. Missing/corrupt/suppressed/oversized
Host lineage rejects the whole affected hit, retaining unrelated accepted hits.
The returned evidence union contains selected registration roots; terminal and
other ancestors remain linked by actual S1 refs and are recursively checked by the
existing policy. No all-indexed roots are added to this union.

These checks are observations, not execution grants. Per-hit late checks do not
claim a shared epoch with the first source snapshot or with each other. Main must
still run the existing fresh history/outbound fence on the complete selected union
at the actual last writer/provider boundary; suppression or expiry after this
reader returns can invalidate its result. The caller supplies the real current
disclosure and authenticated principal; this reader cannot create their authority.

## Work and dependency limits

- At most256 selected bindings per call; zero is a no-work result when the public
  capability exists. One public source-resolution batch; at most one later public
  history batch per visible, Host-provable hit. No unbounded polling or retries.
- Each group has at most256 source refs. Existing PrimaryHistoryPolicy limits
  remain256 bindings/4096 edges/depth64. The combined output retains the existing
  256 dependency limit; an overflowing whole hit is rejected, never truncated.
- No SQL CPU/IO deadline or global indexing cost bound is claimed. Host registration
  lookups use the existing authority. Imported `reconcile()` still scans completed
  groups and rebuilds the index; main owns its scheduling/cost and default hookup.

Unchanged dependencies copied from fixed `55b9e402` (includes `2fb1d190`):
conversation_registration.py, short_indexing.py, test_primary_short_ingestion.py.
Their earlier17 tests are not automatically new-candidate PASS evidence. The real
per-message producer is already present in base54aa2f88; no producer/runtime edits.

## Validation state

Initial SDK069 source-overlay probe completed12 real Host deterministic runtime
turns, S1/outbox, source-only assistant admissions, index and actual short audits.
On reopening the copied complete SDK DB, all17 new case setups stopped at SDK
`schema_upgrade -> upgrade_validation -> evidence_filter_policy_unsupported`.
This is a setup ERROR, not reader PASS. Reported to SDK owner; no SDK edits or
initializer bypass. Raw first log: `.local-test-evidence/2026-09-05/selected-short-sources/source-overlay-draft.log`.

Memory069 is not yet a frozen installed wheel in this probe. Main's existing
environment is reused without installation changes; explicit PYTHONPATH selects
the 069 source tree. No native/network/paid Provider or full suite is run. Final
results after the SDK owner's fix: **19 passed in11.29s**, focused ruff passed.
Fresh-store positive/terminal-forget control separately passed before the reopen
fix. Intermediate15pass/3fail included two mistaken fixture independence assumptions
(turn2 actually inherits turn1 terminal) and one append-only-blocked corruption
injection. Tests now forget the later group to prove the prior independent hit stays
usable; an explicit real-terminal dependency assertion preserves ancestor propagation.
Missing-S1 injection removes triggers only in the test's copied Host DB. Product
reader code was not weakened to satisfy these failures.

```sh
PYTHONPATH=backend:/Users/denny/projects/simple-harness-memory-sdk-069-existing-data/src \
  /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python \
  -m pytest backend/tests/memory/test_selected_short_sources.py -q --tb=short --show-capture=no \
  --basetemp=.local-test-evidence/2026-09-05/selected-short-sources/pytest-final
```

Coverage uses12 actual deterministic Host turns, delivered USER lineage, source-only
assistant admissions, a real index and actual public short audits, then public reopen.
It proves selected-only union (2 roots from24 indexed), no completed-group enumeration,
unrelated forget isolation, inherited USER/terminal suppression, a policy change
between source observation and late check, eleven-field comparison controls, partial/
mixed group rejection, missing Host S1, duplicate input order, dependency deduplication,
late final-history-fence rejection, missing public port and oversized input rejection.
The more-than256-indexed-roots dataset and actual next-Provider request are not this
leaf's acceptance claim; main owns those combination cases. No imported17-suite rerun.

Run Python imports Harness0.7.2 from installed site-packages. The installed Memory
distribution metadata remains0.6.7, while actual imports are the explicit069 source
overlay. Source observations span ongoing SDK edits (`upgrade_validation.py` changed
between the two full leaf attempts); this is not an exact frozen069 identity or wheel
acceptance. Before/after module-path and source-byte inventories are retained locally.

Raw evidence prefix `.local-test-evidence/2026-09-05/selected-short-sources/`:

| File | SHA-256 |
| --- | --- |
| source-overlay-draft.log (17 setup errors) | 501e134497f33eca61880801028970fc82a8c8caf9a3c1c36a223993db633a50 |
| source-overlay-fresh.log (1 pass) | e0c1a1b9859716ebd41b82172e962f847b66600d56dadd28d123d3915d708c1e |
| source-overlay-reopen-fixed.log (15 pass/3 fail) | 5f7baf1e1967f0f5c542222260b0272fa6a8899c0add343622ae14588d16d3f9 |
| source-overlay-final.log (19 pass) | ca22f1ed261d52f546638fc6aa3c8c18bcaaf96f907884a24227eed30fec469d |
| ruff.log | 82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18 |
| source-identity-before.json | 451c4a781bf328752251d859853af6f6096ac22ddc8c2d81088fa2320a41bde4 |
| source-identity-after.json | 3d5893be8d9d3814fba281c37312cb6873259decebb4d34d8701cb6b33188f08 |

## Fixed069 installed follow-up

Host product code remains `61c2f83b`. Consumed Memory source
`f92fac121d2d9ce195b5715d272023e5aec920e3`, wheel
`cf14902223063ba3586032553c3737d0ee0c13311df3e29bd4561629494d6719`, plus exact
Harness0.7.2 wheel `53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`.
The wheel is from the SDK owner's approved offline build, not a Host rebuild.

Created an independent venv under the ignored evidence prefix, leaving original
Host/SDK environments unchanged. An initial offline dependency resolution was
blocked by service SDK's historical direct URL. Reused existing dependency files
via APFS copy-on-write clones, then explicitly installed the two exact SDK wheels
with `uv pip install --offline --no-deps`. No dependency downloads or source overlay.
This is an isolated installed environment, not a newly resolved dependency matrix.

Used its Python `-I`, manually added only this tree's `backend` to sys.path, and ran
the same selected leaf file through `pytest.main` with a fresh `pytest-installed`
basetemp. **19 passed in10.85s**, exit0. Two assert-rewrite warnings arose because
Harness was imported first for identity verification. Module `__file__` checks after
the suite confirmed all168 loaded SDK modules inside the independent venv. Memory68
and Harness151 package files matched source/wheel/installed bytes exactly. The
19 installed cases replace neither the earlier failure logs nor main's integration
acceptance; they are not added to the source-overlay count as38 distinct tests.

| Installed evidence file | SHA-256 |
| --- | --- |
| installed-leaf.log | fc592983e45b0974786adf7d7d7456591431a1380d995fdfc0e9165e3e28be95 |
| installed-identity.json | 39b721ce899376042dff08dbd5b8cee57721ffc965d6081a8858efe6b0c2e1df |
| installed-module-origins.json | febda4d0e40fac041d39d5e81230f0377a23e98056b4a9074477c62fca2578fc |

Main still owns runtime factory/context/default hookup, actual old-store upgrade,
combined last-writer/outbound verification, and native acceptance. No paid provider
or native session was started here; no SDK upgrade of user data was performed.

## Combined candidate0610 verification

2026-09-05: reviewed61c2f83b and installed069 handoffd0a388e3 integrated into
main-owned candidate9a8f8564. Only architecture-history inserts conflicted; all
source files merged without rewriting implementation. Both ingestion and selected
reader files passed34 tests44.35s using the exact installedMemory0610 candidate
and existing Harness0.7.2, no source overlay. The actual production startup/terminal
indexing scheduler and selected-hit runtime wiring remain pending; this source
integration alone does not make the short lane fully available.

Command: dedicated candidate Python -m pytest
backend/tests/memory/test_primary_short_ingestion.py
backend/tests/memory/test_selected_short_sources.py -q.
Ignored local log .local-test-evidence/2026-09-05/primary-candidate/selected-short-installed0610.log
SHA256 3e6b59475bb575fcba6bf365f2103ce96336902a5250e4e1b5fcead2893febd7.
