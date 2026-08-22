# Official Memory integration — Gate final r4 results

> 执行日期：2026-08-22  
> 被测提交：simple_harness `4e797ccd`；Harness SDK `fbb156f`；Memory SDK `3d4247b`  
> 状态：21/21 required 场景已执行；r7 独立审计 `FAIL`，不得作为发布 receipt；审计指出的证据与状态
> 缺口已在继任 Gate 输入前补齐
> 本地 promotion：`v0.3.0` → Harness `fbb156f`；`v0.4.0` → Memory `3d4247b`；未 push/upload

## 结果摘要

| 范围 | 结果 | 主要证据 |
|---|---|---|
| Harness SDK | `1379 passed, 2 skipped` | r4 Gate 的 SDK-C/T/I/S/M/R exec receipts |
| Memory SDK | 默认 full `200 passed, 7 skipped`；正式 candidate gate `205 passed, 2 skipped` | r4 默认全量与 promotion candidate receipts |
| simple_harness 自动化 | 15 shards PASS、2 个实施前 known-red、0 unexpected；focused backend 83、frontend 18、typecheck PASS | r4 exec receipts 与 baseline state |
| 真实 UI | SH-M1～SH-M6、SH-SURFACE PASS | r2 exact originals + current-build shell/web/attachment + fresh fault attestation |

Promotion 后重新校验 Harness canonical `dist/BUILD_INFO.txt`/`SHA256SUMS` 与 wheel `cf629cee…`，
Memory `candidate-dist` 与 wheel `bfcd2506…`；Python 3.11/3.12/3.13 exact-wheel 联合安装矩阵 PASS，
Harness full `1379 passed, 2 skipped`、Memory 默认 full `200 passed, 7 skipped`，以及正式 candidate gate
`205 passed, 2 skipped` 再次通过。

真实 UI 使用设置页已配置的 DeepSeek；凭据未读取、未写入报告。SH-M2 真生成 PPT 并通过权限弹窗；
SH-M5 的 exact oracle 在隔离 profile 写入 `Max`、完整退出、重启后由新 Session 召回。SH-M6 的新鲜
recall timeout root 由 app 进程中的 `DESKPET_MEMORY_RECALL_FAULT=timeout` attestation 直接绑定，主 Turn
completed；record transient 在成功回复但 Memory 尚未提交时退出，清除 fault 后 startup recovery 唯一提交，
随后新 Session 回答“晚饭后”。SH-SURFACE 覆盖设置、技能、产物、Facts、ContextTrace 与权限边界取消；
当前构建已真实打开 macOS `Open` 对话框并以 Esc 安全取消，未选择/上传文件且无附件残留。

最终独立审计改为引用 r2 中逐字符合冻结 oracle 的原始证据：SH-M1/SH-M5 均使用 `Max`，SH-M2
使用标题 `SDK Memory 验收`，SH-M3 两个独立 root 都显示最终一句话偏好总结。原先 `Aurora-R4` / `R4
Memory 验收` 仅是隔离 canary，不再作为 exact-oracle 主证据。SH-SURFACE 的 file 使用 r2 对应原始截图；
旧 attachment 文件名与画面不符，已排除并以当前构建新鲜的 picker-open/cancel-safe 两张截图替代。shell/web
因旧截图停留在 file 页面，于最终 HEAD 上用两个新 root 重新真测：`pwd` exit code
0 并显示工作区路径，`web_search` 返回 Python 官方文档标题与 `docs.python.org` 链接；两者均经可见权限弹窗
“允许一次”完成，原始截图和脱敏 backend 日志保存在 ignored `.local-test-evidence/`。

原始截图、日志、数据库、进程 attestation 与 Gate ledger 位于 ignored
`.local-test-evidence/2026-08-22/gate-final-r2/`、`gate-final-r7-attachment/`、`gate-final-r8-sh-m6/` 及其显式
chain-of-custody successor；继任 Gate 从原始文件直接导入并逐文件记录来源路径/原始 mtime/hash。本文件不复制
原始内容，只保留结论与相对索引。

## 幂等性审查

| 命中场景 | 重复副作用 / 已处理判断 / 持久化 / 失败重试 | 回归证据 | 结论 |
|---|---|---|---|
| Context prepare + source ref | 同 request/continuation 复用 durable claim/hash；改 payload/ref conflict；claim/lease 持久化；owner 失败可 takeover | `test_future_consumer_rich_context_is_frozen_across_replay_and_restart`、`test_continuation_preparation_crash_replays_the_claimed_ref` | 低风险，无补项 |
| committed-turn outbox | deployment+turn+payload hash 去重；receipt/outbox 持久化；transient 不 ack、可 backoff/restart 重试 | `test_after_record_before_ack_reopens_and_replays_idempotently`、SH-M6 | 低风险，无补项 |
| explicit fact/forget/share | source event + canonical payload/action receipt；重放同 ID/结果，冲突拒绝；tombstone 防复活 | `test_explicit_forget_action_is_durable_idempotent_and_owned`、`test_public_share_is_idempotent_owned_and_forget_cascades`、Facts UI 真测 | 低风险，无补项 |
| provider projection cursor | durable receipt 后才推进 cursor；callback/session-write 故障保留重试且不重复 measurement | `test_callback_failure_keeps_receipt_for_idempotent_retry`、`test_sp3_fault_after_session_write_replays_without_duplicate_measurement`、SH-SURFACE stop | 低风险，无补项 |
| v3→v4 migration | backup-first、manifest coverage/hash、临时库验证、原子替换；fault rollback；重复 import 命中 receipt | execution/Memory migration fault suites（SDK-M01） | 低风险，无补项 |

## 范围声明

AIPhone、K6/AgentOS、NovelTagSystem 没有代码修改或产品测试。Harness/Memory SDK 已保留 future-consumer
composition 与 authorized `share_fact` 接口；本结果只证明 simple_harness 产品接入完成。
