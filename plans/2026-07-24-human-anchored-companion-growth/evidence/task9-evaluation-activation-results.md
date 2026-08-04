# Task 9 评测、风险与激活执行结果

> 日期：2026-07-25
> 状态：durable foundation 与 fail-closed platform 边界通过自动化

## 已完成

- `StaticRiskPreflight` 从 Task 8 exact validated package 读取 immutable facts，不加载、
  import 或运行 candidate code。
- checked-in evaluation suites/resources、冻结只读 memory snapshot、逐 case durable
  launch/ACK/cleanup/recovery、只读结果工具和报告提交已进入唯一 Companion Store。
- `CapabilityRiskPolicy` 从 manifest、effect、permission、code/hook facts 作确定性分类；
  safe-auto permit 只允许评测，不携带 activation authority。
- `CapabilityActivationSaga`、activation guard 与 Store API 覆盖 low-risk auto、人工确认、
  rollback、disable、崩溃恢复和 immutable lineage。
- 可执行激活需要 host-issued decision proof、exact package/code digest 与
  `activation_risk_ack=persistent_local_code_no_os_sandbox`。通用 Auto、TaskGrant 和
  lifecycle permission 都不能替代。
- `CompanionActivationPlatform` 是唯一 Manager/Runtime façade。legacy Manager 尚无纯静态
  `prepare_installed_static()` seam 时，返回 `static_lifecycle_prepare_unavailable`，
  不启动 runtime、不做 health check、不换 binding。

## 自动化与进程证据

- Companion 全量：`335 passed`，覆盖 evaluation policy/execution/read tools/suites、
  immutable risk facts、risk policy、activation saga/guard/platform。
- Capability 全量：`249 passed`。
- activation platform 相邻组合：`15 passed`、`20 passed`、`6 passed`。
- 上述受监控全量树均 `survivor=0`；Capability 释放 private memory 982175744 bytes。

## 明确边界

Task 9 不用现有 monolithic install/update API 假装“静态准备”。专用 Manager lifecycle seam
与真实 native Job/MCP instance adapter 由 Task 13 接入；接口缺失期间保持 fail closed 是
预期生产行为，不是灰度或默认关闭。
