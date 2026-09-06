# H075/M616 提醒来源与实际短期使用组合

最后更新：2026-09-06。组合 Host typed-use `fe9e3660`（业务 `756d13d1`）、提醒公开来源 `b4f51024`（业务 `8a91f371`），候选 H0.7.5/M0.6.16/S0.3.13。合并冲突仅 ARCHITECTURE、pyproject、uv.lock；保留两支功能和历史结论，准确更新新 pin。执行库使用 SDK 官方 schema9 迁移；Host 默认 schema49，未开启未完成的 S5c scheduler。

仅执行必要组合，没有重复完整叶子集合：

| 批次 | 实际结果 |
| --- | --- |
| r1 | 3 PASS、2 FAIL，10.17秒。新组合的 empty assistant 六消息索引/遗忘、真实 short page/grant/physical send、候选消费者事实源通过；两项安装身份因复用 agent target 的 direct_url 路径不同失败。 |
| r2 | 从主 vendor offline 安装 H075/M616 到单个5.6MiB target，仅重跑两项失败身份检查：2 PASS，0.03秒。未放宽 direct_url/版本/hash 校验，未新建完整venv，未重跑前三项。 |

r1 导入之前已核的两个 SDK target；r2 用主候选新 target。wheel 字节未改变；二者均用隔离 Python，Host 源码显式输入。r1 是功能证据，r2 是当前安装来源证据，不声称 r2 重跑功能。PG48766/48830 全部回收，峰395360/135760KiB，最低磁盘3002MiB。无真实远端 Provider、模型、native、401或240执行。

H075 source `abbb0fd707f2ceadb271471da2ef906c27748420`，wheel `7969a2e5028f2c5d0b348973a5b330bca797f10c1bdfa2532ae033d352d2ee66`；M616 source `931b8c77076bb5b42ad41a3297ed4eb58bcaaab9`，wheel `00937eb5d79c1ea989112c658eaf543e434fb211106f4edfbecbc10002bca9bf`。两 SDK 已独审；Host 新恢复修复的最后限定复核待归档。

命令入口：本树 `scripts/run_resource_bounded.py --evidence-dir <batch> -- <primary-m0615 Python> -I -B <script> <batch>`。r1 为 `.local-test-evidence/2026-09-06/primary-075616/run_combined.py`（原两agent target），r2 为同目录 `install_consumer.py`（主target）。当前 run_combined 已指向主target；r1 原错误与实际来源由下列原始日志/identity保留。

证据仅在本机 `.local-test-evidence/2026-09-06/primary-075616/`：

| 相对路径 | SHA-256 |
| --- | --- |
| r1/command.log | 2d2be4c952e012639b9ee9861e86d57b34e6177e3dfc5c8cd2c6c1834e097f85 |
| r1/resource.json | 17b13f05beb179e6187d72ec71f89a768b9ed443227655476366df8dde6fda68 |
| r1/identity.json | e5437e9d31c344b7c9f6193b06bdf4b915aa499532b08cf0e3a62a5276f1ecd1 |
| r2/command.log | fdf5266464bbfa0986dc079aac621b610ad61274a54e62cff3a7ac095d95706b |
| r2/resource.json | d65f1d2d42582368b29326f96492f157d489e1d88236db7580b94f2bfe7353be |
| r2/identity.json | 0e2cc152fda507c176548e4462a64a1b918e4ebe5cb885fda06cc2e65e8227c7 |

原程序继续：唯一 scheduler、signal 派生来源、occurrence/ack、Host 持久接收新 SDK 观察事件、非SELF输入许可、401剩余执行器、240真实质量与新native验收尚未完成；不以本组合绿替代这些功能。
