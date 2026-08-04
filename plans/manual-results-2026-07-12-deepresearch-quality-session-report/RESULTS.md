# DeepResearch quality and Session delivery results

## Decision

PASS

Independent final audit: PASS, no P0/P1 findings after three review rounds.

## Automated evidence

- Research/workflow/product regression after independent-audit hardening: 180 passed.
- Workflow evaluation, async handoff, and main wiring regression: 21 passed.
- Final focused delivery regression: 33 passed.
- Frontend workflow/session regression: 35 passed.
- TypeScript project build: passed.

## Real UI evidence

- Run `6f5af6f0d73649249a3d520f48b3ba12` completed from a real Session click/input flow.
- The Session displayed a 5069-character Markdown report with six relevant citations.
- Citations included `Code as Agent Harness`, `AutoHarness`, and an end-to-end Agent Harness paper; no unrelated encyclopedia pages were cited.
- The completed report remained visible after a full DeskPet restart.
- Run `39e57ed0acd241029b8cb16bf4bd9d4e` verified async handoff: the composer returned from Stop to Send while the workflow remained running, then completed with a visible report and citations.

## Notes

- Auxiliary relay calls intermittently returned HTTP 401, but the configured provider fallback completed both real runs.
- Encyclopedia adapters remain available by explicit configuration, but Baidu/Sogou Baike are no longer default direct sources.
- The final topic gate covers ASCII and CJK anchors; ordinary lexical scores and high domain authority cannot independently rescue a cross-topic passage.
- Planner-generated subquestions cannot validate evidence against a drifted topic, and partial LLM reranks verify only the passages the model actually scored.
- Follow-up clarified the fetch stack: both DeepResearch and the compatibility `web_fetch` API are Scrapling-first. Misleading fallback copy now names `scrapling_fetch`; focused fetch/research regression: 107 passed, 1 deselected.
