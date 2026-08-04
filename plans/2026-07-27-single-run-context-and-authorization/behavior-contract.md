# 行为变更契约

## source_request

| request | exact text | sha256 |
|---|---|---|
| implementation approval | `好的，请你直接开始做` | `7a5832f9343851a817ce15f1096246bdf302f4f176890c1a818050237d48624f` |
| incident scope addition | `一起加进去进行修改` | `c9bc6e108d4f8c049c3d56b74a380dc71db28851d102c469c951ed9015906320` |

## behavior_contract

### BC-IT-1 — ordinary turn authority

- before：无 active skill 的普通 turn 可被 IntentTriage short-circuit 或前置
  clarification 截断，`root_run_id` 为空；manual action 还会经过通用 plan admission。
- after：除 durable recovery/cancel/明确产品协议外，普通 turn 在 Context 组装后直接
  创建/恢复唯一根 Run；闲聊与澄清由主 Run 完成；不再有通用 plan admission。
- retained：相同 `session_id+request_id+turn_id` 恢复同一 Run；active skill scope、
  cancel、durable decision、逐工具权限与危险操作确认保留。
- deleted：IntentTriage/IntentCard、旧 route/plan 命令、`chat_v2_intent`、
  `chat_v2_contradiction`、plan-read-only 临时窗口。
- scope：Text/Voice 共用的 ProductVenue production ingress。

### BC-IT-2 — permission timing

- before：manual mode 可先批准模型生成的宽 action-category plan，再由 prepared tool
  做 exact authorization；auto mode 跳过 root admission。
- after：manual/auto 都直接启动根 Run；只有出现真实 prepared action 时才按 exact
  tool/resource/effect 打开 durable decision 或 auto policy grant。
- retained：TaskGrant、exact grant、nonce/version/expiry、deny-by-default、
  user deny、provider unknown outcome。
- deleted：由 `requires_action_plan` 推测副作用并预授权的 root-plan TaskGrant。
- scope：所有 ReAct prepared tool，不改变独立 workflow 自身的业务 admission。

### BC-IT-3 — terminal error visibility

- before：根 Run error 只发 WebSocket；后续 Context 仅从 Session transcript 读取时
  看不到它。
- after：根 Run 失败/取消以 canonical terminal event 投影到 Session；即时下一 turn
  通过同步投影或 execution read-through 一定能看到同一 event，最终 transcript 幂等。
- retained：child error 不刷普通会话；原始 traceback/provider 参数不进聊天；
  unknown-after-handoff 不自动重发。
- scope：root terminal events。

### BC-IT-4 — capability ID compatibility

- before：`tool_describe` 只接受 `source:name` exact ID。
- after：exact ID 仍优先；裸 name 仅在当前 deferred catalog 唯一时规范化；
  0/多匹配拒绝。
- retained：nonce/revision/activate 与 canonical identity 严格绑定。
- scope：deferred capability describe/activate。

### BC-IT-5 — authorized resource invariant

- before：部分 write/shell/dangerous specs 可 prepare 出空 selector，并在 permission
  decision 阶段击穿 Driver。
- after：所有进入 prepared ReAct 且需要授权的 spec 都有 Host-bound selector；
  合同缺失成为 `authorization_scope_missing` 工具失败，真实授权拒绝仍 fail closed。
- scope：ToolRegistry production catalog 与 ReAct Driver。

