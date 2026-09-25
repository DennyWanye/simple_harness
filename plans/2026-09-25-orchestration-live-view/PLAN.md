# 编排实时可视化方案（执行图 + 分解过程 + 整体流程）

日期：2026-09-25　版本：第 3 版（已按两轮独立审查修订，见第 8 节）　状态：待确认后执行

## 1. 目标

每个启动编排的任务（Mission）都能在界面上实时看到：

1. **整体流程**：处在 创建 → 规划 → 执行 → 验证 → 完成 的哪一步，卡在哪、为什么。
2. **执行图**：每个子任务的状态，状态变化后 2 秒内体现在图上。
3. **分解过程**：复合任务拆成了哪些子任务、用了哪个方法、哪些规划决定被提交或被拒绝。
4. **过程记录**：每个节点的尝试、重试、验证结果、事件流；可以按规划版本切换查看当时的结构。

界面要简洁易懂。先保证主流程实时、准确，再做好看的部分。

## 2. 现状（2026-09-25 核对过代码）

路径约定：Host 仓库根目录记为 `./`；Host 实际使用的 SDK 是 `./sdk/simple-harness-sdk/src/agent_orchestrator`，下文记为 `SDK/`。

| 位置 | 事实 | 结论 |
|---|---|---|
| `SDK/storage/schema.py:46` `events` 表 | 列：`seq`（AUTOINCREMENT，全局递增）、`event_id`、`idempotency_key`、`type`、`trace_id`、`mission_id`、`task_id`、`attempt_id`、`actor_type`、`actor_id`、`payload_json`、`created_at`（REAL）；索引 `events_mission_idx(mission_id, seq)`；没有租户列 | 作为唯一事实来源。租户隔离靠"`mission_id` 先经过所有权校验" |
| `SDK/storage/htn_schema.py` | 分层计划存在普通表里：`plan_revisions`、`plan_memberships`、`method_instances`、`method_child_occurrences`、`order_constraints`、`data_requirements`，都在同一个 `orchestrator.db` | **执行图的结构从这里读** |
| `SDK/storage/schema.py:84` `tasks` 表 | `task_id`、`mission_id`、`status`（`TaskStatus`）、`json` | 叶子任务的状态从这里读 |
| `SDK/orchestrator/hierarchical_dispatch.py:3003` | 复合任务阶段每次变化都写 `CompoundPhaseChanged` 事件，payload 为 `{occurrence_id, task_id, plan_revision, phase, display_status, readiness_reason}` | 复合任务的状态从这里读，由 SDK 自己算好 |
| `SDK/api/taskgraph.py:147` 严格执行图读取 | 要求任务先开启执行图内核（`taskgraph_policy_bindings`）；Host 的 `enable_taskgraph_contract`（`service.py:1379`）故意不对外开放，生产代码里从未调用 | **真实任务调 `taskgraph.snapshot` 一律返回 `NOT_ENABLED`**，现在的执行图页面对真实任务永远是空的 |
| `service.py:1113-1117` | 新建任务默认 `orchestration_semantics_version = HIERARCHICAL_SEMANTICS`（分层方式） | 真实任务基本都有分层计划表数据 |
| `backend/deskpet/orchestration/pump.py` | 每秒只读查一次每个任务的 `(status, 最大 seq)`；广播 `mission_changed{mission_id,status,last_seq}` 给所有控制连接（`wiring.py:60`），推送不带内容 | 要改成带事件 |
| `projection.py:576` `project_event` | 输出 `seq/type/created_at/task_id/attempt_id/actor_type`；`HumanCommentAdded` 额外从 payload 取 `text` 生成 `summary` | 推送里的事件复用这个函数 |
| `MissionsView.tsx:678-686` | 打开的任务每 5 秒拉一次详情。注释写明：等人操作的状态（如"授权本轮规划"）不一定伴随新事件 | 不能直接删 |
| `useMissionsFeed.ts:96` | 每 15 秒拉的是 `orchestration_status`（编排后台是否出错），不是任务状态 | **保持不动** |
| `useMissionsFeed.ts:76-85` | 处理 `mission_changed` 时只挑出 `mission_id/status/last_seq` 三个字段 | 要加新字段 |
| `MissionTaskGraph.tsx` + `taskgraphStore.ts` | 调严格读取接口，手写 SVG 画图，每列固定 12 个节点 | 整体换成新的运行视图 |
| `package.json` | `cytoscape` 只出现在 `package.json` 和 `theme/tokens.ts` 的注释里 | 删掉 |
| Tauri CSP | `null` | 不会拦 Web Worker |

## 3. 核心设计决定

**决定一：执行图改用新的"运行视图"，不再依赖严格读取接口。**
- 新增 Host 只读动词 `mission_live_graph`，直接只读查询 `orchestrator.db`。先例是 `pump.py`，它已经这样做。
- 结构来自分层计划表。
- 叶子任务的状态来自 `tasks.status`。
- 复合任务的状态来自最新一条 `CompoundPhaseChanged` 事件。
- Host 和前端都**不推导状态机**，只拼接 SDK 已经写好的事实。
- 严格读取接口的动词保留在后端，前端不再调用。

**决定二：推送带上新事件本身。**
- `mission_changed` 新增三个字段：`from_seq`、`events`、`truncated`。
- 每条事件都经过 `project_event` 投影，评论的 `summary` 不会丢。
- 前端**只对当前打开的任务**追加事件或补缺口。其他任务的推送只更新列表状态，不动 `eventCursor`。

**决定三：执行图按"事件触发 + 防抖"重读运行视图。**
- 当前打开的任务收到新事件后，等 800ms 再重读 `mission_live_graph`。读取还在进行时又来了事件，就合并成一次后续读取。
- 返回的 `through_seq` 小于当前显示的，丢弃。
- `plan_revision` 不变：只更新节点的颜色和文字，布局不动。
- `plan_revision` 变了：重新布局，并把新出现的 `occurrence_id` 高亮 3 秒。直接和上一份节点集合比，不调 diff 接口。

**决定四：一张图。**
- 复合任务画成可折叠的框，框内是它拆出来的子任务（`method_child_occurrences`）。
- 箭头表示先后顺序（`order_constraints`）。
- 数据依赖（`data_requirements`）默认隐藏，有开关可以打开。

**决定五：布局用 elkjs，渲染用 @xyflow/react。**
- 布局算法是纯函数 `buildElkGraph()`，jsdom 下可以直接单测。
- 在 Worker 里用 `import ELK from "elkjs/lib/elk.bundled.js"`。
- Worker 的创建方式是 `new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" })`。

**决定六：显示状态。先看 `status`（或复合任务的 `phase`），只在少数情况下再看 `readiness_reason`。**

叶子任务 `status`（`SDK/contracts/state_machines.py:61`）：

| 值 | 显示 | 颜色 |
|---|---|---|
| `BLOCKED` | 等待 | 灰 |
| `READY` | 可调度 | 浅蓝 |
| `ACTIVE` | 运行中 | 蓝（带动效） |
| `VERIFYING` | 验证中 | 紫 |
| `COMPLETED` | 完成 | 绿 |
| `FAILED` | 失败 | 红 |
| `CANCELLED` | 取消 | 灰色虚线 |

复合任务 `phase`（`SDK/orchestrator/hierarchical_dispatch.py:479`，全部小写）：

| 值 | 显示 | 颜色 |
|---|---|---|
| `planning_ready` | 等待拆分 | 灰 |
| `refining` | 拆分中 | 蓝（带动效） |
| `waiting_children` | 子任务进行中 | 蓝 |
| `composition_review` | 汇总验收中 | 紫 |
| `resolution_committed` | 完成 | 绿 |
| `evidence_or_authority_wait` | 等人处理 | 橙 |

补充规则：
- 如果复合任务还没有任何 `CompoundPhaseChanged` 事件，就用它 `tasks.status` 按叶子任务的表显示。
- 复合任务是 `planning_ready` 或 `evidence_or_authority_wait` 时，悬停提示里显示 `readiness_reason` 的中文，沿用 `MissionTaskGraph.tsx` 现有的 `labels` 表，删除那个文件前先把表挪到 `displayStatus.ts`。叶子任务没有这个原因，不显示。
- 未知值一律显示"未知"（灰），同时在控制台打一条告警，不能报错。

**"可能卡住"**：节点处于运行中、验证中或拆分中，并且该 `task_id` 的最后一条事件距今超过 10 分钟，就加红色角标。这只是提示，不改变状态。

## 4. 分步实施

每一步单独提交。先跑小样本，再铺开。全量回归只在合并前后各跑一次。

测试命令：
- 后端：`backend/.venv/bin/python -m pytest backend/tests/orchestration -q`
- 前端：`cd tauri-app && npm test && npm run typecheck`

### 第 0 步：准备真实数据 + 核对表的含义（1 小时内完成）

1. 本机默认数据目录里没有任务。先按 `native-ui-click-recipe` 的做法，用独立数据目录和日卡线路跑一个真实任务，目标要能拆出复合任务，比如"写一份包含三章的调研报告"。跑到出现至少 1 个复合任务和 3 个子任务。
2. 在这份数据上测第 1 步全部 SQL 的总耗时（目标 < 30ms），并抽查父子关系和界面上看到的拆分一致，结果记在本目录 `00-数据核对.md`。
   - 已从代码确认、不用再核对的两点：每次提交计划都会把**整个网络**写进 `plan_memberships`、`order_constraints`、`data_requirements`（`SDK/orchestrator/plan_commits.py:1240-1251`，`adopted` 恒为 1）；版本 0 是种子，`plan_revisions` 里没有这一行。
3. 把这份数据目录复制到 `backend/tests/orchestration/fixtures/live_graph_real.db`，作为第 1 步的回归夹具。入库前先扫一遍有没有密钥。

### 第 1 步：后端运行视图动词 `mission_live_graph`

新文件 `backend/deskpet/orchestration/live_graph.py`，函数签名是 `read_live_graph(service, request) -> dict`。

**请求**：`{mission_id: str, revision?: int}`。
- 字段校验和 `taskgraph.py:read_taskgraph` 一样，多出的键直接拒绝。
- 所有权先校验：`service._require()._mission(mission_id)`。

**连接与事务**：
- 连接方式：`sqlite3.connect(f"file:{root}/orchestrator.db?mode=ro", uri=True, timeout=1.0)`。
- 执行 `BEGIN` 后依次跑下面的查询，最后 `ROLLBACK`。这样所有查询读到的是同一时刻的数据（WAL 模式下成立）。

**SQL**（参数都用占位符）：
```sql
-- 1 选版本（没传 revision 时）
SELECT revision FROM plan_revisions WHERE mission_id=? AND state='ACTIVE';
-- 0 行 → source="planning"（还没有计划），nodes/edges 为空，只返回 through_seq；>1 行 → 错误 GRAPH_INTEGRITY
-- 传了 revision：SELECT 1 FROM plan_revisions WHERE mission_id=? AND revision=?，不存在 → INVALID_REVISION

-- 2+3 节点、父节点与方法（一条 SQL）
-- plan_memberships.instance_id = 产生这个节点的方法实例（plan_commits.py:1240-1246）；
-- 父节点 = 该实例的 goal_occurrence_id（htn_store.py:421）。根节点 instance_id 为 NULL → parent 为空。
-- 注意：不要用 method_child_occurrences.goal_occurrence_id（那是子节点自己的目标，htn_store.py:2284-2288），
-- 也不要按 method_instances.state 过滤（它是"当前状态"，查历史版本会错）。
SELECT m.occurrence_id, m.task_id, m.form, m.requiredness,
       i.goal_occurrence_id AS parent, i.method_id, i.method_version
  FROM plan_memberships m
  LEFT JOIN method_instances i ON i.mission_id=m.mission_id AND i.instance_id=m.instance_id
 WHERE m.mission_id=? AND m.revision=?;
-- parent 不在本版本节点集合里时，按根节点处理（parent 置空），并打一条告警日志

-- 4 顺序和数据依赖
SELECT before_occurrence, after_occurrence FROM order_constraints WHERE mission_id=? AND plan_revision=?;
SELECT producer_occurrence, consumer_occurrence FROM data_requirements WHERE mission_id=? AND plan_revision=?;

-- 5 叶子任务状态和尝试次数
SELECT task_id, status FROM tasks WHERE mission_id=?;
SELECT task_id, COUNT(*) FROM attempts WHERE mission_id=? GROUP BY task_id;

-- 6 复合任务最新阶段（SQLite 聚合 MAX 时，同一行的其他列取自最大值所在行）
-- 用 <= 而不是 =：新版本提交后、阶段还没推进前，沿用上一版本的最新阶段。payload 里 plan_revision 是整数，比较没问题。
SELECT json_extract(payload_json,'$.occurrence_id'), json_extract(payload_json,'$.phase'),
       json_extract(payload_json,'$.readiness_reason'), MAX(seq)
  FROM events
 WHERE mission_id=? AND type='CompoundPhaseChanged'
   AND json_extract(payload_json,'$.plan_revision')<=?
 GROUP BY 1;
-- 只保留 occurrence_id 在本版本节点集合里的行

-- 7 每个任务的最后活动时间，以及整体截止序号
SELECT task_id, MAX(created_at) FROM events WHERE mission_id=? AND task_id IS NOT NULL GROUP BY task_id;
SELECT COALESCE(MAX(seq),0) FROM events WHERE mission_id=?;
```

**返回**：
```json
{"schema_version":1,"mission_id":"…","source":"htn|planning","plan_revision":3,"through_seq":812,
 "nodes":[{"occurrence_id":"…","task_id":"…","form":"compound|primitive","parent":"occ-id|null",
           "method":"method_id@version|null","status":"ACTIVE 或 复合阶段小写值","readiness_reason":"…|null",
           "attempt_count":2,"last_event_at":1790000000.5}],
 "edges":[{"kind":"order|data","source":"…","target":"…"}],
 "revisions":[1,2,3]}
```
- 叶子节点的 `readiness_reason` 固定为 `null`：库里只有复合任务的阶段事件带这个值。
- `revisions` 来自 `SELECT revision FROM plan_revisions WHERE mission_id=? ORDER BY revision`，给版本切换用。
- 父子关系不作为边返回，只放在 `parent` 字段里。
- 节点数上限 2000，超过返回错误 `BOUND_REACHED`。
- 同时注意 `projection.py` 的原则：payload 里的其他字段一律不外传。

**注册**：
- `handlers.py` 的 `MESSAGE_TYPES` 加 `"mission_live_graph"`，`_ACTIONS` 加 `lambda s, b: s.live_graph(b)`。
- `service.py` 加方法 `live_graph(self, request)`，内部调 `read_live_graph(self, request)`。
- `backend/tests/orchestration/test_handlers_contract.py:22` 的完整集合同步加上新动词。
- `main.py` 按 `mission_` 前缀自动放行，不用改。

**测试**（先写测试）：
- 建库：`Store.open(tmp_path / "orchestrator.db")`（`SDK/storage/store.py:200`）会跑完全部迁移，分层计划表在第 16 号迁移里一起建出。连接开着外键约束，插入顺序是 `missions` → `plan_revisions` → `method_instances` → `plan_memberships` 和其余表。
- 插入：1 个根复合任务 + 1 个方法实例 + 3 个子任务 + 2 条顺序约束 + 阶段事件。断言：
  - 子任务的 `parent` 是根，根的 `parent` 为空；`method` 和 `status` 正确；
  - 复合任务取的是最新阶段；
  - 版本 2 还没有阶段事件时，沿用版本 1 的阶段；
  - 更高版本的阶段事件不会被读进来。
- 读取版本 1 时，结构是版本 1 的：插入一个版本 2 新增的节点，断言它不出现。
- 没有 ACTIVE 版本时返回 `source="planning"`。
- 别的用户的任务读不到：要用真的 `OrchestrationService` 测，建法照 `backend/tests/orchestration/_support.py`。
- 多出的请求键被拒绝。
- 输出里不包含任何 payload 字段（只允许 `phase` 和 `readiness_reason` 两个值）。
- 用第 0 步复制的真实数据夹具跑一次：不报错，节点数大于 0。
- 连接以只读方式打开：对这个连接执行写入会抛出 `sqlite3.OperationalError`。

### 第 2 步：推送带事件（后端）

`pump.py` 做四处改动：
1. `_QUERY` 不变。`_seen` 改为保存 `(status, last_seq)`，并增加一个 `self._cursor: dict[str, int]`。
2. 对 `last_seq` 前进的任务，再查一次：
   ```sql
   SELECT seq, type, created_at, task_id, attempt_id, actor_type,
          CASE WHEN type='HumanCommentAdded' THEN payload_json END
     FROM events WHERE mission_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT 51;
   ```
   - 第三个参数传本轮 `_QUERY` 读到的 `last_seq`，避免两次查询之间写入的事件被夹带进来。
   - 把每一行组装成 `{seq,type,created_at,task_id,attempt_id,actor_type,payload}`（`payload` 由第 7 列 `json.loads` 得到，没有就是 `{}`），然后交给 `projection.project_event`。
   - 取到 51 行说明还有更多：只推前 50 条，并设 `truncated=true`。
3. 广播内容：`{mission_id, status, last_seq, from_seq, events, truncated}`，其中 `from_seq` 是这次查询的起点（上次的游标）。
4. 进程刚启动、某任务第一次出现时，不查事件：`events=[]`，`truncated=true`，`from_seq=last_seq`。

**测试**（新写，放 `backend/tests/orchestration/test_pump_events.py`；现有只有 `test_projection.py:194` 那一个推送测试）：
- 连续写 3 条事件，一次推送带齐，`from_seq` 正确。
- 写 60 条事件，推送 50 条，并且 `truncated=true`。
- 评论事件带 `summary`。
- 推送里不出现 `payload`。
- 首次看到某任务时 `events=[]`。
- pump 的连接是只读的：写入会抛出 `sqlite3.OperationalError`。

### 第 3 步：前端接增量（只处理当前打开的任务）

- `missionsStore.ts`
  - `MissionChange` 类型加三个字段：`from_seq?: number`、`events?: ProjectedEvent[]`、`truncated?: boolean`。
  - `applyChange` 仍然只管列表状态，已有的"只前进不回退"逻辑保留。
  - 新增一个纯 action `appendEvents(missionId, fromSeq, events, lastSeq)`：`fromSeq === eventCursor` 时追加并前进游标，返回 `true`；否则不动，返回 `false`。
- `useMissionsFeed.ts:81-85`：把三个新字段一起传给 `applyChange`，保证类型一致。
- `MissionsView.tsx:529-537` 现有的 `mission_changed` 分支是追加和补缺口的唯一位置（store 里没有通道，分页和"同时只有一页在途"的控制在 `MissionsView.tsx:477-499` 的 `fetchEvents`）：
  - 只处理 `mission_id === selectedId` 的推送；
  - `truncated` 为假时调 `appendEvents`；返回 `false`，或者 `truncated` 为真，就调 `fetchEvents(id, true)` 分页补齐；
  - 其他任务的推送不改变游标。
- `MissionsView.tsx`：选中的任务收到新事件时，防抖 1 秒重拉详情。
  - **5 秒轮询这一步保留**。原因：等人操作的状态变化不一定产生事件，而要让 pump 察觉这类变化，要先核实"授权本轮规划"这类请求存在哪张表。这件事放进第 7 节的后续项，核实并补上测试之后再删轮询。
- `useMissionsFeed.ts:96` 的 15 秒定时不动。
- **测试**：
  - 事件接得上时直接追加；
  - 有缺口或 `truncated` 时触发补拉；
  - 非当前任务的推送不改变游标；
  - 迟到的旧推送被忽略。
- **验收**：真实模型跑一个任务，从后端写入事件到界面时间线出现这一条，不超过 2 秒。

### 第 4 步：执行图重做（前端）

**依赖**：`npm i @xyflow/react elkjs`，`npm rm cytoscape`，同时删掉 `theme/tokens.ts` 里提到 cytoscape 的那条注释。装完后先跑一次 `npm run build`，确认和 React 19 / Vite 8 能一起用。

**新文件**（放在 `tauri-app/src/views/liveGraph/`）：
- `liveGraphStore.ts`
  - 解析 `mission_live_graph` 的返回：未知字段忽略，缺少必需字段就报错；
  - 管理"读取中 + 待读"合并；
  - 丢弃 `through_seq` 倒退的结果；
  - 记住上一份节点集合，用来高亮新节点。
- `displayStatus.ts`：第 3 节决定六的映射，纯函数。
- `buildElkGraph.ts`：纯函数，把运行视图转成 elk 输入。
  - 复合任务作为父节点，子节点按 `parent` 放进 `children`；
  - order 边和打开开关后的 data 边作为连线；
  - 布局选项：`elk.algorithm=layered`、`elk.direction=DOWN`、`elk.hierarchyHandling=INCLUDE_CHILDREN`。
- `layout.worker.ts`：只负责接收 elk 图、调用 `elk.layout()`、返回坐标。
- `LiveGraph.tsx`：React Flow 画布。
  - 自定义节点显示：标题、状态色、尝试次数、卡住角标。
  - 复合任务是可折叠框，点击标题栏切换折叠；折叠状态存在组件 state 里。
  - `plan_revision` 不变时复用上次的坐标。
  - 节点超过 200 个时，默认折叠所有不包含"运行中"节点的复合任务。
  - 布局超过 1 秒时显示"正在排版"。

**节点标题**：
- 按 `task_id` 在 `mission_get` 详情的 `tasks[]` 里找 `id` 相同的一项，取它的 `goal.text`（`goal` 是 `{text, source:"model"}` 结构），截到 40 字，悬停显示全文，并标注"模型生成"。
- 找不到时显示"子任务 N"（N 为节点序号），不显示 id。

**接入**：
- 在 `MissionsView.tsx` 里，用 `LiveGraph` 替换 `MissionTaskGraph`。
- 删掉 `MissionTaskGraph.tsx`、`MissionTaskGraph.css`、`taskgraphStore.ts` 以及对应的测试。后端的严格读取动词保留。
- `source="planning"` 时显示"正在规划，计划生成后这里会出现执行图"。
- 如果任务不是分层方式（没有任何 `plan_revisions`，且任务已经在执行），就用详情里的 `tasks[].dependency_ids` 和 `status` 画一张平铺图。这张图用同一套 `displayStatus` 和布局。

**测试**：
- `displayStatus` 表格测试覆盖第 3 节的全部值，外加一个未知值。
- `buildElkGraph`：输入 2 层嵌套、20 个节点，断言父子关系和连线数量。
- 同一 `plan_revision` 的第二份结果不触发重新布局。
- 旧的 `through_seq` 被丢弃。
- 读取进行中又来了事件，只合并成一次后续读取。

**验收**：用真实鼠标点击，看一个会拆出复合任务的真实任务：框在展开，颜色跟着变化，能折叠，不需要手动刷新。

### 第 5 步：节点详情栏 + 规划决定记录

**点击节点打开右侧栏**，内容如下：
- 任务内容：`goal.text`。
- 显示状态，以及 `readiness_reason` 的中文。
- 尝试列表：详情里的 `attempts`，按 `task_id` 过滤，显示模型、状态、失败原因。
- 验证结果：`results[].verification_layers` 中 `task_id` 匹配的那些。
- 这个节点的事件：从已加载的事件里按 `task_id` 过滤（`mission_events` 本身不支持按任务过滤）。事件没加载完时显示"加载更早的事件"按钮，点一次调一次分页。

**复合任务额外显示"规划决定"**：
- 新动词 `mission_planning_decisions{mission_id}`，写在 `live_graph.py` 里，用同样的只读连接和所有权校验。
- 执行前先核对 `SDK/storage/` 里 `planning_decisions` 和 `planning_requests` 两张表的真实列名，把核对结果补进 `00-数据核对.md`。当前已知 `planning_decisions` 没有 `mission_id`，要通过 `request_id` 关联 `planning_requests` 才能得到 mission，SQL 大致如下：
  ```sql
  SELECT d.decision_id, d.decision_type, d.status, d.rejection_codes_json, d.created_at
    FROM planning_decisions d JOIN planning_requests r ON r.request_id=d.request_id
   WHERE r.mission_id=? ORDER BY d.created_at LIMIT 200;
  ```
  核对后列名以真实表为准。如果没有"目标节点"这一列，就不显示目标节点。
- `rejection_codes` 是枚举代码。前端建中文对照表，找不到中文的显示原代码。
- 注册方式同第 1 步，也要同步更新 `test_handlers_contract.py`。
- **测试**：所有权校验、只输出上面这些列、最多 200 条。

### 第 6 步：总览进度条 + 版本切换

**任务列表进度条**：
- 后端 `service.py` 的任务列表方法 `list_missions(self, *, limit=50)`（`service.py:1270`），每一行加字段 `task_counts: {completed, total}`。
  - 用现有的 `_task_statuses(self, mission_id) -> list[str]`（`service.py:1293`）。
  - 构造每一行时它已被调用一次，先存成变量，再同时用于 `ui_state` 和 `task_counts`。
- 前端每一行显示 5 段进度条，每段的判定：

| 段 | 判定依据 |
|---|---|
| 创建 | `CREATED` |
| 规划 | `PLANNING` |
| 执行 | `ACTIVE`，并在旁边显示 `completed/total` |
| 验证 | `ui_state === "verifying"` |
| 完成 | `COMPLETED` |

- `FAILED` 时，停在当时所在的那一段并变红；`CANCELLED` 时变灰。
- 当前打开的任务里只要有一个节点带"可能卡住"角标，列表里这一行也显示提示。

**版本切换**：
- 执行图顶部放一个版本下拉框，选项来自 `revisions`。
- 选了旧版本就调 `mission_live_graph(revision=N)`，只看结构，节点统一显示灰色，并标注"历史版本，仅结构"。

**不做**："拖动时间轴、按任意时刻着色"的回放。原因：从 seq 找到当时的计划版本需要额外的数据支持，收益也低。以后有需要再单独立项。

## 5. 不做的事

- 不让前端或 Host 按事件自己实现一套任务状态机。
- 不开启执行图内核，不对外开放 `enable_taskgraph_contract`。
- 不把外部追踪系统（Jaeger、Langfuse 等）作为主界面。
- 不做图的编辑或手工调度，这个界面只读。
- 不改 SDK 的表结构；开发期不考虑旧数据兼容。

## 6. 风险

| 风险 | 应对 |
|---|---|
| Host 直接读 SDK 的表，SDK 以后改表会让它失效 | 第 1 步测试用 SDK 自己的建表流程建库，外加真实数据夹具；升级 SDK 时这些测试会先失败 |
| `plan_memberships` 可能只存增量 | 第 0 步先核对，并写明对应的 SQL 改法 |
| pump 多查一次事件，可能锁库 | 只读连接 + `LIMIT 51` + 走 `(mission_id, seq)` 索引；第 2 步测试里测 100 个任务同时变化时单轮耗时 < 200ms |
| 节点多、嵌套深时 elk 布局慢 | 布局放在 Worker 里；超过 200 个节点默认折叠；超过 1 秒显示"正在排版" |
| 出现新的阶段值或状态值 | 显示"未知"并打告警 |
| 同一版本内阶段从 A 变 B 再回到 A 时，SDK 按"节点+版本+阶段"去重，不会再写第二次 A，图上会停在 B | 已知限制，出现几率低。要彻底解决需要 SDK 改事件去重键，本方案不做 |

## 7. 完成标准

- 真实模型跑一个会拆出复合任务的任务：从开始到结束都不用手动刷新；每次状态变化 2 秒内出现在图上。
- 分解过程能看出：拆成了什么、用了什么方法、哪些决定被拒绝了。
- 全量回归合并前后各跑一次都通过；真实鼠标点击验收一次。

**后续项（本方案不含）**：核实"等人处理"类状态存在哪张表，把它加进 pump 的变化判断并补测试，然后删除 5 秒详情轮询。

## 8. 修订记录

**第 2 版（2026-09-25）按独立审查修订：**
- 严格执行图读取接口对真实任务不可用，改成直读分层计划表的运行视图。
- 状态映射改为按 `status` / `phase` 的真实取值来定。
- 推送的事件保留评论摘要。
- 保留 5 秒详情轮询和 15 秒编排状态检查。
- 前端只对当前打开的任务接增量。
- 进度条补上后端字段。
- 回放缩减为"按版本查看结构"。

**第 3 版（2026-09-25）按第二轮复查修订：**
- 父节点改为从 `plan_memberships.instance_id` 关联 `method_instances.goal_occurrence_id` 取（原来的写法会让每个子任务挂在自己下面）。
- 复合阶段改为取 ≤ 当前版本的最新值。
- pump 的事件查询加上 `seq` 上限。
- 前端的追加和补缺口逻辑挪到 `MissionsView` 的推送分支。
- 补上真实的方法名和建库入口。
- 删掉已从代码确认、不用再核对的两项。

两轮审查的其余结论：各表名、列名、类型，只读事务，推送字段，规划决定 SQL，均已核对无误。
