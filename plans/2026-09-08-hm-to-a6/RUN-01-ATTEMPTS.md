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
