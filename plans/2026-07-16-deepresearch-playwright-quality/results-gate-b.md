# Gate B result — research quality kernel

Status: **PASS**
Date: 2026-07-16
Scope: Tasks 3–5 and Task 9 quality kernel, plus Task 8.1 loop policy and Task 10.1 deterministic report/rubric prerequisites. This does not claim the v5 graph, browser product packaging, UI, default wiring, installer, or real-provider E2E are complete.

## Frozen product facts

- `policy_education` produces the exact six core dimensions required by SC-EDU-01.
- Generic fallback is Who / What / State / Drivers / Outlook; technology intelligence has a separate six-dimension profile.
- Every core dimension receives `general`, `official`, `temporal_statistics`, and `comparison` query families.
- Search Gateway dimension batches share one atomic parent budget and reserve every core dimension before distributing surplus capacity.
- Chinese relevance uses domain terms plus 2/3-character n-grams; the legacy quick-search ranking path remains unchanged.
- `EvidenceAdmissionPolicyV1` enforces invalid-page → passage admission → source-family folding → coverage. Its locked hash is `33538ebb585821eb9e158ae22df177f89e165a5c54ff6ae8d2cdafe434d62cee`.
- Invalid-page ratio counts only fetched invalid pages; exact/strong duplicate ratio is measured across all fetched candidates. Weak family matches are audit-only and never folded.
- Analysis passage allocation is core-first and fails closed if the budget cannot provide one passage for every evidenced core dimension. Direct findings must be supported facts; inference remains separate.
- `ReportQualityRubricV1` uses Decimal half-up component scoring, six fixed dimensions, hard-failure precedence, and three delivery states. Its locked hash is `2b25e991a799bfb0a378b49058f312004573871e6cbc740e9d64fac1cbec3d94`.
- Loop policy fixes soft checkpoint / lease / automatic cap / plateau at 300 / 120 / 900 seconds / two rounds. Query-strategy and continue-child claims are scoped to the exact durable operation.

## Main-thread verification

```powershell
# Gate B + Gate A contract/effect adjacency
$tests = @(
  'tests/test_workflow_deep_research_v5_contracts.py',
  'tests/test_workflow_deep_research_v5_contracts_identity.py',
  'tests/test_deep_research_v5_brief_policy.py',
  'tests/test_deep_research_v5_queries.py',
  'tests/test_deep_research_v5_evidence.py',
  'tests/test_deep_research_v5_loop_policy.py',
  'tests/test_deep_research_v5_analysis.py',
  'tests/test_deep_research_v5_report.py',
  'tests/test_research_llm_v2_adapter.py',
  'tests/test_research_call_effect_adapter.py',
  'tests/test_workflow_budget_effects.py'
)
$gateway = Get-ChildItem tests -Filter 'test_search_gateway*.py' | ForEach-Object FullName
.\.venv\Scripts\python.exe -m pytest @tests @gateway -q
# 247 passed in 15.72s

# Evidence policy after denominator audit
.\.venv\Scripts\python.exe -m pytest tests/test_deep_research_v5_evidence.py tests/test_deep_research_v5_brief_policy.py tests/test_workflow_deep_research_v5_contracts.py tests/test_workflow_deep_research_v5_contracts_identity.py -q
# 60 passed

# Loop policy after exact-operation lineage audit
.\.venv\Scripts\python.exe -m pytest tests/test_deep_research_v5_loop_policy.py tests/test_workflow_deep_research_v5_contracts.py -q
# 48 passed

# Search Gateway parent budget + ranking + legacy compatibility
$gateway = Get-ChildItem tests -Filter 'test_search_gateway*.py' | ForEach-Object FullName
.\.venv\Scripts\python.exe -m pytest @gateway tests/test_deep_research_v5_queries.py -q
# 83 passed

# Analysis + evidence + v3 support compatibility
.\.venv\Scripts\python.exe -m pytest tests/test_deep_research_v5_analysis.py tests/test_deep_research_v5_evidence.py tests/test_workflow_deep_research_v5_contracts.py tests/test_deepresearch_v3_claim_support.py tests/test_workflow_deep_research_v3.py -q
# 107 passed

# Report/rubric after intent-mapping audit
.\.venv\Scripts\python.exe -m pytest tests/test_deep_research_v5_report.py tests/test_workflow_deep_research_v5_contracts.py tests/test_deep_research_v5_queries.py tests/test_deep_research_v5_evidence.py -q
# 79 passed
```

## Gate decision

**Gate B passes for the isolated quality kernel.** Gate C–F remain open. In particular, these tests do not substitute for the production v5 graph, bundled browser resolver/pool, checkpoint recovery, real Search Gateway → Fetch → Playwright → report integration, desktop actions, or human report review.
