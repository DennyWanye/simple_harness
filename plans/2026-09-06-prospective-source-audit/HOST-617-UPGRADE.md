# Host Memory 7.3 公共升级接线

2026-09-06：Dirac已完成本叶源码及所列分批实际结果限定复审ACCEPT；不重跑旧绿，不外推本叶未验的组合或native。

2026-09-06。HumanMemoryV7Runtime在实际默认builder之前依次调用SDK提供的7.0/7.1→7.2、7.2→7.3公开升级接口；每阶段独立backup路径和独立receipt字段。未知catalog仍交公开分类拒绝，仅MemoryLegacySchemaUnsupported允许继续探测后继/新库builder；不读取SDK私有schema、删除库或改写旧receipt。注入的测试backend_factory不做迁移。

本次使用真实installed M616子进程创建并登记属主，再用H076/M617已安装组合及Host Runtime升级。升级后属主公共回执逐字段/hash保持，7.3backup hash匹配公开升级receipt；重开复用同owner receipt/upgrade receipt，backup未改。没有在当前native userdata上升级。

r1：2 PASS（原空文件恢复、未知库拒绝两个受影响邻居），1新测试FAIL / 0.55s；失败因测试误调PrincipalRegistrationReceipt.to_json，实际公共类型是dataclass。修正为asdict并将子进程结果单独保存为JSON，避免manager关闭日志混入结果；产品不改。r2只该新用例1 PASS / 0.58s，其余2绿不重跑。三项唯一场景分批通过，无Provider/模型/native。

资源r1 PG71476 exit1/remaining=[] elapsed1.296s，r2 PG71498 exit0/remaining=[] elapsed1.297s，均cleanup_error=null；r2峰值159664KiB、最低磁盘3945MiB。遗留子进程与新运行均由原共享资源组管理。

本叶不代表not_required consumer完成或所有旧版本逐一重新验收；7.0/7.1升级链沿已审SDK公开能力，当前新增跨版本实际输入为M616/7.2。

|本机证据相对路径|SHA256|
|---|---|
|.local-test-evidence/2026-09-06/host617-upgrade/r1/command.log|227de30f9710f12b4381d7e2f2cdb5bbfb7b786df0b66e5fe0b3009f6c8c6bf6|
|.local-test-evidence/2026-09-06/host617-upgrade/r1/resource.json|817f82ba8655e6beb84b6f1c9242b856cdef8d5b8d4f6f9001cce65122a61eee|
|.local-test-evidence/2026-09-06/host617-upgrade/r2/command.log|8afe2471606046b386cbefd7224d475f9d94ddb804d914ca3a5b3472c14d8f5c|
|.local-test-evidence/2026-09-06/host617-upgrade/r2/resource.json|1248970c2db9b0ef336aa4fc5aa410fd8c33a33295bce2f82810b509dc961587|
|.local-test-evidence/2026-09-06/host617-upgrade/r2/identity.json|6c1eeb521729089f1456ba9be31fc18068a940340057fbd1637a030625023425|
|.local-test-evidence/2026-09-06/host617-upgrade/retry.py|6097e6c23b3343d28cd66fa9744a18d3f047eb38dac77a1c1fed0fae07821e1b|
