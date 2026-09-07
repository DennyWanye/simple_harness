# Primary exact effect sources — v47

Last updated: 2026-09-05. Fixed e31c6efd accepted by independent review; combined51 passed. No main/native release claim. See [COMBINED](COMBINED.md).

## Order and authority

Coordinator authorized the first delivered Primary source index as global v47:
`039_primary_effect_sources_v47.sql`, normal v46→47 migration chain. The deferred
S5c source at 6c8ebddd/e2702006 used a separate prospective v47 migration historically;
coordinator owns its subsequent explicit v48 / 47→48 initializer and versioned A11
mapping. Those original v47 AC/logs are historical evidence, not erased or evidence
for this candidate. This package neither installs S5c tables nor jumps to v48.

One append-only `primary_effect_identities` table records real Host Run/SDK Run,
SDK-supplied effect ID and tool name before actual handler entry. It contains no
output, evidence restamp, TaskScope or authority grant. The captured foreground
owner/generation, current state, SDK binding and writer fence are reverified under
that insert transaction. Existing scope/effect authorization stays authoritative.
The existing recovery table registry classifies this index as A and fences all
writes. Migration DDL, registration, chain receipt and version commit atomically.

At every Provider boundary, runtime enumerates search/page-in by the Host index
and reads actual terminal results through public SDK `read_effect`. It never
queries SDK-private SQL or guesses effect IDs from raw call IDs. Create producer
proof reconstruction stops at the exact indexed producer sequence; search before
create therefore belongs to its dependency closure even with no scope reservation.
Page-in cannot silently bypass the check: unproved ordinary content denies the
next Provider call; only the previously supported exact initial projection or
separate skill configuration path remains admissible. Generic page-in source
projection is not asserted complete.

## Compatibility and corruption

New starts freeze `primary_effect_index_version=1`. Existing settled Runs acquire
no fabricated index rows on migration. Old create producers without that contract
supply structural facts/current USER only, with explicit original-source gaps;
old text is not restamped. A current-run proof reconstruction missing the index
contract rejects rather than claiming complete enumeration. Old archive bytes
are never rewritten. This package does not certify pre-fix candidate-generated
history proofs as complete; 8e896472 was blocked before production integration.

Reopen checks actual table/index/append-only/recovery fence DDL as well as the
migration checksum and registration. Future versions reject before any legacy
registration repair. Tests of missing schema are explicit corruption negatives,
not legacy fixtures. The historical v46 tests retain an explicit frozen v46
migration lane and their exact v46 behavior; new tests assert default v47.

## Evidence

All commands run with `PYTHONPATH=backend` and
`.local-test-evidence/2026-09-05/primary-history/venv067/bin/python -m pytest`,
`-q -p no:cacheprovider`. Harness0.7.2 / installed exact Memory0.6.7 wheel
7dd224c29923ab1346a78bb8529d9426559bb1e3a38b8c0964f57cc8687e9c3d;
local deterministic Provider only, no external Provider/native. Test dependencies
are borrowed from main's site-packages via the isolated venv pth, not an SDK source overlay.

- Dirac fixed red copied as `backend/tests/execution/test_unscoped_search_late_forget.py`:
  source-bound real create, 10 real ordinary turns, public suppression excluding
  indirect history coverage, actual unscoped search, then source USER forget.
  Before fix 1 failed / 4.86s / exit1: 2 delegates, 0 scoped search reservations.
  Fixed exact case 1 passed / 6.96s / exit0. Original reviewer files untouched.
- That module now also covers unsuppressed completion and search→real create /
  file effect / terminal / original USER dependency in the actual SDK create result.
  With `test_scope_disclosure_runtime.py`: 17 passed / 48.61s / exit0.
- `backend/tests/execution/test_primary_page_in_sources.py`: real request-bound
  ContextPageInStore + production handler, actual successful SDK tool result;
  unproved ordinary content blocks second delegate, FAILED/no replay:
  1 passed / 0.81s / exit0.
- `test_primary_foreground_runtime.py`, `test_primary_create_new_runtime.py`,
  `backend/tests/memory/test_startup_epoch_dispatcher.py`: 32 passed / 25.10s / exit0.
- `backend/tests/memory/test_effect_closure_migration_v46.py` +
  `backend/tests/memory/test_primary_effect_sources_v47.py`: 11 passed / 1.50s / exit0.
  One intermediate test-fixture cleanup ordering failure was corrected without
  changing production semantics or the historical v46 assertions.

Raw logs remain ignored in `.local-test-evidence/2026-09-05/primary-history/`.
No full 96-case rerun, app build, Provider call, SDK/pin change or production switch.
Assistant short indexing/analysis is not connected to startup or terminal; the
public source-only admission and exact selected-source closure remain successors.

SHA-256 of ignored raw logs:

- `unscoped-search-red.log`: `d1ea62a5c09376c8f7084639731c802f276af802bda7699338837de7deb1e074`
- `unscoped-search-green.log`: `79819081cfccee9f55dd3f623fef632cf2b6563c5421f07e70851574bcc2ea9d`
- `source-search-adjacent.log`: `12660153689ccad4fa0b88625cf927e964577e965e71e29c3fd8123031875bd1`
- `page-in-source.log`: `f9c9506451c8c246d741ba6f531e0c5616255ea5048df68bdc58562afc09939b`
- `source-startup-adjacent.log`: `c81c9d2667e844f2b41df7dbe4504c70e0c7322114ec304eed4f89cb969c590e`
- `source-migrations-final.log`: `5a3e4310217229c11e7abe4a5b5a6c7d605b76641f3ed10b98b47d193de27070`
- `source-migrations-final2.log`: `317bcea011f29d6b63a59e38cd0c64c7ec3761308fd492caf34180c412028d55`
