# 可信披露与工具消息组合验证

最后更新2026-09-06。f3675064由主审和Dirac独立审查后合入工具v2候选，组合固定源码1268e884。生产文件自动合并，仅架构文档冲突保留双方历史。

必要交叉检查21项通过14.55秒：非空完整6item工具组的实际short命中/重开/遗忘、工具child故障原子回滚、披露竞态与历史来源。使用现有H073/M0614/S0313隔离解释器；145默认共享锁2GiB/180秒，PG41526峰212224KiB，耗时15.237秒，exit0，remaining为空、cleanup_error为空。未重跑独立叶原94项，不把执行次数累计为不同验收项。

命令：PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend，现有primary-m0614/venv/bin/python运行scripts/run_resource_bounded.py；pytest -p pytest_asyncio.plugin -q，选择test_primary_tool_message_ingestion.py的test_complete_six_item_tool_group_is_indexed_and_forgotten[False]与test_tool_child_failure_rolls_back_terminal_and_all_children，以及test_trusted_disclosure_races.py、test_trusted_disclosure_sources.py。basetemp为本批r1-db。

独立审查已限定接受faa4c98f支持的非空组；空assistant仍保留M0614原失败，等待SDK后继。非SELF、最终出站撤权原子性、历史拒绝前缀扫描性能、真实Provider/native及240质量未完成，不把这21项外推成全任务完成。

| 本机ignored证据 | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/disclosure-tool-combined/r1/command.log | 1b0de2c1e889ddf40904f20cc7c91540573c4a65fb156bd827a844ed0d3c7481 |
| .local-test-evidence/2026-09-06/disclosure-tool-combined/r1/resource.json | a16a05ad109a6ca3bbd51f20462b4f89762f870a308b5bf5099c4f8a512f0dc8 |
