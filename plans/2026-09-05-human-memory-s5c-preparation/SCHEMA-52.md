# Host schema 52：明确类型的 outbox 游标

最后更新：2026-09-06。仅迁移及正常注册游标接线闭合，not_required SDK 回执消费、完整 scheduler 与默认组合尚未完成。

Host 恢复注册表本身不可变，因此不重建旧游标表来冒充原列身份。新建 prospective_outbox_cursor_v52，两个外键分别指向真实注册记录和独立 prospective_invalidation_terminals，约束恰好一项非空；旧游标七列值及 hash 链原样复制，旧表、DDL 和 recovery columns_json 保留并封闭旧表插入。新版 Store 显式使用新版游标，迁移前已打开的旧 writer 失败，不能写进无人消费的旧队列。

原 schema50/51 SQL、migration hash 和旧注册回执均不改。52 在同一写事务验证旧链、复制、注册新恢复表/触发器并发布版本；事务前后中断均保留真实边界，未知版本、旧链损坏、缺失封闭触发器不自动修复。

本轮 5 项新检查通过 / 2.50s：实际 Store 生成的非空旧注册链迁移、旧 writer 拒绝/新 Store 继续、幂等重开；提交前后故障两项；旧链损坏拒绝；空目标游标及封闭触发器缺失拒绝。测试使用已安装 H075/M616 与原有共享 Python，未新建环境，未重跑 schema51 旧集合。PG68334 exit0、remaining=[]、cleanup_error=null，elapsed2.997s、峰值162160KiB，最低磁盘2956MiB。固定源码 f6e70fc2 已获 Dirac 限定 ACCEPT，无本增量 P0/P1；并发发布分支仅源码审查，未计入动态测试覆盖。

尚未声称新的 not_required 回执实际写入或消费通过；独立 terminal 表当前只提供存储契约，后继 Memory 0.6.17 公共 DTO 与 Host source/consumer 仍在实施。不是 SDK ACK，也未生成伪造 authority。

## 证据

原始文件保持本地 ignored。

|相对路径|SHA256|
|---|---|
|.local-test-evidence/2026-09-06/terminal52/r1/command.log|d6b6d74e87b542efb889b70892fceea647fa51890a46897913aa56842c3a9288|
|.local-test-evidence/2026-09-06/terminal52/r1/resource.json|ba7d8581c0d79ae1eee7fcacc6a7b874d794b0de3ef3b74ee8307526c9a3fdbf|
