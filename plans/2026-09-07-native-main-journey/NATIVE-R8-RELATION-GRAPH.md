# 原生 r8：真实模型关系抽取（v6）→ 图谱边；0.6.23 认知向量世代缺陷

2026-09-07 晚，taiwan Mac。bundle `SimpleHarness Memory Verify bbb0b903p18120.app`（Host main bbb0b903，debug 构建记录 `.local-test-evidence/2026-09-07/native-build-r8/artifact.json`），installed H0.7.10 / **M0.6.23** / S0.3.13，隔离 userdata，gpt-5.6-luna 主通道（未回退）。启动 `scripts/native/launch_native_candidate.py`，UI 驱动用 `scripts/native/ax_click.sh` + 前台 raw input。

| 步骤 | 结果 |
|---|---|
| 空目录首启、跳过向导 | startup complete、WeMM 预热就绪，单实例（进程数核对 1） |
| 输入「请记住：以后整理文件就按这两步：先列清单，再复制到备份目录。整理文件时备份目录用外接硬盘。」 | 助手回复复述两步；后台分析真实模型按 v6 协议提案，`memory_outbox.applied` 1 次 |
| 落库形状 | `cognitive_memory_heads` 3 行（semantic claim `user:self · backup_directory_storage · 外接硬盘`、procedure「整理文件」、relation 记忆），`cognitive_relations` 1 行 `applies_to` |
| 记忆面板 | 记忆列表 2 条（relation 不作为节点展示，符合 HM-AC-6） |
| 关系图 | **「2 条记忆 · 1 条关系」**，claim（圆角矩形）→「适用于」→ procedure（六边形）有向边真实渲染（Cytoscape，密集标签分级在 2 节点时为 70 字预算，未触发截断） |
| 退出 | osascript quit，资源 runner parent 0，remaining=[]，峰 1.49 GiB，813s |

结论：**S6 Task 3「关系生成真实性」通过**——真实模型在一次提案内同时产出 claim、procedure 与 applies_to 关系，SDK 校验依赖后落库，图谱出现真实有向边。

## 发现的缺陷（阻塞向量召回原生验证）

分析落库那一刻（12:26:18Z）起，短索引 worker 每个 tick 记录 `memory_short_index_unavailable type=MemoryCorruptionError`（退出前 155 次），`cognitive_vector_generations` 始终为空（此前记忆为空时每分钟正常产生 empty 世代审计行）。判断为 0.6.23 `rebuild_cognitive_vector_generation` 对 relation 类 SEMANTIC head 的处理缺陷（relation 是 edge 不是 node，不应嵌入），且失败未落 failed 行。已交独立实现子代理复现并出 0.6.24 候选；向量召回（同义查询）原生验证改在 r9 做。密集标签（>12 节点）真实截图仍未覆盖。

本轮未保存窗口截图文件（screencapture 无窗口采集权限），图谱结论以 app_screenshot 观察 + DB 行为准。

| 原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/native-bbb0b903/primary-ui-iatnk176/launch.json` | 93acc6df58d979f115cfdb223b7ce1bed48dab1bf5c75e2630796c920634bb5c |
| `.local-test-evidence/2026-09-07/native-bbb0b903/primary-ui-iatnk176/native.log` | 13c11f1bf6744dc4a898239017ad93ff6fc33538c2646ca8b7a2449e9253106c |
| `.local-test-evidence/2026-09-07/native-bbb0b903/resource-r8/resource.json` | 5420d9d27bcd0478cae39002005c1648ddae9fabe44d2e7069c78bb64e0df95e |
| `.local-test-evidence/2026-09-07/native-bbb0b903/primary-ui-iatnk176/userdata/data/human_memory_v7.db` | 9bfd9e3d9dad5a8ab6c3db6e4c417498f80faf9c96eb95ea05ad3d2ac5dfbd6a |
| `.local-test-evidence/2026-09-07/native-build-r8/artifact.json` | d6ac65f66075b2236776920c58c3791c453f179dfc4b52fc0fd2e89a58eb32bb |
