# 任务：评估 Phase 1（WI-8）实现完成度是否 100%（只读）

对照已锁 plan 的 **WI-8**，逐项核对真实代码是否**完整正确**实现。必须读真源码，给每个子项 DONE / PARTIAL / MISSING + 证据(file:line)，最后给完成度百分比。**只认代码事实**，别被注释/命名糊弄。

## 对照基准
plan：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`（§7 WI-8a~8e + §13 R2/R3/R4 对 WI-8 的修正）

## 逐项核对
1. **WI-8a** `paths.deepresearch_dir()`：env 覆盖 → frozen 安装根（`_install_root_for_deepresearch` 直接 probe `<root>/DeepResearch` 本身、唯一 pid probe 名）→ dev `parents[1]/DeepResearch` → home 兜底 `~/DeskPet/DeepResearch` + warning；**绝不回落 user_data_dir()/AppData**。读 `G:/projects/deskpet/backend/paths.py`。
2. **WI-8b** `_save_report` 切到 `deepresearch_dir()`（不再 `output_dir("Research")`）；旧 `OutPut/Research` 文案在 schema 描述(:1655)、docstring、SKILL.md(:86) 全清。读 `research_tools.py` + `SKILL.md`，并全仓 grep `OutPut/Research` 确认无生产残留。
3. **WI-8c** `_INDEX_HEADER`（含 `|---|` 分隔行）+ `_insert_row_after_header`（倒序、兜底重建）+ `_update_deepresearch_index`（async + `_INDEX_LOCK` + utf-8 读写 + 主题转义 + 幂等去重 + 原子 `os.replace`）+ 顶部 `import os`；`_handle_deepresearch` 保存成功后 `await _update_deepresearch_index(...)`。
4. **WI-8d** README 有 `DeepResearch/` 运行时目录 + index 说明 + 与 `DeepResearch.md`(STATUS 文档)消歧。
5. **WI-8e** 打包无需改 installer（确认 deepresearch_dir frozen 分支覆盖即可）。
6. **测试** `backend/tests/test_deepresearch_output_dir.py` 是否覆盖 plan TG-6 列的全部点（四分支 / probe 唯一名 / insert 倒序+兜底 / update 首建+幂等+utf-8+并发 / save 落点）。跑一遍确认全绿：`cd G:/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/test_deepresearch_output_dir.py tests/test_deskpet_research_tools.py -q`。

## 输出
逐项 DONE/PARTIAL/MISSING + 证据；末尾：`COMPLETION: NN%` + 若 <100% 列出**具体缺口清单**（每条可直接照做的修法）。
