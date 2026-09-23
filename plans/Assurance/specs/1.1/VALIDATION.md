# 本轮实际验证范围

ASSURANCE-EXEC-1.1，2026-09-22。

| 本轮执行 | 结果 | 不证明什么 |
|---|---|---|
| 标准库reference unittest | 168通过，0失败/错误/跳过 | 不证明真实SDK生产来源/接线 |
| reference变异 | 19执行，全部被断言发现 | 不替代16+F SDK mutations |
| Schema meta与独立fixture检查 | 24 schema、23结构正例/23unknown-field反例、6purpose×binding/invocation通过 | 不证明receipt来自真实issuer |
| check_plan资产一致性 | PASS；pointer/展开hash/Ref解析/SQL列/继承MUST/source-map一致 | 不以数量代替全部语义/代码正确性 |
| SQLite反例 | 原3绕过及DELETE/REPLACE/跨Mission/队列竞争反例均拒绝 | 使用reference parent fixture，不是完整SDKmigration |

四视角为作者自审；接收方F01–F15是输入评审，未来独立生产代码审查NOT_RUN。真实Host、当前SDK、Connector、恢复授权入口和模型没有执行；所有对应映射PENDING。

参考测试覆盖局部公式、标签、三值规则、Scope/ref检查、read-set、恢复根授权规则、SQL事务/状态与真实两个SQLite连接/子进程。恢复根规则测试不等于实际t0→t1→t2完整产品restore已经跑过。

本轮未修改用户依赖、未访问用户生产库、未安装候选wheel、未更换模型；jsonschema仅作为本环境额外meta检查，包的运行工具/参考测试只依赖标准库。

原始reference输出保留工作区，不放入Git建议资料；摘要和测试定义随包。查看 review/validation-summary.json、reference-mutations.json、schema-checks.json。交付完整性由DELIVERY-MANIFEST及verify_delivery核对；ZIP解压后还需同样核对。
