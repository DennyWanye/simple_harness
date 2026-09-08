# HM-TO-A6 首日尝试记录（2026-09-08 上午）

> 两次尝试均未跑满 24 轮，但各自暴露了必须先修的缺陷；证据目录均保留于 `.local-test-evidence/2026-09-08/native-a6-7cec5249/`。

## 尝试 1：gpt-5.6-luna（10:47–10:56，`primary-ui-q_7sww4b-luna-abandoned`）

- 预检 200；T1 第一条消息 luna 传输超时 240 s → F06 路径生效（`reconcile.unknown_settled` → `provider_attempt.degraded` → 同 request_ref 重试），Run 未停摆。
- 驱动 365 s 超时后已发 T2；为保证 24 轮状态干净，按用户决定切 DeepSeek 全新重跑。

## 尝试 2：deepseek-v4-pro（10:56–11:05，`primary-ui-cw3ifsxo`）

| 轮 | 结果 | 发现 |
|---|---|---|
| T1–T5 | Run 全部 COMPLETED（40/32/6/21/29 s） | T1 记忆头落库；**T2 的事实未落库**：记忆分析通道 6 次 `product_provider_response_parse_failed`（HTTP 200、content=str + tool_call=1，SDK `_parse_response` 抛裸 `ProviderProtocolError`，日志无原因）→ 三次重试后 handed_off |
| T5 完成后 | `foreground.runtime.failed error_code=primary_read_policy_unavailable`（`PrimaryVisibilityError`，`primary_visibility.py` ~392 行吞掉了真实异常） | 前台驱动永久停摆：UI 显示「等待主对话就绪 / 正在重新读取…」，发送不再产生 Run；同一 envelope 每 ~2 s `memory.evidence_ingestion_replayed`（约 28 次/分钟）持续到人工中止 |
| T6/T7 | 驱动误判「已发送」 | 旧驱动以证据信封 +2 判定结算，早于 Run 终态；随后发送落在不可发送状态，消息丢失 |

另一关键事实（核对脚本 `scripts/native/a6_verify.py` 发现）：DeepSeek 内置窗口 1,000,000 → 分区档 32768、`effective_input_budget=895904`，24 轮内**不可能**发生整组裁剪，A6-3/A6-4 在 DeepSeek 默认配置下永远 INCONCLUSIVE。方案：下次启动前在 userdata 写 `model_overrides.toml`：

```toml
[models."deepseek-v4-pro"]
context_window = 32000
```

（`backend/llm/model_info.py` 三层 resolve：内置表 ← `<user_data_dir>/model_overrides.toml` ← 项目层；启动器已把 `DESKPET_USER_DATA_DIR` 指向 `<E>/userdata`。）

## 已采取的动作

1. 驱动脚本 `scripts/native/a6_driver.sh`：发送后 30 s 内必须出现新 Run head，否则重发一次再记 `send_failed`；结算只看最新 Run head 的终态；UI 轮把观察结果（如 `nodes=2 edges=1`）写入进度行 `note`。
2. 核对脚本 `scripts/native/a6_verify.py`（Opus 子代理编写，已在过往证据与本次证据上跑通；指纹重放 20/20 一致）。
3. 三个 Opus 子代理分别处理：记忆分析通道解析失败（DeepSeek）、主对话可见性停摆 + 重放风暴、Procedure 绑定后续调用与被拒调用观测丢失（r14 后续）。
4. 下一次正式运行条件：三项修复合入 main 后，DeepSeek + 32000 窗口 override，从 T1 全新 userdata 跑满 24 轮。

## 尝试 3：deepseek-v4-pro + 窗口 override 32000（13:15 起，`primary-ui-xmqudtzt` → 修复后同 userdata 续跑 `primary-ui-hv9k7ncq`）

前提：三项修复已合入 main（`b3682fe1`），预检全绿，`model_context_resolved window=32000 source=global`。

| 轮 | 结果 | 发现 |
|---|---|---|
| T1–T6 | 全部 COMPLETED；T1/T2 两条事实均落记忆头（分析通道修复生效）；T6 读 40 KB 文件成功，终态观察按新规则省略超大工具体 | ✅ |
| T7 | 前台驱动 4 次 `primary_history_transcript_mismatch` 后停摆 | 历史读取器第三处严格比较未随省略标记改造（`primary_history.py:420`）→ 修复 `933df61e` + 回归测试 `edc64dad`；重启同 userdata 后排队的 T7 自动恢复 |
| T7（恢复后） | Run FAILED（max turns） | 模型用 `task_scope_search` 搜任务（0 候选）与空参数 `task_scope_update` 循环；发现面未告诉模型"当前已有活动任务、goal.set 需要哪些参数"（事件 B，子代理处理） |
| T8 | Run FAILED `sdk_task_execution_route_authority_missing` | 模型选 `direct_standalone` 路由后激活并调用 `run_shell`，Host 把授权缺失升级为整 Run 失败而非工具级拒绝（事件 A，子代理处理） |
| T9 | COMPLETED | — |
| T10 | Run FAILED | 7 次格式正确的 `task_scope_update(decision.record)` 全被 `task_scope_update_nothing_to_close` 拒绝（`require_dirty` 把纯对话决定判为无可关闭内容，事件 C）；随后一次 DeepSeek 42 字节畸形参数（`expecting_delimiter`）触发 `ProviderProtocolError` 直接判 Run 失败（事件 D）。C/D 交另一子代理裁决与修复 |

判断：本次运行继续跑完以收集 T11–T24 的证据与缺陷，但 A6-5（视图超限）与 A6-2 的第二个大结果已注定不可达，正式判定需在事件 A–D 修复合入后再跑一次完整 24 轮。
