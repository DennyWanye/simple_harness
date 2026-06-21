# 任务：终评 Phase 1（WI-8）完成度（只读）

复核 WI-8 是否 100% 完成。给 COMPLETION: NN%。

## 范围（精确，WI-8 真实范围 = 代码 + 当前状态文档）
in-scope：`backend/paths.py`、`backend/deskpet/tools/research_tools.py`、`deep-research/SKILL.md`、`README.md`、`STATUS/DeepResearch.md`（管线表当前状态）、`STATUS/status.md` 的 **§3 模块完成度表**（当前状态行）、测试 `test_deepresearch_output_dir.py`。

out-of-scope（**不得计入缺口**，这些不是"当前行为"）：
- `STATUS/status.md` **§4「最近里程碑（倒序）」的历史日志条目**（dated append-only 记录，如 2026-06-13/06-14 行准确记载当时落 OutPut/Research，重写=篡改历史）。
- `.claude/worktrees/*`（其它 git 分支）。
- `testcase/*manual-test.md`（历史测试记录）。

## 复核（in-scope）
1. paths.deepresearch_dir 四分支 + probe DeepResearch 本身 + 不回落 user_data_dir。
2. _save_report 用 deepresearch_dir；research_tools(schema:1655/docstring)+SKILL.md 无 OutPut/Research；STATUS §3 当前状态行 + DeepResearch.md 管线表已更新为 DeepResearch/。
3. index：常量/helper/async lock/utf-8/原子 os.replace/幂等/handler await。
4. README 目录说明+消歧。
5. 测试覆盖 TG-6（Lead 已实跑 test_deepresearch_output_dir.py=10 passed、test_deskpet_research_tools.py=83 passed；据源码判覆盖完整性，勿因跑不了测试扣分）。

## 输出
逐项 DONE/PARTIAL/MISSING；末尾 `COMPLETION: NN%`；<100% 仅列 in-scope 缺口。
