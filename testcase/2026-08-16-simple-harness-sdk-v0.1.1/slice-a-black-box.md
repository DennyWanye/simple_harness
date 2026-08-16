# Slice A black-box oracle — SDK v0.1.1 candidate

> 冻结对象：只从 built wheel 安装的 consumer；不允许源码路径、SDK tests、private import、
> monkeypatch 或产品旧 Harness。

| ID | 输入类 | Root runs | PASS oracle |
|---|---|---:|---|
| SDK-A1 | public no-tool/one-tool/multi-tool Runtime | 2 | public namespace完成三路径；root/child/effect/provider ledger一致；物理调用符合预期 |
| SDK-A2 | 三 official Workflow 与 host-owned definition | 2 | 三官方Profile和一个host definition通过同一SDK Runner；缺Port仅自身不注册；ticket/fingerprint伪造零child |
| SDK-A3 | durable Tool permission | 2 | approve/deny/cancel/duplicate/expired/wrong nonce/restart均确定；未批准物理Tool=0，批准物理Tool≤1 |
| SDK-A4 | delivery/reopen temporal faults | 2 | startup backlog、terminal wake、sink fail、close race、crash after sink均不丢不重复，重开最终settled |
| SDK-A5 | hard budgets与LLM payload变异 | 2 | turns/tool/wall/cost/same-tool五硬停；乱序/duplicate/malformed/oversize/refuse-tool不串单、不无限增长、不伪造 |
| SDK-A6 | reusable conformance host | 1 | wheel无tests/source时CLI+pytest provider/tool/runtime/workflow全执行；required skip/fail/error非零；report schema完整脱敏 |
| SDK-A7 | immutable local candidate | 1 | 两次clean build canonical一致；同SHA在clean macOS ARM64 Python3.11环境完成import、schema reopen与四suite；Windows x64/Linux ARM64同bytes复验移至获授权后的SDK-C7 required release gate |

所有 temporal-fault root 必须从新 SQLite DB 开始，并在物理边界记录独立 call counter。A3/A4/A5
至少一个 root 在持久化边界后强制关闭进程并由新进程 reopen；同进程 retry 不算 restart。
