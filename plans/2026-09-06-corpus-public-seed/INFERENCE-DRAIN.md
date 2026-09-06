# C02/C03 多来源 fixture analysis drain

2026-09-06，后继基线 d2e55fc9。源码准备，**NOT_RUN / 不可作为已完成合入**。
r17 使用资源槽，本叶未运行 pytest、模型、native、build 或安装。

## 已核公共接缝

- `build_human_memory_v7(analysis_delivery_authority=authority)` 在构造时绑定同一对象。
  不能在 manager 已构造后替换 authority。重开使用新的 constructor-bound 对象。
- 已审核 C02/C03 setup 的真实原 USER/ASSISTANT S1、完整 completed group、公开
  strict_atomic mutation receipt 是前提。fixture NO_MUTATION 只承认已提交的原 plan，
  不重新 CREATE，不触碰 job 表、不复制 scoring history、不改变 USER 持久 lineage。
- USER 使用原 group.user_analysis_lineage；原未设置 lineage 的 ASSISTANT job 使用明确
  fixture worker fallback。前者保留的 provider/model 字段是原请求约束，不能解读成
  本次真的调用了该模型。新增 delivery 明确 `deterministic-setup-only`、fixture-local、0 usage。
- 通用 `FixtureSetupExecutor` 仍只支持 C01 CREATE。本叶不把它扩成任意推断编译器；
  多来源 adapter 只处理已有公开 mutation receipt 的 C02-19/C03-20。

## 每 job 与恢复

新增 `corpus_inference_drain.py`：每个 actual claim 绑定完整 subject、Run、disclosure、
单 source ID/hash/ordinal 与 lineage；再次读取原 group、scoring exact pair、公开 plan receipt。
调用真实 `DurableMemoryJobRunner`，公开 repository 协议转发层仅记录原 claim 及真实
`finalize_analysis_application` 返回，所有存储和事务仍为 installed SDK。

必须这样观察：SDK audit_pending 恢复路径可以跳过 executor 和 delivery callback。
不能用 executor 次数或两次 IDLE 证明完成。回读 invocation audit 也在 finalize 之前，
不能把仅有 audit 行当 job APPLIED。

`drain_inference_setup` 返回每源真实 finalize proof、outcomes、confirmed、fixture_executions。
缺任何来源 finalize 返回 `inference_fixture_settlement_unconfirmed`，不补齐 IDLE。
`prior_applied` 是保留的原 public claim/application 对象，重开再次经 SDK idempotent finalize
验证；不是调用方 success flag，也不是新增 ledger。尚无跨进程序列化 checkpoint codec；
丢失原 proof 后不得从空队列推定先前完成。claim 恢复已有 envelope 时必须匹配本 fixture
原 durable delivery，不能接管其他 real-model application。

## 尚未关闭 / 下一批只测新增风险

1. 已准备真实 C03 多来源两 APPLIED、原 lineage、graph不增、评分无 sourceRun/history、
   close/reopen 原 proof 零 executor 复验控制，尚未运行。
2. C02 main 已审 adapter 接线尚未完成；本树未跟踪 C02 两 reference 文件原样保留，
   不纳入本固定交付。C02 完成前不会把 C03 单例宣称两类全部支持。
3. 尚需中途 finalize/取消恢复、foreign source/request/lineage 拒绝、无原 proof IDLE
   不算完成的决定性控制。不得重跑原20 setup或graph旧绿。
4. 当前 bounded 函数是每次至多两次 runner 调用，未提供任意来源批处理或任意旧 job
   扫描能力。失败 claim 保留 SDK lease/backoff 事实，不删 job，不启动生产 worker。

240质量仍0；这些只属于 synthetic fixture setup/隔离控制。
