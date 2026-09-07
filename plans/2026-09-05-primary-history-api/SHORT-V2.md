# Primary history proof v2 — standalone short bindings

2026-09-05. Host helper only, extending the exact existing API/history policy.
Installed Memory0.6.7 source `fa6badd086d089e3e1752df45993ce0ccba98377`, wheel
`7dd224c29923ab1346a78bb8529d9426559bb1e3a38b8c0964f57cc8687e9c3d`; Harness0.7.2.
Own ignored venv067 installs both wheels; other Host dependencies use the main
site-packages .pth. This is not a newly resolved whole-Host dependency lock.

Version1 retains its exact existing keys and typed recall meaning. New version2 has
exact keys schema_version/evidence/recall/short_horizon. The first two lanes retain
v1 entries; short_horizon entries are exactly audit_id/chunk_ref/content_hash and
become public HistoryShortHorizonBinding DTOs. They join the SAME <=256 union batch,
ordered binding-hash verification and recursive terminal dependency traversal.
No fake typed tuple, ignored unknown lane, epoch cache or old-archive rewriting.
A missing SDK capability rejects v2 instead of treating it as an empty dependency.
Runtime must bind actual selected hits and returned bytes; this parser cannot infer
completeness from a model response. New source/manifest proof fields need their own
agreed version; this change does not add arbitrary keys to v2.

Eight new cases plus45 existing history/read-API cases: **53 passed in20.75s**.
The new positives use an explicit fixture policy to test Host batch routing and
recursive page/detail filtering; they do not prove production short ingestion.
An actual installed public manager additionally allows a cold original USER but
denies the added unselected/forged short audit binding. SDK067's18 public consumer
stages separately verify real selected short hits; no count is merged with53.
Actual Host conversation registration/short projection production wiring remains
under investigation, and runtime067 integration/native acceptance remain separate.

Command:

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-short-helper/venv067/bin/python -m pytest backend/tests/memory/test_primary_short_visibility.py backend/tests/memory/test_primary_visibility.py backend/tests/memory/test_primary_read_api.py -q -p no:cacheprovider
```

The old helper from2299ef55, loaded in an isolated test process while keeping SDK067
installed, fails the new mixed-v2 positive (1failed). This is a prior-Host-helper
counterfactual, not a whole old candidate run; the active helper file was not replaced.
First attempt7passed/1failed lacked classification_policy in a newly constructed
test manager; after supplying the explicit policy, the cold-USER positive passed.
No product policy was loosened. Helper/new test ruff passes (import formatting only).

Raw files under this tree's ignored `.local-test-evidence/2026-09-05/primary-short-helper/`:

| File | SHA-256 |
| --- | --- |
| combined.log | 8a75b1220d5f5563f929da7152dca1e8bd68cefdae5b2bdac19e138dc7c46479 |
| red-old-helper.log | cde8deaeecd750c14e7fcdca2bc3b8b97a9c2b10b959e88c4757c98e9db786b5 |
| identity.json | 4337dd7eb65f85e9891f9e6be2ffdebc7187e1441d66f75c2990fe0dc65f8408 |

Independent review pending. No native/provider-remote evidence, main cutover,
production short-memory completion or original program gate claim.
