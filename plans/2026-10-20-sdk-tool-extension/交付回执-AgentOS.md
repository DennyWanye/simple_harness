# 交付回执：Simple Harness SDK 正式自定义工具接入能力（给 AgentOS 会话）

日期：2026-10-20。对应交接《为 AgentOS 开放 Simple Harness SDK 的正式自定义工具接入能力》（基线 opt.195）。
本回执只在私有渠道流转，不推送公开 GitHub。

## 1. 固定制品

| 项目 | 值 |
|---|---|
| 版本 | `0.13.0.dev20260925+opt.200` |
| SDK 源码提交 | `25ce272e1062724233a94e03a56d7acbaf2d3e55`（私有 `simple_harness`，子目录 `sdk/simple-harness-sdk/`） |
| 宿主钉版提交 | `1e3a42915` |
| wheel | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.200-py3-none-any.whl` |
| wheel SHA-256 | `f5e03d34df04060ddaedffb9a516549cfa1c2a4d85399ac55c8a37ecde28f2a5` |
| 候选清单 | 同目录 `simple_harness_sdk-0.13.0.dev20260925+opt.200.candidate-manifest.json`，SHA-256 `795795f8a868247deaf82ffe5d697f961ad9976943031f519cb186f56873a37e` |
| Python | `>=3.11`；纯 Python wheel（`py3-none-any`） |
| 必需依赖 | `deepseek-recipe==0.1.1`（非 Windows）、`httpx>=0.27,<1`、`pytest>=8.3,<9` |
| 验收门 | 发版门 167 条通过，wheel 内清单为 `VALIDATED`；独立核验回执 `.local-test-evidence/2026-10-20/taskgraph-gate/review-232415.json` |

opt.195 之后的版本：opt.196 修 SQLite 3.50/3.51 开库即倒；opt.197 只读工具注册口；opt.198 Linux 隔离区；opt.199 应用连接器（有副作用的操作）；opt.200 修参数引用产物的操作在建动作时被拒（真实模型一局发现）。opt.195 的 wheel 不覆盖，旧号不再用。

## 2. 公开导入与宿主构造

只用公开模块（`agent_orchestrator/public_api.py` 的清单），不导入 `tests/`、`testing/`（`agent_orchestrator.host.offline` 是公开的脚本模型转出）。

```python
from agent_orchestrator.host import (
    DomainTool, ToolCallContext, HostSettings, ModelConfig, OrchestrationHost, Principal,
)
from agent_orchestrator.runtime.connectors import (
    OperationSpec, Receipt, ConnectorRejected, ConnectorTransportError, params_hash,
)

# 只读工具：同步函数（线程里跑）或 async def（宿主事件循环上跑，里面不能阻塞）
async def nas_search(arguments, context: ToolCallContext):
    # context 由 SDK 填：mission_id、attempt_id、call_id、principal_id、tenant_id；模型参数里的同名字段不算数
    return {"hits": [...]}            # 必须是规范 JSON 对象，≤1 MB

search = DomainTool(
    name="nas_search",
    schema={"type": "object", "description": "在家庭 NAS 里按关键词找文件",
            "properties": {"q": {"type": "string"}}, "required": ["q"],
            "additionalProperties": False},
    invoke=nas_search,
    timeout_seconds=30,
)

# 有副作用的操作：连接器
class Photos:
    name = "photos"                       # 要求里写 action:photos.<操作>:<目标>
    supports_idempotency = True           # 同一 idempotency_key 只执行一次
    supports_reconciliation = True        # 能按 idempotency_key 查
    lookup_authority = "authoritative"    # L2 以上必须是它
    lookup_fenced = True                  # 查询为空后这个键再来的执行一律拒绝
    operations = {
        "move": OperationSpec("move", "L2", ("to",), description="把照片移到 to"),
        "delete": OperationSpec("delete", "L2", (), always_needs_approval=True),
    }
    def normalize_target(self, target: str) -> str: ...
    async def execute(self, operation, target, params, *, idempotency_key) -> Receipt: ...
    async def lookup(self, idempotency_key) -> Receipt | None: ...

host = await OrchestrationHost.open(
    root,                                  # 单目录单所有者，有实例锁
    principal=Principal("family-admin", "管理员"),
    model=ModelConfig(base_url=..., model="deepseek-flash", api_key=...),
    settings=HostSettings(max_action_level="L2"),   # 注册连接器时必填
    auto_mode=True,                        # 或每轮现读的函数
    tools=[search],
    connectors=[Photos()],
)
```

建任务：

```python
host.create_mission({
    "goal": "把 /photos/a.jpg 移到归档",
    "success_criteria": [
        "file:NOTES.md",
        {"statement": "action:photos.move:/photos/a.jpg", "params": {"to": "/archive/a.jpg"}},
    ],
    "idempotency_key": "move-1",
})
```

完整接入说明：`sdk/simple-harness-sdk/docs/orchestration-host.md`（4a 节只读工具，4b 节连接器）。

## 3. 授权、等待、批准、拒绝、取消、对账的调用顺序

**两条通道，各管一类：**

- 只读工具（`DomainTool`）：模型在执行步骤里直接调。没有审批。应用在 `invoke` 里按 `context` 做自己的授权；不允许就抛异常，记为"失败"交回模型。
- 有副作用的操作（连接器）：模型不直接调。副作用必须在建任务时写成 `action:` 要求，参数一起写下；任务中途临时起意的副作用做不了（独立核验裁决的代价，文档写明）。有副作用的 `DomainTool` 在构造时被拒。

**一个操作的完整顺序：**

1. 建任务：`action:` 要求 + 参数。SDK 在门口查：连接器已注册、操作存在、风险等级不超过 `max_action_level`、必填参数齐全、参数是规范 JSON、不像密钥。参数进要求版本哈希。
2. 确认完成要求：自动模式下 SDK 代签（每条 `action:` 一个效果，记 `HOST_AUTO_PERMISSION`）；手动模式下人调 `approve_operation_completion_spec(body)`。
3. 内容步骤做完、审阅通过后，系统按已批准的效果准备申请单（参数原样取自要求，`{"$artifact": "路径"}` 绑成 `{artifact_path, content_hash, size, storage_uri}`），独立审阅员审申请单。
4. 审批：L2/L3 生成审批请求，`approvals(mission_id)` 可读（`kind="action"`、`person_required`）。
   - 自动模式：主机代批（`HOST_AUTO_PERMISSION`，事件执行者 `system`，一次决定即批准）。
   - 标了 `always_needs_approval` 的操作、手动模式：人调 `decide(request_id, "approve")` 或 `decide(request_id, "reject", reason=...)`。主机代批对它们会被拒。
   - 审批有有效期；过期、任务取消后，旧审批不能再唤醒执行。
5. 执行：先写"已交接"，再调 `execute`（带超时）。
   - 返回合格 `Receipt` → 成功。
   - 抛 `ConnectorRejected` → 失败（明确没生效）。
   - 其它异常、超时、回执不合格（不是 `Receipt`、不是规范 JSON、超过 64 KB）→ "不明"。超时的调用在它真正结束前仍算在途。
   - 异常文字截到 500 字；像密钥就只记异常类型名。
6. 不明时对账：SDK 调 `lookup(idempotency_key)`。
   - 找到回执 → 按回执结案。
   - 权威且封口的连接器查询为空 → 证明没生效 → 系统按已批准效果换新键重做（不同键重交；迟到的原请求被封口拒，不会生效两次）。
   - 其它情况 → 保持"不明"，等人：`resolve_unknown(action_key, outcome, basis=...)`。SDK 不盲目重试。
7. 结果审阅员按回执判定效果是否达成，任务才能完成。
8. 取消任务：`cancel(mission_id)`。没交接的动作取消；已交接未决的动作在任务快照里列为"未结清"，之后对账结果会补发通知。

**一个授权事实源**：审批只在 SDK 里。应用不要另弹窗；要人把关就把操作标成 `always_needs_approval` 或用手动模式，在应用自己的界面里调 `decide`。

**SDK 不承诺任意外部系统的物理恰好一次**：只在连接器满足"按键去重 + 权威可查 + 封口"时证明"没生效"；其余情况留"不明"给人。

## 4. 事件、游标、去重与结构化结果

读法：`events(mission_id, after_seq=上次的 through_seq, limit=)`。事件按 `seq` 排序。`on_write` 只是"有新写入"的提醒，可能重复、可能合并，内容一律从游标续读。任务结束时 `notify=` 回调至少一次。没有逐 token 流的公开合同。

只读工具事件（同一调用共用 `call_key` = `context.call_id`，用它去重；同一调用多条结束事件时以 `seq` 最大的为准）：

| 事件 | 载荷要点 |
|---|---|
| `ExternalToolCallStarted` | `tool`、`call_key`、`argument_keys` |
| `ExternalToolCallSucceeded` | `result`（32 KB 内原样；更大时 `truncated: true` 加预览）、`duration_ms` |
| `ExternalToolCallFailed` | `error_code`（`tool_failed` / `tool_result_invalid` / `tool_timeout` / `tool_busy` / `tool_interrupted`）、`error_type` |
| `ExternalToolCallUnknown` | 步骤被取消时调用仍在进行（`tool_cancelled`） |
| `ToolCallRejected` | 没授权、参数不合格、预算用完、工具已删 |

结构化结果示例（`ExternalToolCallSucceeded.payload.result`）：

```json
{"hits": [{"path": "/photos/2026/a.jpg", "taken_at": "2026-05-01", "thumb": "nas://t/123"}]}
```

操作事件（按 `action_key` 关联；审批按 `request_id`）：见第 6a 节真实模型这一局的事件清单。审批事件 `ApprovalGranted` 的 `approval_source` 是 `HUMAN` 或 `HOST_AUTO_PERMISSION`。

外部结果是"不可信外部内容"：工具结果交给模型时带来源，不能授予权限或改变状态；应用可以在 `invoke` 返回前接自己的输出检查。

## 5. 工具集合、身份与升级规则；可信边界

- 工具名不能与内置工具、技能工具、审阅员工具重名；重复注册、非 `DomainTool`、schema 不是对象或没写顶层 `description`、超时不是正数，构造时就拒。
- 连接器名不能是 `file_publish`，不能含 `.` 或 `:`；只读操作、不能去重或查询的、没写 `lookup_authority` 的，构造时就拒。`kind="event"` 的操作随注册启用。
- 同一数据目录换了工具集合：宿主照常启动；在途任务再调已删工具被拒，记 `ToolCallRejected`，任务继续（已测试）。
- 可信边界：宿主固定一个 `principal` 和 `tenant_id`、一个数据目录、一个所有者。`ToolCallContext` 里的身份来自宿主，不来自模型参数。**没有多用户合同**：每个成员一个宿主（各自目录），或由应用在工具与连接器里按自己的会话身份做隔离。

## 6. 已执行的验证

**脚本模型（不联网）**

- 只读工具：执行器 12 条、宿主 6 条及核验后补的用例；`tools_readonly.py`、`tools_async.py` 例子。
- 连接器：`tests/orchestrator/product_world/test_generic_connector.py` 6 条，覆盖以下五种情况：
  - 自动模式代批执行一次；
  - "总要人批"等人；
  - 回执丢失后用封口证明、换键只生效一次；
  - async 查询走同一条路；
  - 不封口时保持"不明"、不重交。
- 宿主注册检查、回执与报错文字检查 9 条；改到的相关测试约 450 条通过。
- 仓库外：只装 opt.197 wheel 的空项目跑三个例子退出码 0（`~/sh-ext-tools`）；opt.199 wheel 在 Linux 虚拟机（Ubuntu 24.04 ARM64、Python 3.12）跑 `tools_effect.py`，两个任务 COMPLETED。

**真实模型**：PASS，见第 6a 节（真实 DeepSeek，仓库外只装 opt.200 wheel）。

**真实设备**：NOT_RUN（没有接 AgentOS 的 NAS、桌面 Relay、MCP）。

**独立核验**：只读工具口两轮（第一轮 3 条必须处理，已修）；连接器两轮（第一轮 1 条阻断：async 查询的封口连接器自动重做走不通，已修；6 条方案偏差判可接受）。

## 6a. 真实模型一局

**opt.200 结果：PASS（2026-10-20 深夜～10-21）**。仓库外项目只装 opt.200 wheel，经本机转发（上游 chunfengapi.com，DeepSeek v4.1 flash），生产验收门开启，自动模式。任务：写三条省水办法到 NOTES.md，再经假"留言板"连接器发出（参数 `{"title": "省水三招", "body": {"$artifact": "NOTES.md"}}`，连接器的 `execute` 与 `lookup` 都是 async，声明权威且封口；第一次执行故意丢回执、请求不落地）。

- 任务 COMPLETED，停止原因 `verification_passed`；结清用量 490 462 token，尝试 1 次。
- 连接器日志：第 1 次执行回执丢失 → 查询该键为空（封口）→ 第 2 版动作换新键执行 → 发帖 1 条。留言板上只有一条，正文就是模型写的 NOTES.md（连接器按 `storage_uri` 读字节并核对了内容哈希）。
- 动作：`…:v1` FAILED（`proven_not_applied:rehandoff_refused:lookup_fenced_key`，交接 1 次）；`…:v2` SUCCEEDED（交接 1 次）。
- 事件（按出现顺序）：`OperationCompletionSpecApproved` 1、`OperationIntentSubmitted` 2、`ActionProposed` 2、`ApprovalRequested` 2、`OperationMaterialized` 2、`ApprovalGranted` 2（`approval_source=HOST_AUTO_PERMISSION`，执行者 `system` / `host-auto:demo-user`）、`ActionHandedOff` 2、`ActionOutcomeUnknown` 1、`ActionScopedReconciled` 1、`ActionNonapplicationFinalized` 1、`ActionSucceeded` 1、`OperationOutcomeReviewPrepared` 1、`OperationOutcomeAccepted` 1。
- 过程中规划器有几次没按格式写完（转发站线路当晚不稳），SDK 按格式重写或服务故障宽限处理，没有扣错次数。

这一局在 opt.199 上先发现了一个缺陷：参数引用产物的操作在建动作时永远被拒（建动作按文件发布的办法绑定参数）。已在 opt.200 修复，修复独立核验 PASS。

更早一次尝试（opt.199，10-20 晚）因线路故障没跑完：转发站 520、单次请求 90～220 秒；SDK 按服务故障宽限处理。

## 7. 未解决的条件

| 事项 | 状态 |
|---|---|
| Linux 执行器（K6） | opt.198 已有 bubblewrap 隔离区；只在 Ubuntu 24.04 ARM64 虚拟机实测，K6 真机 NOT_RUN |
| 模型 | 只支持 DeepSeek（官方端点或声明过的兼容转发）；Luna/Sol、视觉模型的认证计数与回退没有合同 |
| 多用户 / 多租户 | 没有合同；见第 5 节的两种做法 |
| 快速聊天、原 plan execute/skip/stop、逐 token 流 | 没有公开合同 |
| 动态增删工具与技能 | 只能重开宿主换集合；运行中热更新没有合同 |
| 个人/家庭记忆注入 | 没有记忆合同；正式接法是建任务时附资料（`create_mission_with_sources`）或只读工具按需取；资料可换版本、撤回 |
| 任务中途临时起意的副作用 | 做不了；副作用要在建任务时写成要求（含参数） |
| 大文件 / 二进制产物导出 | 没做：`artifact_read` 正文上限 256 KB，二进制不给正文 |
