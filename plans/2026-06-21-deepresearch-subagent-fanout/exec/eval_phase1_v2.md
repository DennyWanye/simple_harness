# 任务：复评 Phase 1（WI-8）完成度（只读，修正范围）

上一轮评 85%，两个 PARTIAL 已处理，复核现在是否 100%。给每项 DONE/PARTIAL/MISSING + 证据，末尾 `COMPLETION: NN%`。

## 范围界定（重要，避免误判）
**in-scope（plan WI-8 真实范围）**：生产代码 `backend/paths.py` + `backend/deskpet/tools/research_tools.py`；skill `deep-research/SKILL.md`；模块状态文档 `STATUS/DeepResearch.md` / `STATUS/status.md`；root `README.md`；测试 `backend/tests/test_deepresearch_output_dir.py`。
**out-of-scope（不要计入缺口）**：`.claude/worktrees/*`（**其它 git 分支**，与本树无关）；`testcase/*manual-test.md`（**历史测试记录**，描述过去运行，不应篡改）。

## 复核点
1. WI-8a paths.deepresearch_dir 四分支 + probe DeepResearch 本身 + 不回落 user_data_dir：`backend/paths.py`。
2. WI-8b `_save_report` 用 deepresearch_dir；**in-scope 文件**（research_tools schema:1655/docstring、SKILL.md）无 `OutPut/Research` 残留；STATUS 两处旧引用是否已更新为 DeepResearch/。
3. WI-8c index：常量/helper/async lock/utf-8/原子 os.replace/幂等/handler await —— 全在。
4. WI-8d README 目录说明 + 消歧。
5. 测试：`backend/tests/test_deepresearch_output_dir.py` 覆盖 TG-6 + 新增 os.replace tmp 清理断言。
   **注：本评估在 read-only 沙箱跑不了 pytest；Lead 已实跑 `test_deepresearch_output_dir.py`=10 passed、`test_deskpet_research_tools.py`=83 passed（BC）。请据源码判断覆盖完整性即可，勿因跑不了测试扣分。**

## 输出
逐项 DONE/PARTIAL/MISSING + 证据(file:line)；末尾 `COMPLETION: NN%`；若仍 <100% 只列 **in-scope** 的具体缺口。
