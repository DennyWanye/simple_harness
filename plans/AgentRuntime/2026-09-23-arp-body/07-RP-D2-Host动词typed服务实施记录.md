# ARP-EXEC-1.1.1 主体施工 RP-D2：Host 动词 typed 服务（`api/runtime_plane.py`）

日期：2026-09-23。分支 `arp-1.1.1`（worktree `simple_harness-arp`）。规格依据：HOST-DTOS §1–§7、`contracts/host-verbs.json`（23 个动词的请求/响应类型与 view_revision 口径）、codec 中 HostRequest/HostResponse 的语义校验、INTERFACES 的 owner 分工（写入由原 exec/registry owner 承担，Host handler 只做解码与固定 caller 委托）。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `src/simple_harness/api/__init__.py`、`api/runtime_plane.py`（新） | `RuntimePlaneService(runtime)`：`handle(HostRequest, caller) -> HostResponse`（永不抛出，失败为 Error DTO）；typed 方法 `context_summary / context_history / context_manifest / settings_get / policy_get / policy_submit / settings_update / command_receipt_get / history_search / history_read / catalogue_page / skill_details / skill_install`，会话销毁/恢复/重建与 Skill 生命周期直接委托 `runtime.arp.sessions` / `runtime.arp.lifecycle`；`error_json` 把目录 retry 折叠到 DTO 枚举 |
| `sql/execution_additive_v1_1_1.sql` | 新表 `arp_host_commands`（command_id 主键、verb、subject_id、command_hash、body_json、body_hash；禁改禁删） |
| `store.py` | `read_host_command` / `put_host_command_locked`（首写胜出，同号异 hash → `EXPECTED_REVISION_MISMATCH`） |
| `ports.py` | `ArpPorts.artifacts`：Host 认证上传链的 artifact 读口（`Pin -> bytes`），用于 `payload_ref` 与 Skill 安装包；缺省 None → `SOURCE_UNAVAILABLE` |
| `context/composer.py` | 装填改用会话**已采用**的 policy 对象（`latest_adoption` → `read_policy_object`），manifest 的 `effective_context_policy_ref` / `authority_refs` / read_set.policy 随之取自采用的 policy；已冻结召回的重放沿召回请求中固定的 policy_ref / adoption_revision，不受之后采用影响；访问句柄 TTL 也取已采用 policy |
| `runtime.py` | 新增 `policy_for(session) -> (采用行, policy 对象行)`（hash 校验，不一致 → `POLICY_CONFLICT`）；`search_for` 的 cursor TTL 改用已采用 policy |
| `retriever.py` | 访问句柄 TTL、检索上限（limits）与回执 `policy_ref` 改用已采用 policy，而非 profile 冻结的初始 policy |
| `tests/agents/arp/arp_fixture.py` | `build(..., artifacts=)` |
| `tests/agents/arp/test_arp_runtime_plane.py`（新，5 项，含 §4 五条回归断言） | 见 §3 |

## 2. 实现决定

1. **信封规则**：未知 verb 先按名拒绝（`UNSUPPORTED_HOST_VERB`），再走 `check("HostRequest")`（恰一个 payload、cursor/limit 逐值相同、settings update 的 expected_revision 配对由 codec 负责）；inline payload ≤256KiB；`payload_ref` 只经 `artifacts` 端口读取并核对 sha256，再按该动词的 request_type 做结构+语义校验。
2. **主体绑定**：会话动词的 `subject_id` 必须等于会话的 agent_id（否则 `SESSION_IDENTITY_MISMATCH`）；目录动词的 `subject_id` 必须等于绑定的 catalogue namespace（否则 `REF_OUTSIDE_SCOPE`）；payload 中没有任何字段被当作 caller / tenant / 路径。
3. **写命令的 revision 配对**：destroy / rebuild / destroy_resume = `Session.row_version`；skill trial/admit/suspend/resume/retire = 当前 activation `row_version`；install = payload 的 `expected_catalogue_revision`；settings update 由 codec 与 `expected_adoption_revision` 配对；`command_id` 与 `expected_revision` 缺失 → `MISSING_FIELD`。
4. **命令回执**：每个成功的写动词在 `arp_host_commands` 写一条（`command_hash = digest(verb, subject, payload, caller)`，body 含 outcome/result_ref/result_revision/observed_at_ms 与**当时的结果 DTO**）；同 command_id 同 hash 重放直接返回原回执与原结果（不重新执行、不返回今天的状态）；同号异 hash → `EXPECTED_REVISION_MISMATCH`；失败不写回执。`agent_command_receipt_get` 只读这张表。
5. **settings / policy**：`policy_submit` 把批准后的 Policy 存为同 policy_id 的下一 revision（`check_policy` 硬上限），`expected_policy_ref` 必须等于当前最新 revision 的 pin（首个 revision 允许 null），approval_ref 为 Host 认证命令派生的 authority pin，不自动采用；`settings_update` 要求会话 ACTIVE、`expected_adoption_revision` 与 `expected_effective_policy_ref` 同时等于最新采用、候选必须是已存在的 policy 对象，成功后追加采用并写 `AgentContextPolicyAdopted` 事件；composer 在下一次装填读取最新采用的 policy 对象，已冻结请求的重放不受影响（`_replay` 走冻结 manifest）。
6. **视图**：`ContextSettingsView` 的 view_revision = adoption_revision；`ContextSummaryView` 首次请求前 context_id / manifest_ref / retrieval_status / last_count_mode 皆 null，`configured_context_tokens` 与 `next_request_policy_ref` 来自有效采用；有请求时从冻结 manifest 与召回结果行投影 `RetrievalSummary`（candidate_count = 候选数、selected_count = manifest 已选块数，SKIPPED 时按 codec 规则清零）；`ContextManifestPage` 按 HEADER→SECTIONS→RECENT_GROUPS→RECALL→AUTHORITIES 分页，cursor 绑定 manifest_hash 前缀；`agent_context_history` 列出会话全部冻结 manifest 的头部，cursor 绑定 `RuntimeContextPrepared` eventseq 上界；`SessionView` / `SkillCatalogueItem` 由原 owner（`sessions.view`、`catalogue.summary`）投影，本层不手工拼。
7. **view_revision**：summary/history/manifest = 对应 `RuntimeContextPrepared` 的 original eventseq；settings = adoption_revision；policy_submit = policy revision；policy_get = pin revision；receipt_get = 原 result_revision；history_search = `receipt.index_upper_commit`；history_read = `journal_highwater`；destroy/rebuild/resume = row_version；目录列表 = registry_epoch；skill details = pin revision；skill 写 = activation row_version。会话动词的 `as_of_context_id` = 会话最新冻结 context。
8. **错误映射**：`ArpError` → 目录 stage / public_message；retry 只保留 DTO 枚举（`SAME_DESTROY_BACKOFF` → `BOUNDED_SAME_ID`，其余非枚举值 → `AFTER_RESOURCE_OR_AUTH`）；非 `ArpError` 异常只按类型名映射（AgentNotFound 等 → `SESSION_NOT_ACTIVE`，其余 → `STATE_COMBINATION_INVALID`），不透传异常文本。
9. 未新增错误码；`arp_host_commands` 是本地 SDK DDL 的追加（与 RP-C2 追加表同一做法，v11 尚未发布）。

## 3. 测试与回归

- 定向：`tests/agents/arp/test_arp_runtime_plane.py` 5 项——settings 往返（首次请求前 GET / summary → 提交新 policy → 配对错误拒绝 → 采用 → 陈旧更新 `POLICY_CONFLICT` 无回执 → 下一次请求的 manifest 用新 policy 与采用号 2 → history 头部 [1,2] → 断线后取回执 → 同命令重放返回原回执原结果 → 同号异 body `EXPECTED_REVISION_MISMATCH` → 他人 subject `SESSION_IDENTITY_MISMATCH` → policy_submit 同命令重放不产生第三个 revision → 他人 subject 读回执视为不存在 → 冒用创建时采用命令号 `{sid}:adopt:1` 换 body 被拒且采用表不变 → artifact-ref 更新的 expected_revision 不配对被拒、配对后采用号 3 → 采用 3 之后重放采用 2 的命令仍返回当时的采用 2 视图 → 更新后 history_search 回执的 policy_ref 为新 policy）；summary/manifest 分页与信封规则（两个 payload、cursor 不一致、未知动词、无 caller、写命令缺 command_id）+ 管理 search/read purpose；目录与 Skill 动词（subject 越界、kind 不符、artifact-ref 安装、install revision 配对、details、trial 陈旧配对、trial 重放同结果、admit/suspend/错误 action/resume/retire、回执读取）；destroy/resume/rebuild 走信封（row_version 配对、PURGING 视图与回执、destroy 身份下 rebuild 拒绝、清完后重放仍返回当时的 PURGING 视图、错误 destroy 身份的 resume 拒绝、PURGED 后 summary 可读而 history_read 拒绝）；HOST-DTOS 机器夹具的信封可被同一 codec 接受。
- ARP 全量：333 passed（328 + 5）。
- legacy `tests/agents`（排除 arp）：16 failed / 172 passed / 6 skipped，与基线一致；`tests/execution`：17 failed / 144 passed，与基线一致。

## 4. 独立核验

opus 5.5 子代理按"只报阻断级"审阅（1 轮），回 5 条阻断，全部修复并各补回归断言（见 §3 第一项末尾）：

| # | 发现 | 处置 |
|---|---|---|
| 1 | `policy_submit` 同命令重放时响应缺 `command_receipt`，而 `check("HostResponse")` 在 try 外，写命令重放会直接抛异常而非返回 Error DTO | `_replayed` 为 policy_submit 从 approval id 派生回执补齐；HostResponse 校验移入 try，失败折叠为 Error DTO |
| 2 | `agent_command_receipt_get` 不校验 subject，任何 subject 可按 command_id 读到他人回执 | `_command_receipt(command_id, subject_id=)` 他人 subject 视为不存在（`SOURCE_UNAVAILABLE`）；关联会话时再复核会话身份 |
| 3 | `settings_update` 命中已存在采用行时提前返回按**今天**状态拼的视图冒充当时结果；且崩溃窗口（采用已写、回执未写）内重试撞 revision 围栏 | 去掉提前返回；`read_adoption_by_command` 命中且 hash / session 相同 → 用该采用行构造当时视图（`_settings_view(session, adoption=...)`），否则 `EXPECTED_REVISION_MISMATCH`；destroy/resume/rebuild/install/trial/admit 同样在围栏前先查 owner 账本（`_owner_replayed`） |
| 4 | `payload_ref` 路径不做 `expected_revision` 与 `expected_adoption_revision` 配对，与 inline 不等价 | ref 分支读入 payload 后重跑 `check("HostRequest", {…payload, payload_ref: None})`，两种模式走同一校验 |
| 5 | settings 更新后 retriever / recall 恢复仍用 profile 冻结的初始 policy（TTL、limits、policy_ref），GET 显示的获准值与实际不一致 | `runtime.policy_for` 统一读已采用 policy；retriever、`search_for`、composer `_access` 改用之；冻结召回重放沿请求内固定的 policy_ref |

## 5. 未做 / 排入 RP-E

- Host 侧接线（`backend/deskpet/orchestration/{handlers,service,projection}.py` 等）与 UI 页面：本片只交付 SDK typed 服务与信封编解码。
- 事件推送（`{event_id,eventseq,event_type,subject_ref}`）与断线补读：由 Host 沿原 eventseq 实现。
- 旧归档的显式 legacy reader（v1 视图）：当前库无 v1 归档。
- `agent_context_history` 的分页按 manifest 头部实现（一页 ≤ limit 项），未做按字段跨 manifest 的深分页。
- skill suspend / resume / retire 没有 owner 侧按 command_id 的账本，崩溃窗口（activation 已改、回执未写）内的重试会得到 owner 状态错误（如 action 不匹配）而非重放；Host 需按 `agent_skill_details` 的当前 activation 状态判定。
