# 实际验证与边界

ARP-EXEC-1.1，2026-09-23。本次是计划与参考附件校验，不是本地产品实施。

| 已执行 | 结果 |
|---|---|
| 标准库参考 unittest | **76 tests / 0 failure / 0 error / 0 skip** |
| 参考实现定点变异 | **10/10 KILLED_BY_ASSERTION**；导入/语法失败不算 |
| JSON Schema 官方库结构检查 | Draft 2020-12 根结构及89种示例通过 |
| 示例语义/类型 | 89份synthetic形状及本包context-free规则通过，不是实际授权/回执 |
| 额外字段负例 | 87个object根负例拒绝 |
| 生成资产 | 1755递归field pointer/hash、44Pin resolver、20裁定、原60断言继承、新20场景通过 |
| 完整SQL | 21 execution side表、9个session普通表、2个FTS5虚表在参考父契约通过；17命名查询EXPLAIN通过 |

另有两个实际SQLite连接的版本竞争、事务回滚、同span跨索引代、原closed group源冲突、短中文fallback、大数f32、长Turn完整N、未完成扫描不发布排名等参考反例。

这些检查只覆盖明确实现的参考规则和fixture；不保证全部业务合同自动被Schema或测试证明。类型系统不能证明真实caller、有效grant、真实call或部署模板一致，须生产resolver/原UOW与实际验收。

**未执行**：当前dirty SDK、TaskGraph23/Assurance后继完整源映射、原SDK migration runner及完整schema库、80场景生产集成、原16 SDK mutation、真实embedding/512K模型、Host原生UI、Linux/Windows产品环境、独立子代理代码审查。对应状态全部PENDING/NOT_RUN。旧116 PASS不沿用。

本机已有jsonschema仅用于作者附加核对；交付轻量工具和unittest没有新增第三方依赖。WorkAgent不得为跑参考tests擅自改项目lock。实际SDK采用项目已有严格codec；需要PyYAML/embedding依赖时走正常已授权依赖流程，记录制品，不混作本次已安装。

交付文件校验：DELIVERY-MANIFEST列除自身外的完整文件与SHA-256，manifest不递归哈希自身。ZIP解压后使用同工具与同套参考tests复核；最终汇报不得把文件完整性写成产品完成。

四视角挑战是作者自审。参见review/CHALLENGE-REPORT.md和reports/reference-mutations.json。
