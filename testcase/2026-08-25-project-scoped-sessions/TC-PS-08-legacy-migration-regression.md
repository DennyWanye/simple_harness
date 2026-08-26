---
id: TC-PS-08
purpose: Verify the v33 one-time reset removes all pre-upgrade conversation data without deleting global configuration or new post-upgrade data
status: active
surface: integration
type: hybrid
obligations:
  - TO-A8
  - TO-R4
tags:
  - migration
  - regression
  - restart
entrypoint: application startup migration
revision: 3
---

# TC-PS-08 — v33 升级一次性清空与新数据保留

## 前置

- 使用不含真实用户数据的 v9、v17、v23、v31 和 v32 旧库 fixture。各版本只写入当时真实存在的表；v31 含 Session、消息/归档/标题、Code Session、Run/上下文/投影、Memory 与每 Session Provider binding，v32 另含 Project/Binding。
- 同时写入全局 Provider、默认模型、应用设置和测试项目文件，记录其 SHA-256；真实 macOS 测试复用现有登录态验证 Keychain，不把 secret 写入 fixture 或日志。
- 所有破坏性操作只针对 `.local-test-evidence/` 下的隔离副本。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 用 v9、v17、v23、v31、v32 五份隔离旧 user-data 分别启动候选构建；另用完全空的 user-data 冷启动。 | 所有旧版本都升级到 completed v33 且进入空 Session/空 Project；fresh 安装也正常启动，不依赖旧库。 |
| 2 | 按下方“必须删除矩阵”逐类查询，并为每类使用独立哨兵。 | 每个必须为零项均为零，每个必须不存在项均不存在，`foreign_key_check` 为空；不能只靠 UI 空列表或搜索一个 Session ID 判定。 |
| 3 | 升级前后比对全局 Provider/模型/应用设置与项目文件 SHA-256；真实 macOS 测试期间禁止重新登录或写入凭据，升级后直接完成一次 Provider Run。 | 所有保留哨兵不变；项目文件未改写；同一 app identity 的原 Keychain 登录态仍可调用，日志不出现 secret，并记录 `provider_call_succeeded/keychain_credential_preserved/post_upgrade_run_completed`。 |
| 4 | 对下方每个故障点使用独立 fixture：首次启动注入中断，检查入口关闭；第二次启动继续；第三次启动确认不重复清理。 | 每个故障点第一次都不能开放 Session/UI/Run，第二次最终 completed 且旧哨兵全删，第三次不会删除升级后新数据。 |
| 5 | 在升级后的真实 UI 新建 Project/Binding/Session/标题/消息，完成一个真实 Provider Run，并形成 context/projection、每 Session Provider binding 和一条可召回的对话 Memory。 | 新数据链完整可见，Run 正常完成。 |
| 6 | 完全退出并再次启动同一 v33 user-data，逐项读取步骤 5 的新数据。 | Project/Binding/Session/标题/消息/Run/context/projection/Provider binding/Memory 全部保留，证明 reset 只执行一次。 |

## 必须删除矩阵

| 存储 | 必须为零/不可恢复 | 必须保留 |
|---|---|---|
| `state.db` | Session、message/archive/title、Project/Binding、creation receipt/tombstone、Run admission/handoff、context、projection/outbox、每 Session Provider binding、对话 Memory identity | schema authority、全局配置文件 |
| `messages_vec` | 所有旧向量与旧消息检索命中 | sqlite-vec 能在 reset 后为新消息正常工作 |
| `workflow.db` / workflow blobs | execution/workflow/trace/task grant 行及其内部 blobs | workflow schema/evaluation 定义 |
| `companion.db` | Session/Run/job/projection 及对话派生候选数据 | Companion 全局设置、profile、reminder |
| SDK execution / product state | execution DB、Run catalog/runtime/lease、authorization saga/task grant | SDK 非 Run 全局配置 |
| `memory.db` | 整个旧对话认知库及 sidecar/lock | 升级后新建 Memory 可正常持久化 |
| state backup | `state.db.bak.*` 与 manifest | 无 |
| 文件系统 | 无：不得删除真实项目/产物 | Provider/默认模型/应用设置、Keychain、skills/plugins、billing、真实项目/产物文件 |

实现与自动化测试必须共同引用 `project_session_reset.RESET_MANIFEST` 作为精确清单；本表冻结产品级数据类别，二者有差异即失败。

## 故障恢复矩阵

每行均执行“独立 fixture → 中断后入口不可用 → 重启完成 → 创建新数据 → 再重启仍保留”。

| fault point |
|---|
| before / after v33 reset DDL commit |
| before / after external-store reset phase |
| before / after vector reset |
| before / after backup cleanup |
| before completed marker commit |

## 通过条件与证据

- 步骤 1～6 和两张矩阵全部满足；不能只以 schema 版本或 UI 空列表作为 PASS。
- primary evidence：各版本 fixture hash、逐类别清理计数/独立哨兵搜索、保留文件 hash、逐故障点恢复日志、UI 空态、Provider Run 与完整新数据重启截图/数据库快照。
