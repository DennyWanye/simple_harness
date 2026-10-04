# HTN 补齐 · 阶段 G · 偏差裁决 2：G-8 第 2 条"写方静态扫描"

- 裁决人：独立偏差裁决子代理（只读；除本文件外没有改任何文件、没有跑测试）。日期 2026-10-04，代码以 `htn-g` `061f1e29` 为准。
- 依据：偏差单 2；偏差裁决 1；施工清单 G-8、G-10；`SDK/storage/source_records.py`、`SDK/storage/store.py`、`SDK/observability/business_replay.py` 与清单 json、`T/product_world/test_business_replay.py`、`T/full_target/test_business_replay_inventory.py`；SDK 与 Host 全部 `sqlite3.connect`。

## 一、事实核对（偏差单所述全部属实）

1. 清单校验（`business_replay.inventory()` 约 :83）只**要求** `writers`、`events` 两栏存在，没有任何代码读它们的内容；记录（`source_records`）、归属、折叠、"无静默改动"检查都不用它们（"本事务有哪些领域事件"取自临时事件触发器，不取清单的 `events`）。`gaps` 已删。
2. `agent_orchestrator` 里能写编排库的连接只有 `storage/store.py`（`Store.open`；另两处是只读打开与备份目标）。其余：`runtime/assembly.py`、`runtime/legacy_provider_slots.py`、`observability/business_replay.py` 为 `mode=ro`；`observability/taskgraph_replay.py` 写的是新建的另一个库；`domains/drone_sim.py`、`evaluation/appworld_operations.py` 是各自的库。没有 `from sqlite3 import connect` 之类的别名，`Store(...)` 也只在 `store.py` 里构造。`simple_harness` 包不碰编排库。Host 打开编排库的三处（`orchestration/pump.py`、`orchestration/manifest.py`、`sdk_adapters/runtime_paths.py`）都是 `mode=ro`。
3. `test_an_out_of_band_write_breaks_the_chain` 已在，证明"绕过存储层的写"会被 v3 报出来。
4. 补充一条偏差单没提的：`replay_scope` 摘要是对清单文件**整个字节**求哈希，所以 `writers`、`events` 这些不参与核对的文字，一改就让旧任务变"范围外"。留着它们不仅要人维护，还会无端牵动范围判定。

## 二、结论：选 B；`writers` 删，`events` 也一并删

**理由**：原文守护的目的是"不让没被记下的写入从后门回来"。偏差裁决 1 以后，是否被记下只看"是不是经存储层的连接写的"，与哪个函数写的无关。所以后门只有一种：在存储层之外另开一个可写连接写编排库。B 守的正是这一点。A 守的是一份不参与任何核对的清单，属于守护没对准目的。C 只靠语料运行时去抓，语料没走到的代码路径就漏掉了。静态扫描约 30 行，成本很低，能补上这块。`writers`、`events` 两栏性质一样：都是没人读、已经过时、却会改动范围摘要的文字。按"同一件事一条路径"（谁写这张表、发什么事件，以代码为准），开发期不留兼容，两栏都直接删。

## 三、要做的改动（等语料跑完、解冻后再动）

1. **清单** `SDK/observability/business_replay_inventory.json`：75 张业务表删掉 `writers`、`events`；`inventory_version` 3→4，`INVENTORY_VERSION` 常量同步。业务表只剩 `class`、`fields`、`rebuild`，可选 `owner`、`silent_ok`、`note`。
2. **清单校验** `business_replay.inventory()`：允许的键去掉 `writers`、`events`，必填只剩 `rebuild`。报错文字改成 `a business table says how it is rebuilt`。`T/full_target/test_business_replay_inventory.py::test_inventory_v2_rules` 里对应的期望文字同步改（这是 G-04 绑的用例，改坏条目的 `original` 若引用这一行要一起改）。
3. **守护用例**：放在 `T/full_target/test_business_replay_inventory.py`，新增 `::test_only_the_store_opens_a_writable_connection`。规则如下：
   - 用 AST 遍历 `src/agent_orchestrator/**/*.py`，找出所有 `sqlite3.connect(...)` 调用；
   - 该调用的源码片段里含 `mode=ro` 的，放行；
   - 否则所在文件必须在允许清单里（清单写在用例里，键是文件，值是理由）：
     - `storage/store.py`：存储层本身，备份目标是副本；
     - `observability/taskgraph_replay.py`：重建到另一个新库；
     - `domains/drone_sim.py`：无人机模拟自己的库；
     - `evaluation/appworld_operations.py`：AppWorld 评测自己的库；
   - 允许清单里的每个文件必须仍含至少一处不带 `mode=ro` 的 `connect`，否则判清单过时、失败。开发期不留没用的条目。
   - **不扫**：Host（只经 SDK 读，三处都是只读；万一有越界写，v3 链检查照样报）；"拿到存储层连接却在事务外写"（偏差裁决 1 第 5 条已交给链检查）；SQL 语句文本（与机制无关）。
4. **不新增其它用例**。已有的 `test_an_out_of_band_write_breaks_the_chain` 负责运行时那一半。
5. 改了清单，范围摘要会变。只重跑 `test_business_replay_inventory.py`、`T/product_world/test_business_replay.py`，以及 G-7 列过的命令行 `replay` 用例和 Host `test_mission_diagnostics.py`，不跑整目录。

## 四、改坏检验（新增 G-25，写进 `T/acceptance_assets/mutations.json`）

`SDK/runtime/assembly.py` 约 :387：把 `database.resolve().as_uri() + "?mode=ro"` 改成 `database.resolve().as_uri()`，也就是一个不在允许清单里的可写连接。应该变红的是 `T/full_target/test_business_replay_inventory.py::test_only_the_store_opens_a_writable_connection`。

## 五、要改的文字

- **施工清单 G-8 第 2 条**改为："守护：清单里每张业务表都有 `rebuild`（清单第 4 版删去 `writers`、`events` 两栏）；静态扫描'编排库的可写连接只来自存储层'（`agent_orchestrator` 里不带 `mode=ro` 的 `sqlite3.connect` 只许出现在允许清单的四个文件里），与'绕过存储层改一行 → 链断'用例一起，堵住'没被记下的写入'从后门回来（偏差裁决 2）。"
- **施工清单 G-8 第 3 条**："G-01～G-18"改为"全部 G 条目（到 G-25；G-08、G-12、G-18 在改坏表里注明无可达触发）"。
- **主计划**正文没有提到写方扫描，不用改正文。收尾升版时，修订记录里加半句："G-8 写方扫描改为'可写连接只来自存储层'，清单删 `writers`、`events` 两栏、升第 4 版（`HTN补齐-阶段G-偏差裁决-2.md`）。"
- **实施记录"阶段 G"**里记一行，引用本裁决。

## 六、需要问用户的

无。这是技术口径，不碰范围和产品决定。
