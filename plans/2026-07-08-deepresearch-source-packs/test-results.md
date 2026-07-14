# Test Results：deepresearch source-pack 优化

## 命令与结果

| 命令 | 结果 |
|---|---|
| `python -m py_compile backend\deskpet\tools\research_tools.py` | passed |
| `python -m pytest backend\tests\test_deskpet_research_tools.py -q` | 87 passed |
| `python -m pytest backend\tests\test_research_scrapling_priority.py backend\tests\test_scrapling_tools.py backend\tests\test_deskpet_agent_loop.py backend\tests\test_agent_loop_sentinel.py -q` | 16 passed |
| `python -m pytest backend\tests\test_task_drift_fixb.py backend\tests\test_research_sources.py backend\tests\test_search_provider.py -q` | 69 passed |
| `python -m pytest backend\tests\test_deskpet_research_tools.py backend\tests\test_research_scrapling_priority.py backend\tests\test_scrapling_tools.py -q` | 91 passed |

## 可追溯矩阵

| AC | 覆盖任务 | 代码/文档证据 | 测试证据 | 状态 |
|---|---|---|---|---|
| AC-1 | Task 1 | `research_tools.py` `_source_packs_enabled()` / `_source_pack_queries_for()` | `test_source_pack_queries_for_ukraine_and_kill_switch`, `test_research_run_source_pack_kill_switch` | ✅ |
| AC-2 | Task 1 | `research_tools.py` `geopolitics_ukraine` source pack | `test_research_run_source_pack_queries_and_coverage` | ✅ |
| AC-3 | Task 2 | `coverage.route` fields `source_packs_enabled/source_packs_hit/source_pack_queries` | `test_research_run_source_pack_queries_and_coverage`, `test_research_run_source_pack_kill_switch` | ✅ |
| AC-4 | Task 3 | `backend/deskpet/skills/builtin/deep-research/SKILL.md` v0.4.1 文案 | `test_deep_research_skill_mentions_source_packs_and_scrapling_first` | ✅ |

## 审计结论

全部 4 条必须 AC 均有代码/文档证据和自动化测试证据。已补做桌宠 UI 真机手测；证据见 `plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`。

## Manual E2E supplement - 2026-07-08

DeskPet real UI E2E was completed after the automated run. Evidence:

- `plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`
- session: `a8204ba6-18df-4856-ae05-de3a8bdb5f9e`
- screenshots: `plans/manual-results-2026-07-08-deepresearch-source-packs/01-new-session.jpg` through `04-report-and-final-reply.jpg`
- log: `.tmp/dev-tauri.err.log` showed `name='deepresearch'`, `deepresearch_finalize_queued`, and authority-directed source-pack queries for ISW, UN, Reuters/AP/BBC/Al Jazeera.

The first manual run exposed one quality issue: source-pack searches were generated as `Chinese sub-question + site:...`. The implementation was tightened to standalone source-pack query templates, and `backend\tests\test_deskpet_research_tools.py -q` was re-run successfully (`87 passed`).
