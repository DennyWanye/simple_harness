# Plan challenge 第 1 轮

## 致命问题

1. AC-SRV-1 漏掉 `ContextCompressor` 的生产全局 fallback，也没有明确“已启动 Root 的 immutable provider route 优先于后来变化的 Session binding”。
2. Provider Registry 没有 generation/change event；删除 provider 后会清除 Session binding，使 stale 绑定伪装成 unbound；普通 breaker 也不能保证冷态 20 个并发 402 只有一个物理请求。
3. Context Usage 只描述 reducer 类型，没有 materialized durable authority、producer lineage、迁移与旧历史全量重建方案。
4. Public read model 没有闭合 workflow.db 与 state.db 的事实合成、水位和缺源语义，也只脱敏 tool，漏掉 provider input/output/policy。
5. stale binding 的旧测试明确断言 global fallback；行为契约尚未逐条获批，也没有冻结 black-box oracle。

## 真架构问题

- 模型路由 authority 分散在 Session binding、Root snapshot、长期 callback、compactor 和 Registry 生命周期中；必须收归“immutable Root route + typed workload envelope + registry revision/incarnation”。
- 用户运行视图跨 workflow ledger、provider ledger、SessionDB；必须建立跨 store 的 public read service 和 provider/tool 双 default-deny projection，不能继续由前端拼 raw payload。

## 次要问题

- `RootOutcomeView` 需要精确定义 blocked 与 waiting。
- 未发现经典“异步注册未就绪后永久缓存默认值”竞态；需解决的是运行期 registry mutation 与 breaker revision 原子性。

## 未覆盖 AC

- AC-SRV-1、AC-SRV-2、AC-SRV-3、AC-SRV-6、AC-SRV-7。

VERDICT: FAIL
