# 任务：生成 Phase 1（WI-8 DeepResearch 落盘+索引）的 windows-mcp 手工测试文档

为已实现的 **WI-8**（所有 deepresearch 报告落安装目录 `DeepResearch/` + 维护 `DeepResearch/index.md` 总索引）写一份**详尽的手工测试文档**，供真人/windows-mcp **模拟人工点击+输入**执行。把文档**写到** `G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/exec/manual_test_phase1_draft.md`。

## 先读
- plan WI-8 + TC-F6：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`
- 实现真相：`G:/projects/deskpet/backend/paths.py`（`deepresearch_dir`）、`G:/projects/deskpet/backend/deskpet/tools/research_tools.py`（`_save_report`/`_update_deepresearch_index`/`_INDEX_HEADER`/`_handle_deepresearch`）
- 参考现有手测文档的写法/详尽度：`G:/projects/deskpet/testcase/2026-06-20-deepresearch-upgrade/manual-test.md`

## 被测行为（要全覆盖）
1. dev 模式下 deepresearch 报告落 **repo 根 `G:/projects/deskpet/DeepResearch/<slug>-<ts>.md`**（**不再** `<user_data>/OutPut/Research/`，**不进 C 盘 AppData**）。
2. `DeepResearch/index.md` 首次自动创建（含标题+说明+表头 `| 日期 | 主题 | 文件 | 来源数 | 域名数 | 模式 | 子问题 |` + 分隔行）。
3. 每份新报告在 index **表头后插入一行（倒序，最新在最上）**；行内文件名是**可点击相对链接**；列值正确（mode 列扁平为 `flat`）。
4. 幂等：同一报告文件不重复加行。
5. 中文主题：文件名 slug + index 行 **UTF-8 不乱码**；主题里的 `|`/换行被转义不破坏表格。
6. artifact 卡片 path 指向 `DeepResearch/` 的 .md，可点开。
7. env 覆盖 `DESKPET_DEEPRESEARCH_DIR` 生效（可选 TC）。
8. 诚实边界：打包应用落"安装目录/DeepResearch"——windows-mcp 在 dev 跑，打包路径分支属 env-limited，需声明。

## 文档要求（严格按项目手测纪律）
- 真机环境前置：Tauri dev 启动方式（**不要手动起 backend**，给 Tauri 注入 `DESKPET_BACKEND_DIR=G:/projects/deskpet/backend` + `DESKPET_PYTHON=G:/projects/deskpet/backend/.venv/Scripts/python.exe` 让它跑本树代码；确认 log 出现 `[backend_launch] Dev python=...` 而非 Bundled exe）；dev 自动登录（relay）。
- 每个 testcase 必须含：`case ID` / `坐标=(x,y)|动作=click/type/...|期望=...` 的 declare 行 / 操作步骤（真模拟点击+输入，中文用剪贴板+Ctrl+V）/ **判定证据**（落盘文件路径截图 + `DeepResearch/index.md` 内容 + tauri-dev.log grep 关键事件）/ PASS/FAIL 判定。
- 禁止用脚本回放/import/WebSocket 当 UI 证据。
- 覆盖 happy-path + 5~8 条边界（首建 index、第二份倒序、幂等、中文不乱码、转义、env 覆盖、落点非 C 盘、artifact 可点）。
- 给出文档头部元信息（被测功能/范围/目的/用例数/是否需 windows-mcp）便于登记进 testcase/index.md。

## 产出
把完整 Markdown 文档写到 `exec/manual_test_phase1_draft.md`。最后简述：共几个 TC、覆盖哪些边界。
