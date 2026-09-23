# ARP-EXEC-1.1.1 实际验证边界

日期2026-09-23；作者参考环境 Python 3.13.5 / SQLite 3.46.1。不沿用旧包76项结果。本报告不是用户本机SDK、原生UI、模型或迁移的验收。

| 检查 | 本轮实际结果 | 范围 |
|---|---|---|
| 原1.1定点复现 | 三个原问题复现 | 未修改原包；详见reports/original-1.1-probes.json |
| R1–R5窄测试 | 32 PASS / 0失败 / 0错误 / 0跳过 | 临时SQLite持久化/重开、临时目录rename、字段真值、参考composer/分页/身份 |
| 全包标准库参考测试 | 109 PASS / 0失败 / 0错误 / 0跳过 | 原有参考回归及本次新增；参考工作，不是产品suite |
| 原参考定点变异 | 10/10 KILLED_BY_ASSERTION | 语法/导入错误不计kill；RM07改由非重复的HistoryReadPage分支证明cursor门 |
| 本次R参考变异 | 5/5 KILLED_BY_ASSERTION | destroy latch、状态真值、highwater、stable key、硬上限 |
| Schema结构 | 101定义 / 101正例 / 99额外字段负例通过 | jsonschema只在作者环境附加验证，用户无需安装；跨字段规则另由codec测试 |
| 资产核对 | Schema/字段hash/引用/错误/当前计划路径一致 | 不等于来源真实性核验 |
| 生产要求继承 | 原80场景和原16 mutation保持 | 仅4处assets字段改到新版主文档；Given/When/Then未减 |

R1选定保持PURGING并记录typed blocker，因此原来的PURGING→QUARANTINED仍应被SQL拒绝；关闭证据是新合法恢复路径可持久化、普通rebuild不能复活，而不是强行让原UPDATE通过。

R3的参考composer经内部协调与两页扫描取得后页候选；JSON序列化重建保留原query调用key、冻结行与排除组。call port是受控测试替身，没有真实embedding调用或费用发生，不能据此声称实际SDK已无重复付费。集成测试明确要求真实原Store/调用账本/SessionSearchService/原composer。

未执行：实际dirty SDK读源/开发/安装、完整migration、SDK87场景/原16+新增5mutation、stateful、Host/native、真实计量/embedding/512K模型、独立代码审查。对应状态全部PENDING/NOT_RUN。当前只关闭本轮规格与参考反例，未更新ARCHITECTURE功能完成度。

完整ZIP解压后会按同一命令再次核验文件hash、资产一致性与参考suite，结果在交付答复中如实报告；ZIP所列SHA仅证明交付字节完整，不证明产品行为。
