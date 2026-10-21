# 交付回执：AgentOS 迁移 8 块（SDK opt.201 → opt.213）

日期：2026-10-21。对应 AgentOS《迁移交接》8 块要求与 AIPhone 手机隔离区阻断。接入说明统一在 `sdk/simple-harness-sdk/docs/orchestration-host.md`（下面用 §号引用）。

## 1. 最终候选身份

| 项目 | 值 |
|---|---|
| 版本 | `0.13.0.dev20260925+opt.213` |
| SDK 源码提交 | `ce0e91ef5d8b77a65f2641f8630999e91f7d463b` |
| Host 钉版提交 | `5efc61c8fa1397f4b34f5cb2ea2228cbe2de532c` |
| wheel | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.213-py3-none-any.whl` |
| wheel SHA-256 | `4718a39a94a8a596ca1f44b3f2edfe2350ed1e796fff74a0b34f1613ee5a1f6d` |
| 候选清单 | 同目录 `...opt.213.candidate-manifest.json`，SHA-256 `1ba300a36c7d4aa67e6b6ebdf588c8da0031ed08053b3cb82cfdd87ea1c26ca3` |
| Python / 依赖 | `>=3.11`；已测 3.12、3.13 |
| 验收门 | 每版发版门 167 条通过、清单 `VALIDATED`；每块独立核验回执在 `.local-test-evidence/2026-10-21/taskgraph-gate/` |

## 2. 八块各做了什么、怎么接

| 块 | 版本 | 做了什么 | 接入 |
|---|---|---|---|
| 0 手机隔离区 | opt.201 | 两层 bwrap（先用户命名空间、再其余），no_new_privs，bwrap≥0.8，启动检查 9 项，无 pidfd 的收尾 | 《交付回执-AIPhone-opt201.md》；手机实测归 AIPhone |
| 1 中途决定动作 | opt.202 | 执行步骤的模型在结果块里写 `action_request` 提议动作；连接器 `prepare` 整理参数→冻结→批准（名单内自动代批、名单外一律人批）→执行→结果交回同一步续做 | §4c；`allowed_actions`、`host.step_actions()`、`approvals()`/`decide()` |
| 2 动作兜底 | opt.202 | 动作编号贯穿提议/审批/执行/回执/对账；重复提交不重复执行；`delivery="at_most_once"` 只交接一次、结果不明等人 `resolve_unknown`；上限跨尝试跨重启累计 | §4b/§4c；`HostSettings.max_step_actions_per_mission`、`connector_timeout_seconds` |
| 3 接 Luna/Sol | opt.203 | 通用 OpenAI 协议模型；非 DeepSeek 按字节上界计数（可多算不少算）；`fallback_model` 首选失败换备用一次、同运行黏住；台账 `model-calls.jsonl` | §2a；`ModelConfig(base_url, model, api_key, thinking, fallback_model)`。**Luna 账号 404，NOT_RUN** |
| 4 快速聊天 | opt.204 | `host.chat()` 一句一回合，不进编排；单独聊天池；一个会话一个 Agent；只读工具；月账预留后按真实用量结清；取消、同键幂等、台账 | §2b；`chat` / `chat_cancel` / `chat_history`、`HostSettings.chat_*` |
| 5 逐字输出 | opt.204 | 提供方正文片段经 `OutputStream` 按运行号分发；事件 v1（piece/end/error，generation，seq）；片段不落盘，终稿以回执与产物为准 | §2c；`chat(on_delta=)`、`host.output_stream.subscribe()` |
| 6 看计划、执行某步、跳过、停止 | opt.205 | `host.plan()`；`takeover(task_id, "skip"/"run_now"/"stop"/"retry_with_note", expected_version=, command_id=)`；跳过不算成功、规划器看到接管记录；重复点击幂等、过期决定被拒 | §4d |
| 7 子 Agent、多成员、完整结果 | opt.205 | 子步骤即子 Agent，工具只缩不放，预算挂任务账；一人一个宿主目录；换工具集重开宿主在途待批不丢；超限工具结果回读修通（预览 + `session_history_read` 分页） | §4e |
| 8 Linux x86_64 / NixOS | opt.205 | x86_64 在 Lima qemu 虚拟机实测（启动检查 + 一局任务）；NixOS 无实机，只写了 bwrap/AppArmor 要求 | §3a |
| 真实模型局修出的 | opt.206～210 | 审阅员说明写清步骤先后的读法；驳回原因随 `action_results.decision_note` 交给模型；"连续没有新知识"计数在开着的一轮有新验收即归零；线路出错后的用量未知预留只等 300 秒且不分回合种类（审阅员/规划器回合也算）；L2/L3 都一人批 | §4b/§4c |

## 3. 真实模型三局（MEETRAN Sol，仓库外只装轮子）

三局全部通过：第 1 局搜索后中途归档（opt.210，484 秒）、第 2 局快速聊天 + 逐字输出（opt.205）、第 3 局先驳回再批准的审批循环（opt.207，242 秒）。过程与修出的问题见 `plans/2026-10-21-agentos-aiphone/真实模型三局-记录.md`。脚本与说明在 `~/sh-ext-accept5/README.md`（不进仓库，可按需交付副本）。

## 4. 已知限制与留项

- Luna（gpt-5.6-luna）账号 404，未验。
- NixOS 没有实机。
- `run_now` 在"等待满 300 秒"的步骤前不一定排最前（留项）。
- 目标已有做法时，"还能提几个新做法"按没做法的目标算是 0，Sol 规划器看到后反复问人而不是改做法（模型判断；可考虑在请求包里把"改做法"的路写得更明白，留项）。
- `host.run_once()` 跑到空闲才返回；收敛作业每 60 秒唤醒时算不上空闲，调用方要用 `drain(timeout=)` 定时回来（接入说明已有）。
- 宿主以外的部署入口没放行 `session_history_read`（留项）。
- 做法提案额度按任务里任何真人回答给每个目标各加一次（可改为同主题，留项）。
- AIPhone 手机实测、三个手机工具接入、语音并行归 AIPhone。

## 5. 全量回归（2026-10-21，唯一一次授权）

| 套 | 用例 | 结果 |
|---|---|---|
| 前端 | 724 | 全过 |
| SDK（逐文件） | 7737 | 14 失败 + 1 文件看门狗误判；除基线已红的 1 条外，全是测试过期 / 本次新功能的钉死值变化，已更新；真正改代码的一处是"聊天池不该进任务池清单 / 技能目录 / 验收读方"（opt.211～213）；卡住文件单独重跑 23 项全过 |
| Host | 5140 | 7 失败：1 条旧真实线路测试引用已删函数、1 条源码结构锚点过期、2 条按用户 10-20 自动代签决定反过来、2 条工具边界加回读工具、1 条原生池清单；全部处理后 7 条所在 5 个文件 22 项全过（opt.213） |

归类与证据：`plans/2026-10-21-agentos-aiphone/全量回归-记录.md`、`.local-test-evidence/2026-10-21/full-regression*/`。
回归后补发 opt.211～213（任务池清单 / 原生平面状态 / 技能目录与验收读方不含聊天池；依赖锁哈希），每版独立核验 PASS。
