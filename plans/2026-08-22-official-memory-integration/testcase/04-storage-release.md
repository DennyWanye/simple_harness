# Storage、embedding 与发布身份黑盒用例

## SDK-S01 — 有界召回、分页与支持的并发边界

绑定：AC-5, AC-7；TO-05, TO-R3。类型：automated scale/concurrency。

1. 通过公开批量 fixture 写入小库与至少放大两个数量级的大库，混合多个 identity/scope；执行相同
   recall 请求并记录候选数、返回数、bytes、duration 与公开 query diagnostics。
   - 预期：结果、候选、bytes、deadline 始终受硬上限约束；大库不会把全部 messages/vectors 加载到进程；
     正确 personal/family 结果仍可分页取得。
2. 在同一个 manager 内并发执行多 actor 的读写、checkpoint 与 recall。
   - 预期：无丢失、半事务或串数据；busy 只产生有界重试/稳定错误，不挂死主任务。
3. 尝试让第二 active writer owner 同时写同一 DB。
   - 预期：在不承诺的多 writer 环境 fail fast，返回稳定 capability/error，不依赖偶然成功。

主证据：规模参数、公开 bounds/diagnostics、RSS 或等价进程观测、并发结果矩阵、稳定错误码。

## SDK-S02 — production embedder、lineage drift、reindex 与离线约束

绑定：AC-5；TO-05。类型：automated production configuration。

1. production 模式不提供 embedder，或提供缺失本地资源的 embedder。
   - 预期：初始化 fail fast，错误可行动；网络观察证明运行时没有下载模型。
2. 用明确 provider/model/revision/dimension/normalization/format 建索引并重启。
   - 预期：相同 lineage 正常召回；公开 status 可核对完整 identity。
3. 逐项改变 lineage 后直接召回。
   - 预期：明确 lexical degrade 或 drift error；绝不静默混用旧向量。
4. 执行显式 reindex，并分别在完成前中止与完整完成。
   - 预期：中止仍使用完整旧 generation；完整验证后一次切换到新 generation，不出现混合结果。

主证据：初始化结果、网络零请求记录、lineage/status、旧/新 generation recall digests。

## SDK-S03 — backup/restore、corruption 与关闭语义

绑定：AC-5, AC-7；TO-05, TO-07。类型：automated operability。

1. 在 WAL 活跃且有并发读的受支持状态下调用公开 online backup；继续写入后关闭 manager。
   - 预期：backup 是一致 snapshot，源库仍可用；close 后无悬挂 worker/连接。
2. 在 manager closed 状态将 backup 恢复到隔离目标并重新打开。
   - 预期：schema、integrity、FK、lineage 校验通过；恢复内容等于 backup 时点而非后续写入。
3. 分别损坏 backup、schema、FK 与 lineage 后尝试恢复。
   - 预期：原目标保持不变；返回稳定 corruption/validation 错误且不泄漏路径或内容。

主证据：backup/restore receipts、时点前后 exports、目标 hash、corruption error scan、worker shutdown probe。

## SDK-M01 — simple_harness v3→v4 协调升级与回滚

绑定：AC-6, AC-7, AC-8；TO-R5。类型：automated migration/change-risk。

1. 从隔离的 v3 fixture 准备 completed pair、suppressed terminal、仍在运行的 deferred turn，以及完整、
   缺失、歧义三类可信 identity mapping；在 runtime 关闭状态启动公开产品升级流程。
   - 预期：唯一映射的完整输入可升级；缺失/歧义、未知 source、重复 source 或内容 hash 不符时整体停止，
     原两个数据库保持可用且不被部分替换。
2. 对成功升级核对 `KEEP_COMPLETED_PAIR`、`SUPPRESS_TENTATIVE`、`SUPPRESS_TERMINAL`、
   `DEFERRED_TURN` 四类外部结果；构造 root + 多 continuation，确认只有因果绑定最终 terminal 的最后
   user+assistant 被保留，更早 tentative user 及其派生数据被抑制。
   - assistant `continuation_id=NULL` 时仍须由持久 event/receipt/claim sequence唯一确定最终user；歧义数据
     必须fail closed，不能用时间戳猜测。
   - 对已迁移非终态run，再通过公开continuation入口连续提交两条新user输入；每次新输入原子supersede前一
     active cursor，最终completed只将最后user与assistant写成一pair。分别在cursor更新前/后、terminal前/后
     crash重启；failed/cancelled保持零pair且无旧user复活。
   - 预期：completed 形成完整单一 pair；suppressed 不出现；deferred 在后来成功完成时形成一个正常 v4
     committed Turn，失败/取消时为零，不出现 tentative user 或半 pair。
3. 分别在两个临时库生成后、第一次 swap 后、第二次 swap 前后终止协调器，再重启产品。
   - 预期：启动只选择完整旧 pair 或完整新 pair；mixed pair 自动恢复，绝不交给 runtime。
4. 令 installed-origin smoke 失败并执行 rollback。
   - 预期：消费者 pin 与两个数据库都回到切换前已验证状态；用户既有 eligible 内容不丢、不重复；
     candidate bytes/tag 不被覆盖修改。

主证据：公开 migration manifest digest、升级/回滚 receipt、前后 export hashes、每个 crash phase 启动结果。

## SDK-R01 — exact wheels、跨版本 conformance 与 release identity

绑定：AC-1, AC-4, AC-8；TO-01, TO-04, TO-08, TO-R4。类型：automated release gate。

1. 在互相隔离的 clean Python 3.11、3.12、3.13 环境安装 Harness exact wheel 与 Memory `[harness]`
   exact wheel；禁止 editable/path import，并核对 installed origin。
   - 预期：三个环境均可 standalone import Memory；联合安装无循环依赖；官方极简组合可完成一轮。
2. 运行未来消费者 fixture：只提供 identity、产品 ports 与 `MemoryManager`，执行 personal/family matrix、
   `memory=None` 和 borrowed/runtime ownership。
   - 预期：无需产品 Adapter 或内部 query/sink；所有环境结果一致。
3. 对照 version、tag、wheel bytes、SHA256SUMS、BUILD_INFO、README、Quickstart、API、Integration Status、
   changelog 与 simple_harness exact pin。
   - 预期：全部指向同一 candidate/released bytes；任一篡改或旧 wheel 均使 verifier fail closed。
4. 从任一环境执行最小 diagnostics。
   - 预期：可核对版本/origin/hash，但不输出凭据、Memory 内容或本机敏感路径。

主证据：三版本 install transcripts、origin/hash verifier、conformance matrix、文档/制品对账表。
