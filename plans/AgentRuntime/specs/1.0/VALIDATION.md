# 本轮实际验证与边界

日期2026-09-22。以下由本轮容器实际执行，不是未来测试清单。

| 范围 | 结果 |
|---|---|
| 标准库 unittest | 116项，0失败、0错误、0跳过 |
| 参考规则定点变异 | 10项全部被目标测试的行为断言发现；不把语法/导入错误当kill |
| 单一Schema | 32份定义（含公共Pin/Json）；31份结构正例、31份未知字段负例通过 |
| 字段源表 | 330个字段pointer/subtree hash与正式Schema一致 |
| 可选jsonschema交叉检查 | 当前容器已安装，31份样例通过；不是用户项目必需依赖 |
| SQL fixture | 13张execution side表、5张session普通表＋2个FTS虚表；FK、状态/不可变、FTS、回滚通过 |
| 并发/故障参考 | 两个SQLite连接CAS；子进程提交前/后立即退出并恢复，通过 |
| 文件交付 | 完整manifest/hash，最终ZIP重新解压后复核并重跑116项 |

SQL父表为最小键契约fixture，未运行真实SDK完整schema/迁移runner。token测试以已给定安全charge为输入，未调用真实tokenizer/embedding/model，不证明512K实测或检索质量。路径alias模拟在Linux执行，不冒充macOS或Windows原生验证。独立审阅者未调用，CHALLENGE-REPORT是作者四视角自审。

`implementation/sdk-cases.json`的60组与16项SDK mutation仍PENDING_SDK_EXECUTION。没有读取当前全部dirty候选；没有安装Host/SDK、修改项目依赖、打开生产或TaskGraph门禁。真实能力默认ON发生在完整实现/规定验收通过后的同一交付，而不是此参考包PASS。

计数与环境见 reports/REFERENCE-RESULTS.json；参考变异见 reports/reference-mutations.json。
