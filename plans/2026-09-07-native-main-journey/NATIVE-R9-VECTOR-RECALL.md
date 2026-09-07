# 原生 r9：Memory 0.6.24 向量召回（同义零词面重叠）与 relation 世代修复验证

2026-09-07 深夜，taiwan Mac。bundle `SimpleHarness Memory Verify 70c5ef89p18120.app`（Host main 70c5ef89），installed H0.7.10 / **M0.6.24** / S0.3.13，隔离 userdata，gpt-5.6-luna 主通道未回退。UI 驱动：System Events（`ax_click.sh` + 设置文本框 value），computer-use 授权在本轮被拒绝（用户点了拒绝），因此无截图，全部以 SDK 库表、Host 日志与资源记录为证据。

| 步骤 | 结果 |
|---|---|
| 首启 + 「请记住：以后整理文件就按这两步…备份目录用外接硬盘」 | 真实模型 v6 提案落库：3 head（claim `backup_directory_device=外接硬盘`、procedure、relation）+ `cognitive_relations` 1 行 applies_to |
| 0.6.24 世代重建 | `cognitive_vector_generations` 立即 active、`vector_count=2`（relation 被跳过），**`memory_short_index_unavailable` 全程 0 次**（r8 为 155 次）；后续每次记忆变化都重建：退出时 6 retired + 1 active，35 向量 |
| 同义提问（有词面重叠）「整理资料的时候，副本应该存到哪种硬件上？」 | 模型未路由，直接由近期上下文回答「副本存到外接硬盘上」——符合动态 Context 设计（事实仍在最近 10 个因果组内） |
| 12 轮无关填充对话 | 全部正常回答，把原偏好挤出近期窗口 |
| 零词面重叠提问「把资料搬去存档之前，我定过要用什么介质保存副本？按我以前说过的答。」 | 模型主动 context_route：typed plan `retrieval_modes=[full_text, vector]`、types semantic/episode/procedure；召回 8 项，其中 **long_term_typed 语义记忆 `backup_directory_device=外接硬盘`（第 2 位）**，退化码 `[]`；模型改写查询「用户以前是否约定过：…副本要用什么介质保存？」经 `typed_recall_query_terms` 38 个词项与该记忆 payload **0 个词面命中**——命中只能来自向量通道。终答「你定的是用**外接硬盘**保存副本。」 |
| 退出 | osascript quit，资源 runner parent 0，remaining=[]，峰 2.89 GiB，2317s |

结论：
- **HM-AC-3/4 中文同义召回在原生真实模型下通过**（0.6.24 认知向量通道），且 r8 的 relation 世代缺陷已修（零告警）。
- 限制：同一召回里短时域 lane 也返回了含答案的近期问答 chunk（turn 2/3 的回答），模型终答可能同时依据两者；但认知记忆项本身的命中已由零词面重叠证明。更严格的隔离（冷重启后仅长期记忆）留待 r10。
- 图谱边在 0.6.24 下仍为 1 条（`cognitive_relations` 1 行）；未截图。

| 原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/native-70c5ef89/primary-ui-9gbpm518/launch.json` | 85aac47ae17cf87391e045b3830ea921fa2005347cefa554ea4364a3fdd463b4 |
| `.local-test-evidence/2026-09-07/native-70c5ef89/primary-ui-9gbpm518/native.log` | 8a7e89f76a41272070dd200952f7e9bd7702b577adcd7ec42799988e8ffe0ad3 |
| `.local-test-evidence/2026-09-07/native-70c5ef89/resource-r9/resource.json` | 5204038c2cadfae90a05f59e4ae1c5eece3acba16f29900db9a488b8f2f3a100 |
| `.local-test-evidence/2026-09-07/native-70c5ef89/primary-ui-9gbpm518/userdata/data/human_memory_v7.db` | c4c4eae7e1110ec990c1143cc4729838ff379170bc5bb30f37bce014c008eb72 |
