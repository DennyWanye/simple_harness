# Plan Challenge：T11/T12 定向回炉 Round 2

> 结果：`VERDICT: FAIL`  
> 范围：只读审计 `architecture-baseline.md`、`plan.md`、`acceptance.md`、`v6-contracts.md`、`HANDOFF.md`、`execution-results.md`、`AGENTS.md`，并对照 `service.py`、`launcher.py`、`store/research_repository.py`、`store/schema.py`、`retention.py` 与 registered blob/checkpoint ownership 实现。  
> 判定标准：新增 §6.3/T12 必须能在当前生产边界中无二义地编码、迁移、故障注入、恢复和写出独立测试 oracle；任一 P0 continuation/recovery/retention 语义仍需编码者临场决定即 FAIL。

## 结论

§6.3 已经修正 retention 文件路径，并冻结 UUIDv5 namespace、十个事务 fault point、server-side closure 校验顺序、notify 后恢复方向及 900 秒目标；但仍有三项 Blocker 和一项 High。当前不能进入 T12 实现，否则最容易在“已有 head 但 pin 已过期”、900 秒 deadline 持久化字段、以及 lineage retention 三处重新产生互相不兼容的实现。

## Findings

### 1. Blocker — service/repository 调用顺序仍不能实现“已有 head 优先，即使 pin 过期也返回 canonical child”

**证据**

- `plan.md:519-520` 要求 service 从 server-side terminal manifest 取得 snapshot；`plan.md:546-547` 又要求 `create_child_from_snapshot_v6()` 先查 parent head，已有 head 时不因 pin 过期创建 sibling。
- `v6-contracts.md:308-310` 冻结了 repository 事务、notify 恢复和 closure 校验，但没有冻结 service→repository 的 v6 API 形状，也没有说明“已有 head”分支是否必须在 terminal event/pin/snapshot load 之前短路。
- 当前生产调用顺序是 `service.py:1104-1137` 先 `resolve_continuation_source()` 并加载 snapshot，再到 `service.py:1176-1192` 创建 child；当前 resolver 在 `research_repository.py:734-750` 会先因 snapshot/pin 过期失败，永远到不了 child/head 返回路径。
- 当前 repository 只支持 v5：`research_repository.py:710-723` 明确要求 completed `v5`，而 service 仍以 caller idempotency key 派生 child：`service.py:1128-1130`、`service.py:1184-1187`。

**精确改法**

在 `v6-contracts.md` §6.3 与 T12 冻结一个唯一跨层入口，禁止继续复用“先 resolve、后 create”的两步 TOCTOU：

```python
create_or_get_continuation_v6(
    *,
    parent_run_id: str,
    caller_idempotency_key: str,  # audit only
) -> ContinuationCreateResultV1
```

该 repository API 在同一个 `BEGIN IMMEDIATE` 中必须按以下顺序执行：

1. 由 `parent_run_id` 计算全部 UUIDv5 identity，查询 `workflow_research_continuation_heads`；
2. head 存在：验证 head→child run→child lineage→start operation/request→session refs 的 canonical identity，**不读取或重新要求有效 parent pin**，返回 `created=false`；
3. head 不存在：再执行 parent/version/terminal event/manifest/snapshot/pin/exact closure/checkpoint-owner 校验和十个 write points；
4. 返回内部已持久化的 `start_payload`（或仅 `child_run_id`，由 launcher 从 capability snapshot 读取），service 不再把客户端/预读 snapshot refs 传入 repository；
5. service 对 `created=true|false` 都调用同一 `launcher.launch_existing_run(child_run_id, persisted_start_payload)`/dispatcher notify。

同时冻结 loader/decoder owner：terminal manifest 与 snapshot bytes 由注入的 `RegisteredBlobStore`/v6 continuation decoder读取，并要求 manifest ref、snapshot ref及 `closure_refs` 都能通过 `(parent_run_id, terminal_checkpoint_id)` owner join；不能只验证全局 digest 存在。

必须新增 oracle：先创建 child，令 parent pin/snapshot expiry 过期，再用不同 idempotency key 调用；返回相同 child、`created=false`、notify 发生一次，且没有第二次 snapshot/closure validation 依赖。

### 2. Blocker — 900 秒契约引入了不存在的字段，且 30 秒 settle 的截断公式未冻结

**证据**

- `v6-contracts.md:124-196` 的 `DurableDeadlineV1` exact keys 与 schema owner只有 `created_at`、`last_observed_at`、`wall_not_after` 等字段。
- 当前 schema 也只有这些列：`store/schema.py:497-518`；当前 identity 已由 `deadlines.py:139-163` 按 owner/scope/policy/parent 稳定派生，实例字段见 `deadlines.py:166-180`。
- 新增 `v6-contracts.md:312` 却要求持久化 `automatic_started_at`、`automatic_wall_not_after`；这两个字段不在 exact-key dataclass、表或 migration 中。编码者无法判断是新增 schema v5、扩展 schema v4，还是映射到现有字段。
- 同一行只写“30 秒不能延长 wall guard”，没有冻结 `accepted` 靠近 900 秒时 `settle_deadline` 的计算和 900 秒到点后的 control terminal reason；`acceptance.md:122-123` 要求 settle/replay 有确定性结果。

**精确改法**

不要再发明两个新字段；在 §6.3 明确复用现有 `DurableDeadlineV1`：

- root automatic deadline：`owner_id=run_id`、`logical_scope='run:automatic'`、固定 registered policy hash、`budget_ms=remaining_ms=900000`、`created_at=last_observed_at=route_checkpoint_wall`、`wall_not_after=created_at+900`、`parent_deadline_id=null`；
- `deadline_id=deadline_id_for(owner_id=run_id, logical_scope='run:automatic', policy_hash=..., parent_deadline_id=None)`，create-or-load 时逐字段核对 immutable values；禁止新 wall 值覆盖；
- control `settle_deadline=min(accepted_at+30.0, root.wall_not_after)`；若 accepted 时 root 已过期则不得接受新控制；若 settle 被 wall guard 截断，稳定 terminal reason=`wall_guard`，随后按已提交 outcome 进入 partial/insufficient；
- continuation child 使用自己的 `run_id` 产生新 root deadline；parent deadline/effect reservation不得复制。

测试必须用固定 wall clock 断言 accepted at `T+895` 时 settle deadline 恰为 `T+900`，accepted at/after `T+900` fail closed；retry/resume/restart 读取相同 `deadline_id/created_at/wall_not_after`，不能仅断言“总耗时小于等于 900”。

### 3. Blocker — retention 把 head/lineage 同时叫作 root 和删除对象，缺少可达性闭包及无泄漏删除判据

**证据**

- `v6-contracts.md:314` 说 reachability roots 包括 continuation head、parent/child lineage，同时又要求删除完整可删 lineage 时先删 head；若“所有 head/lineage row”都是无条件 root，则它们永远不可删。
- continuation-head 表没有 `expires_at`，只有 `claimed_at`：`store/schema.py:564-590`；因此不能由表自身推断何时 head 脱离 root。
- 当前 repository 的 root 集只含 nonterminal/control/live pin，再沿 child→parent lineage 扩张：`research_repository.py:1214-1241`；snapshot 只从 protected run/pin派生：`research_repository.py:1242-1269`。它既没有 continuation-head 双向保护，也没有 snapshot blob 解码后的 spec/fact/provenance/policy closure。
- 当前 retention 顺序先删 checkpoint/blob refs，再删 lineage/snapshot/run：`retention.py:265-301`，与 §6.3 要求的 `head -> owners/pins -> lineage -> snapshot -> blobs/spec -> run` 不是同一事务顺序。
- schema 的 lineage 自引用 `parent_operation_id ... ON DELETE RESTRICT`：`store/schema.py:406-428`，因此 child→grandchild 链必须后序删除 child/grandchild relation；“lineage refs”一词不足以决定合法 SQL 顺序。

**精确改法**

在 §6.3 冻结以下算法，而不是把所有 head/lineage 声明为 root：

1. 初始 run roots 仅为 nonterminal、terminal-retention 窗口内、open control、required delivery 非 terminal、有效 snapshot pin；
2. 对每个 `continuation_head(parent,child)` 做无向 component closure：任一 endpoint 为 root，则 parent、child、对应 lineage/head/snapshot 和 snapshot exact closure 全部 protected；这样 parent 仍保留时不会删 head/child，child 保留时也不会悬空 parent；
3. snapshot closure 只能由 strict v6 decoder 读取 `manifest_ref` bare digest并重算 semantic hash；missing/corrupt/owner mismatch 时 retention fail closed 并保护该 component，不得当 orphan 删除；
4. 只有一个 component 中无任何初始 root、live delivery/control/pin/checkpoint owner 时才可删。单个 `BEGIN IMMEDIATE` 内后序执行：所有 descendant heads → inherited child staging/checkpoint blob refs与 pins → descendant lineage/start operations/start requests/session refs → snapshot rows → orphaned blob refs/blobs → child runs → root run；每一步后做 FK/reference-count oracle，任一 fault rollback；
5. head 不独立设置 TTL；它与 parent run同事务删除，从而“parent 存在但 head 已删、再次 continue 产生 sibling”不可发生。

必须补三组独立 oracle：parent→child、parent→child→grandchild、同 blob 被另一个 retained checkpoint/delivery引用；逐步推进 retention clock，断言组件保护/后序删除/FK 无残留，且每个 delete point fault 后数据库字节级集合不变。

### 4. High — UUID 算法可实现，但缺固定 golden vector；允许 continuation delta 又把它排除 identity，race 语义冲突

**证据**

- `v6-contracts.md:306` 冻结了 namespace 和派生公式，`plan.md:553` 要求 UUID/request/logical-slot oracle；但计划没有提供任何固定输入→固定输出常量，测试若调用同一 production helper计算 expected，会自证。
- §6.3 同一段说 caller idempotency key 不进入 payload hash，而 `v6-contracts.md:310` 又允许 continuation natural-language delta；当前 action 明确拒绝任何 payload：`service.py:1092-1097`。若将 delta 放入 start payload，两个不同 delta 会共享同一 child UUID，却产生不同 request/capability hash；当前 immutable检查在 `research_repository.py:918-936` 会冲突。
- 当前 child `logical_slot` 实际取 request key：`research_repository.py:985-990`，但 §6.3 没有明确写 `logical_slot=child_request_key`。

**精确改法**

- T12/v6 policy v1 明确 continuation action payload 必须为空；自然语言范围/目标变化走新 root/spec revision，不进入 single-head continuation。删除 §6.3 “允许 natural-language delta”，避免 first-writer-wins 隐含产品语义。若产品必须保留 delta，则必须另行冻结：首个 committed delta 为 canonical、后续不同 delta 返回现有 child且不改 payload，并把 delta audit hash纳入返回结果但不影响 child identity。
- 明确 `logical_slot=child_request_key`，start operation ID固定为 `child_operation_id+':start'`，start payload exact keys/hash也加入 contract。
- 增加独立 golden vector（expected 常量不得调用 production helper）：parent=`11111111-1111-1111-1111-111111111111` 时 namespace=`a7e5f48a-9b2b-549f-a67c-3ebdee7b9c78`、canonical name=`{"parent_run_id":"11111111-1111-1111-1111-111111111111","policy_version":1}`、child=`2c2bba0d-c87b-5e70-ba41-1bf66ade02ff`、turn=`7a7859966d71596fa9ab7d17b75526bc`、trace=`12f427c8b6a85a5aa12ccdde03333ece`、thread=`b072c692df6c5afdb875d5b84f28250d`、budget=`5c544596151f5b6b8266a8055e774b1b`。再用两个 caller keys断言全部 child/start/logical-slot identity完全相同，只有 audit command identity不同。

## 已确认无问题的部分

- retention 实现路径已正确冻结为 `backend/deskpet/workflows/retention.py`（`plan.md:551`、`v6-contracts.md:314`）。
- UUID namespace 声明值与 `uuid5(NAMESPACE_URL, 'https://deskpet.local/workflow/deep-research/v6/continuation')` 一致。
- continuation-head SQL 的 parent/child/run/lineage/snapshot/spec FK方向足以承载 single-head CAS；`BEGIN IMMEDIATE` 加 `parent_run_id PRIMARY KEY` 可以阻止两个 connection 产生 sibling，前提是 Finding 1 的唯一事务入口落地。
- 十个 write/fault points 已有稳定名称，notify 位于事务外且 created-run scan方向与 `launcher.py:387-426`、`launcher.py:538-562` 的现有 recovery能力相容。
- `manifest_ref` 对 v6 保存 bare snapshot blob digest、domain 才 format wire ref的边界已经明确。

## 收敛条件

上述 3 个 Blocker 与 1 个 High 必须回写 `plan.md`/`v6-contracts.md` 后再进行下一轮 challenge。下一轮只需增量核对：唯一 create-or-get API与 existing-head短路、900 秒现有字段映射、retention component算法/事务后序，以及固定 UUID vector和空 payload政策。

VERDICT: FAIL
