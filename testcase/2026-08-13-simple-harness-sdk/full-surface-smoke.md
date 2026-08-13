# Full-surface smoke specification

> 目标：昂贵构建/完整真人矩阵前，对公开计划能够推出的每个用户/消费者入口打一枪。未知或未公开的历史入口不得猜测；汇合闸必须接收实现轨生成并审阅的产品公开 surface inventory，逐项合并到本表。inventory 缺失、截断或存在未映射入口时 fail closed，全表面门不得开始，更不能宣称通过。

| Surface | 最小 smoke | PASS 条件 | 方式 |
|---|---|---|---|
| SDK import | clean exact-wheel Python `import simple_harness` | 无副作用、无 forbidden imports | `run-sdk-public-smoke.sh` |
| SDK conformance CLI | public `python -m simple_harness.testing --host ...` | provider/tool/runtime/workflow report 非空且无 required failure | script |
| Runtime lifecycle | public build/start/query/signal/cancel/close | 每个调用有确定返回，close 释放资源 | conformance host |
| OpenAI-compatible Provider | 受控 structured Tool request | 物理请求一次、usage/correlation 可用 | script |
| Tool Registry | 注册并调用 F1 | handler 一次，invalid args 零次 | script |
| Workflow catalog | 枚举三个官方 Profile | ready 的 Profile 默认可见，缺 Port 的仅自身缺席 | script |
| Desktop direct answer | SDK-S1 | 新 root、非空回答、无 child/effect | Computer Use |
| Desktop ordinary Tool | SDK-S2 | 单 Tool、无 Workflow | Computer Use |
| Desktop durable_task | SDK-S3 Run A | ticket-bound child 与可审计交付 | Computer Use |
| Desktop personal_v1 | SDK-S4 Run A | candidate ID + frozen Host binding | Computer Use |
| Desktop capability_build | SDK-S5 Run A | search miss 后受治理 build，完成后启用 | Computer Use |
| Frozen backend | 启动 vendored exact wheel build | module inventory 有 SDK、无旧 authority | script + UI |
| Release/Handoff | clean Linux ARM64 按 Handoff 安装 | exact hash + fake Mobile Host conformance | CI/script |

## 执行顺序

1. exact wheel/import/conformance；红则停止。
2. 将产品公开 surface inventory 与本表合并，给每一项唯一 surface ID；数量与 inventory 完全一致后才能继续。
3. SDK-S1、SDK-S2、SDK-S3 Run A 作为 value smoke；任一无有效业务结果则停止。
4. frozen backend build 与完整 SDK-S1～S5 UI 矩阵。
5. SDK-S6/S7 fault matrix、三平台与 release/Handoff。

所有命令回执、截图、日志和 DB 导出写到 `.local-test-evidence/2026-08-14/simple-harness-sdk/<gate-run-id>/`。
