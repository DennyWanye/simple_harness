# 任务书 E 执行记录

日期：2026-10-31。工作树 `../simple_harness-sol-E`，分支 `sol/E-authorization-goal`。
基线：当前 main `5f1a11c1b`（已含 A/B/C/D/D2 及金丝雀修正）。未推送、未合并。

## 范围与执行依据

任务书 E §4/§5 为验收真相，§10 优先：续办只走 ContinuationPolicy；取消在持有 send lock 后重读队列、扣额前检查。只修改 §3 车道与本记录；不按通用 AGENTS/skill 扩写架构、oracle、journal 等其他文件。任务书 §9 已授权范围内技术取舍，不停等确认。

执行分工：授权及效果账本、Goal 及恢复报告、owner 由独立代理准备测试和实现；主代理负责会话生产接线、取消、关闭、文档与集成验证。独立 review 交叉审查非本人实现。此任务为 SDK 库接口，以 FakeHost 账本测试和 ScriptedProvider 真宿主测试验收，不涉及 UI。

## 先红后绿

实现前冻结的预期：A1 默认/自定义授权在受理和派发检查，异常超时拒绝、人工批准绑定摘要且不复用到新参数；A2 根授权撤销让子队列执行拒绝；I7 取消先持久屏障，派发/续办/队列均检查；O1 owner 持久代次及失锁拒绝；C1 默认关闭拒活、强制关闭保留任务、归档不删数据；R1 恢复报告纯事实；G1 根级唯一 Goal、版本历史及结构收口；E1 claim 与权威回执对账不盲重放。

红测试（源码未改）：
- `test_authorization_port.py`：缺 authorization 模块，1 collection error。
- `test_goals.py`：17 failed（缺宿主 Goal API/恢复报告）。
- `test_owner_and_close.py`：6 failed（缺代次、冲突码及失锁拒绝）。
- `test_cancel_scope.py`：6 failed（缺范围取消、force 和命令 schema）。

## 基线与验证

所有测试从 `sdk/simple-harness-sdk` 执行，使用 `uv run --frozen python -m pytest ... -q -p no:cacheprovider`，不跑全量。
真宿主前已执行 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json`；清单不提交。本地证据 `../../.local-test-evidence/2026-10-31/sol-E/`。
基线指定八文件测试进行中。

## 拿不准的

- 同步原生命令端口与 async AuthorizationPort：保持命令端口协议，在线程内只运行宿主回调，账本仍由会话线程提交，最多等待 5 秒。
- 默认关闭拒活与 Host.shutdown：宿主进入 closing 时仍走资源释放语义；显式 Session.close 默认核活，force=True 明确释放资源且保留业务责任。
- 未指定的预算维度不另起模型计数；Goal 登记正整数预算，模型计量继续读取既有会话及 continuation_usage。

## 结果

实施中；未宣称完成。
