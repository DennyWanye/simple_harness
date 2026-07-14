# DeepResearch quality and Session delivery fix

## Problem

The durable run can complete while irrelevant fallback pages are accepted as
evidence. The full Markdown report is persisted in a blob, but the Session
projection only receives a short summary.

## Acceptance criteria

1. Deterministically irrelevant passages are removed even when LLM reranking
   is unavailable or returns an error.
2. Agent-harness research adds bounded official-source search queries.
3. The final assistant event contains the complete Markdown report while the
   durable blob remains the replay/checkpoint source of truth.
4. Live and rehydrated Session views show the report body and citations.
5. Focused backend tests, workflow regressions, frontend checks, and a real
   Windows click test pass.

## Work items

- [x] Add deterministic evidence relevance gate and source pack.
- [x] Project the complete report into the final Session message.
- [x] Add regression tests for quality and delivery.
- [x] Run automated and real UI tests.
- [x] Record evidence and update STATUS.
