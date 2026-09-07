# 提醒状态库启动兼容

更新：2026-09-06。候选源码 `e21f6e47` 与后继 `88e9106b`；独立审查对 `88e9106b` 限定 ACCEPT。当前仅数据库启动入口验收，完整 A7 提醒链和原生 schema52 重启尚未验收。

应用 lifespan 的 `dispatch_startup_epoch` 和后续通用数据库初始化，现在对已知 50/51/52 逐版调用原有完整校验器，核验 bootstrap、迁移链/SQL 哈希、真实 DDL、恢复注册与围栏、旧游标前缀。新库仍由原入口建到49，提醒组件负责自身事务升级；不修改旧 SQL、不把任意高版本视为可用、不修补或回退损坏库。

新增负控实际发现第二处问题：通用 initializer 原先只在 bootstrap 值正确时才检查 human 版本，标记缺失会绕过检查。后继按已有库的实际版本决定检查，缺失标记的52库与未知53库都拒绝，文件不变。

## 实际结果

共9个唯一新增场景分批通过：非空注册的50/51/52经真实 startup dispatcher、generic opener、human initializer重开两次，数据和版本原字节不变；迁移标记、DDL、围栏、bootstrap损坏分别拒绝且不修复；未知53拒绝；fresh保持49并不自动安装未完成的提醒功能。

- r1：7 PASS / 2 FAIL，5.69秒。两失败发生在故障注入夹具，被 append-only 约束阻止。
- r2：2 PASS / 1 FAIL，1.80秒。迁移标记负控首次通过；bootstrap夹具又被 CHECK 阻止。`-k marker or bootstrap` 还匹配了名称含 markers 的未知版本用例，误重跑1项旧绿，不计新增。
- r3：1 FAIL / 8 deselected，0.79秒。夹具改为模拟缺失标记后，实际证实 generic opener 未拒绝的产品缺陷。
- r4：修复后只跑 bootstrap，1 PASS / 8 deselected，0.74秒。其余已绿用例未再重跑。

测试使用现有 primary-m0615 Python和 H077/M617 installed overlay，真实SQLite/Host入口，不加载模型、不启动应用、不安装环境。PG97849/97975/98056/98127均退出且 remaining=[]、cleanup_error=null，共享锁释放；最后最低磁盘5141MiB。原生 r12 的49库短查 PASS 不替代本片52原生重启。

命令：`<primary>/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python scripts/run_resource_bounded.py --evidence-dir <本根>/rN -- <同Python> -I -B <本根>/run_controls.py <本根>/rN [选择参数]`。默认2048MiB/180秒/共享锁；r1无选择，r2 `-k 'marker or bootstrap'`，r3/r4 `-k bootstrap`。

## 本地证据

全部原始文件留在 ignored `.local-test-evidence/2026-09-06/composed-startup/`，未提交日志或数据库。

| 文件 | SHA-256 |
|---|---|
| run_controls.py | 120ad6ef69f59017cf15fbdc10a6825da0c812af641668a84c416235e10f9fef |
| r1/command.log | d99a41152e4a9285ff14ed4fed78503ee5574819a259e64993d4bdaedbd85373 |
| r1/resource.json | 3ac7c7a3ad938d3601daaa41b75fa66919a57b5efc040bf64aedc358c946b2ab |
| r2/command.log | 830991036218f0941bb925306c8c5b11f250fcadd1833921c2cba5dcecbef9c4 |
| r2/resource.json | 6a9fb594fd53a67b858e15c7c7d9bd1f569fbe14f8682af8ab195b9feccf2ffe |
| r3/command.log | 03ee7546f6550dc445070712ff2af7e43a7150c3834ae7e98dd20a5f0e814681 |
| r3/resource.json | 4106a5cfbe3e5af6c64960c60896c3ecc875eacce9b3a2ab9c8531d5ce21e0ef |
| r4/command.log | 59549a8ba885276afea1cd707a6567fb8a95396451a22fd4813f446f5fb67024 |
| r4/resource.json | 4b4d2c912c9479b7e97011c5f2139e1e84333146a43ec6b171afc949f7577abd |
