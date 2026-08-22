# S6 — simple_harness 正式接入与 windows-mcp 真 E2E

<!-- slice-status: completed -->

## Release unit

- MUST AC：AC-6、AC-8（2/8）
- Tasks：10/10
- 高风险系统：product composition、Memory authority cutover、UI E2E（3/3）
- 依赖：S5 authoritative candidates

## 文件影响

| 文件/区域 | 修改 |
|---|---|
| `backend/requirements*.txt`/wheel vendor manifest | exact Harness 0.3.0 + Memory 0.4.0 candidate/published hashes |
| `backend/main.py` SDK stack与chat/continuation入口 | 构造MemoryManager、trusted identity、自动start/signal，不手动recall/prepare |
| `backend/deskpet/memory/session_db.py` | 生命周期只持有manager；保留non-Harness product outbox |
| `backend/deskpet/memory/product_outbox.py` | 仅non-Harness provenance；适配manager explicit projection API |
| `backend/deskpet/memory/identity.py`（新） | 本地可信 identity actor、deployment/household binding与session不变量 |
| `backend/deskpet/sdk_adapters/context_provider.py`（新/改） | preparation id + immutable source snapshot ref 的只读non-Memory Context provider |
| `backend/deskpet/sdk_adapters/context_source.py`（新） | ingress侧content-addressed source snapshot与bounded retention |
| `backend/deskpet/migrations/sdk_v4_cutover.py`（新） | 产品级双库coordinator、identity map、upgrade journal与恢复 |
| `backend/deskpet/sdk_adapters/memory_faults.py`（DEV-only新） | 仅隔离测试目录可启用的deterministic recall/record fault wrapper |
| `backend/deskpet/memory/recall_adapter.py`, tool catalog | foreground live auto-recall入口退休/改 frozen-stage read |
| `backend/deskpet/sdk_adapters/*` | non-Memory Context provider、identity resolver、composition conformance |
| `backend/tests/**`, frontend tests | authority、regression、fault、artifact/context tests |
| `ARCHITECTURE/**`, `PROJECT_STATUS.md` | 当前事实和最终证据索引 |

## Tasks

### S6-T1 — Exact candidate vendoring [AC-6, AC-8]

- 更新唯一依赖事实源和lock/vendor manifest；startup verifier检查distribution origin/version/SHA，拒绝editable、
  path checkout或旧wheel。
- 测试backend进程日志只输出版本/hash/origin category，不输出凭据路径内容。

### S6-T2 — MemoryManager composition与identity [AC-6]

- 启动时通过production builder构造一个manager，复用现有已解析的本地BGE资源和真实production embedder
  adapter；preflight要求`is_mock=false`和完整lineage，不由Memory builder下载模型。传入
  ProductionRuntimeConfig.memory，ownership选择与SessionDB资源图一致，确保只close一次。
- `deployment_id`取`state_db_identity.instance_id`；`actor_id`只取`LocalAuthSnapshotProvider`通过
  `load_or_create_local_identity(user_data_dir)`校验得到的`HumanIdentity.identity_namespace_hash`。原始本地UUID、
  provider配置、API key、模型文本和普通请求payload均不得直接成为或覆盖Agent Memory identity；常量
  `legacy_local_profile`仅保留为历史companion profile标识，禁止作为Agent Memory actor。
- 首次本地身份初始化时在state.db `memory_identity_bindings`获得随机稳定household id，binding成功后才允许首
  Turn；进程重启、Provider CRUD/排序、API key变化均保持同actor/household。不同user-data目录产生独立actor与
  household，既有session不可换绑；本地identity缺失时可按正式初始化路径创建，格式损坏、namespace校验失败或
  binding歧义则在LLM前fail closed并给出明确产品错误。删除默认`"user"` fallback。

### S6-T3 — Root/continuation去手工Memory prepare [AC-6]

- `_run_product_harness_chat`和continuation入口只构造ConversationTurnInput/Continuation与正式non-Memory
  Context provider；删除ConversationMemoryRecallQuery、manual recall、Memory lineage/private partition拼装
  和`prepare_consumer_conversation_context`调用，并await新的async start/signal入口。
- 产品ingress每次进入SDK前把persona/history/skills/attachments/project/task输入物化为state.db中的
  content-addressed immutable source snapshot并传ref；provider只读snapshot、不写DB，同key/ref只返回同hash。
  SDK先保存claim/ref后调用provider，crash/restart不重读变化源；retention长于SDK stage horizon，cleanup仅删
  已STAGED/CONSUMED且过期snapshot。ingress原子写`PENDING` binding/lease；超过orphan horizon时必须由
  execution claim-inspector证明无claim/ref才回收，inspector故障保留重试；共享hash使用binding refcount。
- root构造`ConversationTurnInput(..., context_source_snapshot_ref=root_ref)`；每个continuation构造
  `ConversationContinuationInput(..., context_source_snapshot_ref=continuation_ref)`，禁止传root ref或在provider
  内按同ref改payload。测试root+两个continuation各自ref/hash、同continuation换ref冲突和两处crash恢复。
- SDK start/signal返回的stage/public snapshot继续供Context Inspector与Provider binding使用。

### S6-T4 — Authority cutover而非误删product outbox [AC-6]

- foreground Harness message只走execution v4 committed-turn outbox；SessionDB不再给这些message写
  product_memory_outbox。
- companion/background/non-Harness message继续走product outbox，并通过manager explicit projection方法写入，
  provenance互斥测试证明同一message不进两套authority。

### S6-T5 — Foreground Memory tools收敛 [AC-6]

- 普通foreground catalog移除可二次live recall的`memory_recall/memory_search`；若Context Inspector需要，新增
  read-only frozen-stage redacted reader且不可触发backend recall。
- simple_harness现有显式remember/write与forget继续作为授权mutation tool，绑定trusted run identity和独立
  event key；不能更改deployment/household/actor或绕过scope。本轮不虚构`memory_share`产品Tool、schema、权限
  UI或迁移；Memory SDK正式authorized share/projection API保持公开、受测且接口就绪，供未来AIPhone、
  K6/AgentOS与NovelTagSystem消费者接入。

### S6-T6 — Automated product regression [AC-6]

- 更新composition/context/ingress/tool catalog/product outbox/provider refresh tests；加recall/record计数器证明
  root/continuation无双调用。身份测试覆盖同user-data重启稳定、Provider CRUD/API key变化稳定、不同user-data
  隔离、损坏/畸形本地identity fail closed，以及模型/payload不能覆盖actor。
- 跑backend critical/affected/full-surface shards、frontend type/vitest、Rust cargo checks；ArtifactCard、Context
  budget、persona/skills/history/attachments不得缩水。

### S6-T7 — Fault/cold restart automation [AC-6]

- unique test data目录验证fresh execution v4/memory v4；recall timeout→主Run完成；record transient→响应完成、
  restart后outbox收敛；apply-before-ack重放一份receipt；同路径fail-fast。
- 产品coordinator先备份两库并冻结v3 user/session→完整trusted AgentIdentity的一对一map；缺失/歧义停止。
  Harness migrator只迁execution并输出中立hash manifest；Memory公开migrations API迁自己的库并导入eligible
  legacy terminal message，两SDK不互调。Harness manifest按KEEP_COMPLETED_PAIR、SUPPRESS_TENTATIVE、
  SUPPRESS_TERMINAL、DEFERRED_TURN四类覆盖root+多continuation；产品同时冻结non-Harness provenance
  manifest，两manifest必须恰好覆盖Memory v3全部source_event_id。已apply的三个非KEEP类及其facts/embedding不复制并写
  suppression receipt；recall snapshot丢弃、digital twin从保留facts重建。恢复后deferred completed由Harness
  生成一个正常v4 pair，failed/cancelled零pair。
- v3 NULL assistant continuation FK必须由terminal event/receipt identity + claim epoch + durable sequence唯一
  解析，歧义即停止。迁移后再次收到continuation时，Harness enqueue事务CAS supersede旧legacy cursor并把
  新input设为active；terminal只配active input。测试连续两次post-migration continuation及每个cursor crash窗。
- coordinator journal记录两temp hash与swap phase；每个copy/import/check/rename/startup中断都只开放校验后的
  全旧或全新pair，mixed pair自动恢复backup和旧pin。矩阵覆盖全部run状态、identity map、manifest tamper、
  root+多continuation、四类applied/pending、NULL FK、迁移后新continuation supersession、derived级联与swap fault。
- 自动化只作cheap gate，不替代MCP UI。

### S6-T8 — 冻结black-box testcase与启动真环境 [AC-6]

- 在实现diff之外按acceptance冻结SH-M1～SH-M5，并依据已批准AC-6/AC-7故障断言增加SH-M6 testcase
  （不是新行为范围）；冻结hash、manifest applicability和impact paths，gate init。
- 清理orphan进程；只启动Tauri，设置唯一端口、`DESKPET_BACKEND_DIR=<当前backend>`、显式Python、全新
  `DESKPET_USER_DATA_DIR`，确认日志为dev source backend且`DESKPET_SDK_DESKTOP_TEST`未启用。

### S6-T9 — windows-mcp SH-M1～SH-M6 [AC-6, AC-7]

- 每case动作前声明`坐标|动作|期望`；Snapshot/Screenshot→真坐标点击/输入→截图→Tauri/backend safe logs。
- SH-M1 Max重启召回；SH-M2真实PPT+ArtifactCard+极简偏好；SH-M3至少10轮且独立root≥2；SH-M4经正常
  UI预置恶意Memory后不遵循/不越权；SH-M5全新数据冷启动、退出重启、单receipt。
- SH-M6仅在`DESKPET_DEV_MODE=1`且user-data位于`.local-test-evidence`时装配fault wrapper：recall timeout
  下从UI输入“请用一句话回答：今天继续完成SDK验收。”仍非空completed；record transient下从UI输入
  “记住我喜欢蓝色，然后回复已收到。”先正常响应，重启无fault进程后询问“我喜欢什么颜色？”回答蓝色。
  production/非隔离目录设置fault env必须fail closed，UI ingress全程真实。
- 失败至少3种workaround重试；不能测则BLOCKED向用户报告，不用协议直注/脚本回放补PASS。

### S6-T10 — Promotion、回归复测、事实源 [AC-8]

- 真UI全绿后promotion S5同一candidate bytes/tag；simple_harness pin切发布exact版本/hash，重新installed-origin
  smoke和受影响MCP场景。
- 若promotion后复测失败，pin回上一个已验证版本并恢复pre-cutover backup；不可变tag/bytes不覆盖，按registry
  能力yank并从修复提交发布新patch candidate，旧坏版本和处置写入changelog/status。
- 原始证据只留`.local-test-evidence`并计算SHA；更新Memory boundary、主architecture、PROJECT_STATUS最近
  里程碑/完成度/日期；full audit、re-attest、finalize exit 0。

## Required scenarios

| ID | 必须证明 |
|---|---|
| SH-M1～M5 | 逐字使用acceptance矩阵，不在本slice另改oracle |
| S6-A1 | foreground计数：one recall + one committed turn + one Memory receipt |
| S6-A2 | non-Harness outbox保留且与foreground provenance互斥 |
| S6-A3 | recall outage不阻断；record outage重启收敛 |
| S6-A4 | exact wheel origin/version/SHA且非editable |
| S6-A5 | persona/skills/history/attachments/tool/artifact/context regression全绿 |
| S6-A6 | fresh local identity、同user-data重启及Provider/API key变化稳定、不同user-data隔离、损坏identity fail closed、缺binding与v3→v4可恢复cutover |
| S6-A7 | v3各run状态及已apply/pending Memory交叉矩阵迁移无tentative残留/重复且fault可完整回滚 |
| S6-A8 | PENDING context orphan有界回收且不误删active/shared source snapshot |
| S6-A9 | root/continuation ref互异且各自稳定重放；旧candidate复用root ref的行为被contract test拒绝 |

## 真测禁止项

- 禁止直接向`ws://127.0.0.1:*`注入消息。
- 禁止用pytest/last-mile smoke/registry import/DB查询单独宣称UI PASS。
- 禁止手动启动backend或第二个Vite；禁止测到bundled旧backend。
- 禁止把账号、token、cookie、Memory私密原文写入截图或Git报告。
