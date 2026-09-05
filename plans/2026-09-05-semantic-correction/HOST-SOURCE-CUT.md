# Trusted Host origin and first-action cut facts

Last updated: 2026-09-05. Independently reviewed, scoped source ACCEPT.
Memory protocol source8bd93c9d23a33726a901e8dca640a3ee5ea0a0a4 supplies the public
fact carriers; this verification uses a source overlay, not a successor wheel.

`HostHistorySourceAuthority` reads only Host state.db in read-only snapshots.
It recomputes the immutable initialization receipt and marker hashes, binding
store epoch, owner and actual primary foreground stream. Source resolution checks
the actual S1 envelope/receipt and queue request/hash. Atomic v2 and legacy v1
origins stay distinct; missing queue order returns unknown. No Memory ingestion
is fabricated for an asynchronously pending source.

The existing authenticated cognitive action store now captures MAX enqueue
sequence and appends its first v2 action S1 in one fenced BEGIN IMMEDIATE.
Its payload stores namespace and through_sequence. Request identity is derived
from the actual evidence ID; target scope is bound by the original action payload.
The cut hash and future SDK decision hash are not embedded back into the action.
Replaying a v2 action preserves its original cut; a v1 action retains its exact
original bytes and has no cut. A failed pre-commit action retains neither action
nor cutoff. A later successful first action may capture its then-current frontier.

The public cut resolver matches an actual SDK decision's subject, request ID,
memory scope/target, reason and effective_at against the dedicated original Host
action. It returns the original action evidence ID/envelope hash with the cut.
Generic primary.append cannot impersonate the dedicated action source domain.
The SDK still owns equality computation and suppression; these facts grant no
permission, and the Host never reads private SDK tables.

Verification: existing action and atomic source stores13PASS/3.00s using the
installed069 environment. New protocol-source tests4PASS/1.37s, plus rollback and
fresh-store namespace separation2PASS/0.95s. The final actual cognitive API7 plus
all public fact tests6 passed13/5.16s. Counts overlap and are not summed. Test and
changed-module ruff passes. Independent review checked the three changed files;
it did not repeat the tests. No paid Provider or native run was used in this leaf.

Source-overlay command: set PYTHONPATH to the duplicate-source Memory SDK's src,
then dedicated candidate Python `-m pytest
backend/tests/memory/test_primary_cognitive_controls.py
backend/tests/memory/test_history_source_authority.py -q`.

Ignored local evidence under `.local-test-evidence/2026-09-05/primary-candidate/`:

- first-action-cut-stores.log: b9db7d77cdba4eec4e4b2af78a512896957bf7b289954b05b8112dec3bf54196
- history-source-authority-overlay.log: 9d83a63a147a91cc9a6aea4bebbe720d2e25f6b77faeb06b3d313c3c333ff2ef
- history-source-cut-faults-overlay.log: bf811777d11ccd1d989069be4447a1c1164f604112e1668c52c4ace761bad987
- history-source-cut-api-overlay.log: f5a911d9741d5825e8c2a2971b7bc92b54b259fdc9e5ea91dd4bb49b5118f1b3

Remaining: builder composition, shared SDK enforcement across ordinary entry
points, duplicate-source negative controls turning green, installed successor
and native verification. The old native v1 forget has no proven cut; an unknown
boundary must not be relabeled as a repaired historical fact. Fresh same-text
behavior on a store containing such legacy directives needs separate verification.
