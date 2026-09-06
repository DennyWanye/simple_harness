# Rich Episode 首叶实际结果

2026-09-06。执行源30fcc261（077447c7首实现；Dirac建议fresh来源/replay补强后固定），两次只读均无阻止新方法执行的P0/P1。
唯一新增方法1 passed in 0.48s；PGID45170 exit0，峰72096KiB、耗时0.658s，remaining_group_members=[]/cleanup_error=null。
新版默认资源入口实际磁盘准入1024MiB/停止256MiB，min_disk_free_mib3360。槽已释放。

真实installed H073/M0613公开链：原完整Episode rich JSON→S1 admission/span→conversation scope registration→
CREATE→typed recall→close/reopen exact零读取重放→fresh来源相同召回。实际结果SENSITIVE、cross_scope=true、rich-source-task。
一个方法含真实首叶和六字段泄露谓词、错误ID、legacy错误PASS共八反例；不是九个pytest tests。
六字段检查在wire hash检查前，目的是独立泄露谓词，不是产品实际泄露red，也不伪称全wire自洽攻击。

原fixture/hash未改，actual payload/hash与label→actual ID映射在adapter观察对象内单列并经oracle检查。
本轮未导出逐项观察JSON：raw只有命令/源码身份/pytest日志/资源记录；不得宣称已有独立正式矩阵Run或raw逐字段复核。
原Episode literal仍BLOCKED，另四类rich setup仍待实现；strict DTO并不包含canary，本片证明完整S1 rich输入不进入minimal public payload。
无矩阵计数变化，不合旧批为401完成，无SDK产品/wire/API新增。

命令：既有installed M0613 artifact/venv/bin/python，经默认scripts/run_resource_bounded.py --rss-mib 2048 --seconds 180，
child `-m pytest testcase/human-memory-program/tests/test_typed_recall_rich_source.py -q -p no:cacheprovider`。
无模型/native/build/install，旧绿未重跑。ignored raw根：`.local-test-evidence/2026-09-06/rich-source/`。

| 相对路径 | SHA256 |
|---|---|
| tests-r1/command.json | c671471d507448fcedef779d08c1d86e15c0e1faa9e49580294aae56e76f2411 |
| tests-r1/source.txt | e802b673cd144b73cf148b51fde65097b8fd5e6cdb89dc30053efb3d4a817504 |
| tests-r1/resource/command.log | 0c33791aa03cbc8826ba04efd1b062adb3baa875c47625e4de7090bd753d7635 |
| tests-r1/resource/resource.json | c729c042d7d215ca6d3829f31c9707640e81faa06f7a9033d8df2d2428613941 |
