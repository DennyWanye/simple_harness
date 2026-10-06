# 夜间车道 N3d 记录（2026-10-07）

工作树 `simple_harness-n3d`，分支 `night-n3d`，基线 main `47bf424a`。只改 Host 测试与发版脚本；没改 SDK，没改 Host 产品代码。Host 环境是 opt.166 轮子（`0.13.0.dev20260925+opt.166`）。

## N3-07（A40）真删状态文件后重启 Host

- 状态：完成。
- 改动：`backend/tests/orchestration/test_assurance_quarantine.py` 新增用例 `test_a_really_deleted_root_state_file_restarts_the_host_in_quarantine`。
- 做法：不用替身。用真 SDK 起 Host 一次，SDK 真装保证根（诊断报 NATIVE）。停服务。真删 `assurance-root-state.json`。重新起 Host。
- 断言：
  - `status().state == "quarantined"`，`available` 是 False，原因里有"隔离"，不开驱动循环。
  - `assurance_root` 报 `QUARANTINED`，阻塞码 `ROOT_QUARANTINED`。
  - `mission_assurance_root_diagnostic` 可用，报 QUARANTINED，只有五个非披露字段。
  - 建任务、列任务、读通知、读策略都答 `orchestration_unavailable`，原因里有"隔离"。没有建出任务。
  - 状态文件没有被写回，关服务后也没有。
- 顺手：删掉本文件的 autouse 替身 `_validated_gate`。它是给 opt.164 轮子用的（当时没有 VALIDATED 部署清单）。opt.166 轮子真读出 `VALIDATED`，不再需要。文件头说明同步更新。
- 改坏：`service.py` 的 `_root_quarantine` 固定返回 None。新用例红（`status["available"]` 是 True），原第一条用例也红。用 cp 备份恢复，清 `__pycache__`。
- 结果：本文件 3 条全过。
- 发现（不是缺陷，供参考）：改坏后 Host 会把一个 SDK 已隔离的根当"可用"开起来。现在只靠 `_root_quarantine` 这一处判断挡住。

## N3-11（T10）Host 诊断两份测试对 opt.166

- 状态：完成。两份用例原样对 opt.166 轮子全绿（各 3 条，共 6 条）。不需要改用例口径。
- 加进发版脚本：`scripts/release_sdk_opt.sh` 钉版后用例列表加 `test_diagnostics_contract.py`、`test_mission_diagnostics.py`。`bash -n` 通过。
- 加一处断言：`test_diagnostics_contract.py` 断言归因报告 `version == "attribution-v2"`。原用例没钉 T10 的版本。
- **Host 产品缺陷（交主会话）**：
  - 位置：`backend/deskpet/orchestration/diagnostics.py` 的 `_attribution`（白名单过滤）。
  - 现象：SDK 的 attribution-v2 带 `goal_chain`（每级目标结论：结论编号、目标任务编号、义务编号、是否根、子结论编号、贡献的验收编号），产物行带 `acceptance_id`。Host 白名单没放行这两项。诊断报告写着 v2，却没有 v2 新加的内容。
  - 核实：加 `report["attribution"]["goal_chain"]` 断言后 `KeyError: 'goal_chain'`。
  - 建议改法：`_attribution` 加 `goal_chain`（按上面六个字段 `_pick`，全是编号，没有自由文本），`final_products` 的 `_pick` 加 `acceptance_id`。再在 `test_diagnostics_contract.py` 加 `goal_chain` 断言（改坏：去掉白名单这一项要红）。
  - 今夜没改，因为本车道不改 Host 产品代码。

## 测试数字

- `test_assurance_quarantine.py` 3 过；`test_diagnostics_contract.py` 3 过；`test_mission_diagnostics.py` 3 过。合计 9 过，0 失败。
- 只跑这三个文件，没跑整目录。

## 改动文件

- `backend/tests/orchestration/test_assurance_quarantine.py`
- `backend/tests/orchestration/test_diagnostics_contract.py`
- `scripts/release_sdk_opt.sh`
- 本记录
