# 记录：语音中继 tokenseller 协议终止错误的修复（指针页）

日期：2026-10-30 深夜。中继仓库：`/Users/taiwan/PROJECTS/OneOneTalk/tokenSeller`（GitHub `OneOneTalk-AI/tokenSeller`）。详细记录在中继仓库 `docs/incidents/20261030-native-orphan-terminal/`（`README.md` 为事故记录，`handoff-zh.md` 为给 SDK/AIPhone 的回执）。

## 结论

1. **五条重连/回灌终止 + AIPhone 41～46 帧终止：中继自己拒绝的，不是上游拒绝。** 文字 `conversation.item.create` 回灌之后再送音频，上游（Qwen）会给一条从未宣告过的响应发四条无 `response_id` 的完成通知和一条空 id、无 usage 的 `response.done`。中继编解码器要求 `response.id` 非空，把它判成协议错误并按 `upstream_protocol_error` 致命终止。已在正式域名用脚本复现（带回灌 11 帧后必终止；不带回灌对照完成三轮），见中继仓库里的 `prod-before-*-safe-wire.json`。
2. **工具续答那一条（corr_372…）：上游行为。** 上游在前一条响应未结束时再发 `response.created`（复用 item，前缀永远没有终态）。中继两槽内核和 Service SDK 0.3.20 会话层都不允许两条未结束未取消的响应。中继这次不改语义，只补关联字段；彻底解决要按 chinzy 10-30 的"响应替换"提案做能力协商。
3. 重复 `session.update` / 只含 instructions 的部分更新：不是同一根因（客户端方向拒绝，`message_class=invalid_open`）。

## 改了什么

- 空 id / 未知 id 且无 usage 的 `response.done`：丢弃并记诊断 `orphan_terminal`，会话继续；带 usage 的未知终态仍致命（未计费工作，宁可失败）。
- 会话期 `tokenseller.error` 现在带 `related_event_id` = 触发帧的 `event_id`（冻结合同唯一关联槽，SDK 0.3.20 不用改）；服务端新增 `realtime_native_session_failed` 日志，带 correlation、relaySessionId、方向、code、触发帧 event/response/item/call id、generation、在役 response、内核快照。
- 会话期失败不再给共享 qwen 凭据记失败（只有 unauthenticated / forbidden / rate_limited 才记）。
- 新增 15 条用例，先红后绿；Realtime 目录 5 个 suite 61 条全过；typecheck、build 通过。

## 状态

- **代码已推分支、部署交另一台电脑、待部署后复验。** 分支 `fix/realtime-orphan-terminal-20261030` 已推 origin；2026-10-31 用户在修复会话里直接要求推主分支，已快进合入 `main` 并推送（origin/main = origin/分支 = `cbace05`；修复本体 `bedd04c`，其后两个提交只改记录并随附冒烟用 PCM）。推送内容做过密钥字样扫描，干净。
- 本机未部署（没有 Vercel 登录与 Supabase 凭据）。另一台电脑按中继 README 第 7 节清单做：取码校验 → 候选 `--skip-domain` → 候选上 `context`/`plain` 两场景真实语音冒烟 → promote → 正式域名再跑两场景 → 补 README 第 8 节。回滚目标 dpl_6nM9SBTFUqq689tyvPQ4EZG4NQmA。
- 部署后复验：重跑 A09/A10/A11 三个场景，预期四条无 `response_id` 的 `*.done` 之后不再出现 `tokenseller.error`，正常走到 `response.done completed`；A10-voice-task（工具续答）仍会终止，但错误会带 `related_event_id`，留到协议协商后再验。
