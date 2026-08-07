# r4 作废说明

r4 在真机重跑过程中发现并修复了两个本次改版引入的缺陷、以及一个既存的 mac 环境缺陷，
生产代码已变更 → r4 的全部入账（脚本道 6 条 + 真机道 S01/S02/S03/S04/S06/S07/S08）
不再代表交付物状态，**整体作废**，改开 r5 全量重跑。r4 未 finalize，无 receipt。

## 作废触发的代码改动

1. **S05（改版引入）**：`tauri-app/src/components/SessionList.tsx`
   新建的会话永远不进侧栏列表（切走就回不去，只有重启才出现）。清单只在 mount 与
   「新会话诞生」两处刷新，而后端 `list_sessions_with_preview` 以「已有消息」为会话
   进清单的条件，诞生那一刻会话还是空的。修法：`chat_v2_final` / `chat_response`
   收尾时再刷一次（中间态 `tool_call`/`tool_result` 不刷，避免抖动）。
   回归测试：`SessionList.test.tsx`「一轮对话收尾后重新拉清单」。

2. **S18（改版引入面待定，见下）**：`tauri-app/src/code-panel/controlWs.ts` + `backend/main.py`
   `pending_root_request_id` 原先只在 `chat_v2_run_reserved`/`started` 里清除，而 Run
   预约之前就夭折的拒绝（`companion_identity_not_ready`、只读会话）走不到那里 → 该字段
   永远挂着 → `InputBar` 的 `shouldDefer` 恒真 → 此后每条消息只 push 到本地流、根本不
   send，永久停在「等待 Agent 读取…」。删除当前打开的会话后应用必然切到 default，
   default 正是被这样卡死的。修法：后端两条前置拒绝分支回传 `request_id`/`turn_id`；
   前端 `chat_v2_error` 认领并清除 pending（缺 request_id 时按会话兜底），并把挂起的
   deferred/waiting 消息落到 `failed`。
   回归测试：`ws.chat.test.ts` 两条（撤掉修复即红）。

3. **mac 既存缺陷（非改版引入，顺带修）**：`tauri-app/src-tauri/src/process_manager.rs`
   + `backend/deskpet/companion/control_ingress.py`
   后端自己读 `deskpet-relay` 钥匙串项调 `/v1/me`，而该项每次令牌刷新都被 Tauri 重写、
   ACL 随之重置成只信任写入方 → 后端解释器每次都要重新弹系统授权框，「始终允许」存不住
   （uv 装的 python 是 adhoc 签名，白名单本就不稳）。修法：改由 Tauri（该项创建者，读取
   免弹框）读出后经 `DESKPET_RELAY_ACCESS_TOKEN` env 交给后端，后端优先读 env、钥匙串
   仅作回落。回归测试：`test_window_control_credentials.py` 两条（env 优先 / 空白回落）。

## r4 期间已取得、可作为 r5 参考的结论（不作为入账证据）

- S04 特权链在真机上闭环：控制连接以 `window_label=main` + `scope=companion_action`
  建立、`window_scope_denied` 命中 0、devtools 实调 `get_window_control_credential`
  返回 `windowLabel:"main", scope:"companion_action"` 的已签发凭据。
- 日志中两处 402 Payment Required 均为反思后台链路的 `sf-glm-5.2` 无额度，
  kimi-k3 主链路全程 200。
- 证据文件保留在 artifacts/（r4-S02-manual.txt / r4-S04-manual.txt / r4-S05-manual.txt /
  r4-S18-manual.txt 及各截图），供 r5 对照与审计追溯。
