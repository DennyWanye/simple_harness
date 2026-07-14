# Windows 真机验收结果

> 日期：2026-07-14～2026-07-15
> 方式：Tauri 源码启动 + Windows Computer Use 真点击/真输入 + backend 日志
> 判定：**PASS**

## 启动环境

- Tauri 自行启动唯一 Vite 与 backend；没有手动占用 8100/5173。
- 注入 `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend` 与仓库 `.venv` Python，日志确认运行源码 backend，而不是旧 bundled exe。
- 使用隔离 `DESKPET_USER_DATA_DIR`，复用已经登录的 OS keychain 会话；本轮没有输入、读取或变更账号密码。
- 启动日志确认 `search_gateway_ready enabled=True providers=['baidu','duckduckgo','google-cdp','bing-cdp']`。
- 窗口恢复在 Xiaomi 屏幕；桌宠与独立消息窗口均保持“已连接”。

原始日志、PID 与隔离 userdata 留在本机证据目录，不纳入提交，避免提交会话数据库和本机元数据。本报告只记录可安全复核的 run id、状态和验收信号。

## 用例结果

| TC | 判定 | 真实 UI 与日志证据 |
|---|---|---|
| W01 快速搜索 | PASS | 在普通会话真输入“请搜索并告诉我 Python 官方最新稳定版本，给出至少三个来源链接”；日志为 `task_type='web_search'` 且真实调用 `web_search`。UI 返回 Python 3.14.6，并给出 python.org 下载页、3.14.6 release 页、3.14 What's New 三个官方来源。 |
| W02 逐步可视化 | PASS | run `753bb05bf72142908e5a750aa84d628b` 完成 13/13。总体卡始终可见；阶段默认折叠；点击聚焦后 Space 展开、Enter 折叠；展开后 13 个阶段按序显示且不遮挡最终报告。 |
| W03 重启恢复 | PASS | 在 11/13 `cite` 运行中精确关闭并重启 Tauri。旧 lease grace 到期后同一 run 被 reclaim，`lease_epoch 1→3`，没有创建新 run；最终恢复并完成 13/13，总体卡和阶段历史重建。 |
| W04 最终交付 | PASS | 第一份报告含 12 条引用、Coverage、Degraded/Error Summary，并生成 3.3 KB Markdown 卡。历史重载不重复；点击“打开”触发 Windows 打开方式，点击“在文件夹中显示”真实打开资源管理器并选中文件。修复实时广播后，run `12f4fca62dac42ed8bb199fe8de8e841` 完成时无需重载即在 UI 出现 `0s 前`、3.9 KB Markdown 卡。 |
| W05 双 run 隔离 | PASS | 同一 `default` 会话先后运行 `753bb05b…` 与 `0e3d02f…`。第一张保持 13/13 completed；第二张独立推进并以 13/13 failed/no_results 结束，拥有自己的 4 条 warning、错误摘要、500 B Markdown 产物和终态，没有覆盖第一张的进度、引用或终态。 |

## 真实 DeepResearch 结果

### 完成 run A：重启恢复

- run：`753bb05bf72142908e5a750aa84d628b`
- 终态：`completed`，13/13，耗时约 6 分 28 秒
- 检索/抓取：16 candidates；18 attempted / 15 succeeded / 3 dropped
- 证据：6 independent domains，12 passages，13 claims
- 引用：12 citations；3 supported / 41 unsupported；support rate 0.068
- Artifact：3,297 report bytes，文件卡与打开/定位动作通过

### 完成 run B：实时 Artifact 广播

- run：`12f4fca62dac42ed8bb199fe8de8e841`
- 终态：`completed`，13/13，耗时约 4 分 31 秒
- 检索/抓取：12 candidates；18 attempted / 14 succeeded / 4 dropped
- 证据：8 independent domains，12 passages，17 claims
- 引用：12 citations；5 supported / 41 unsupported；support rate 0.109
- Artifact：3,899 report bytes；消息窗口未重载，实时卡立即出现

### 隔离 run：可解释失败

- run：`0e3d02f3139b4aafa3acf826bd010a46`
- 终态：`failed/no_results`，但仍完成 13 阶段并交付错误摘要与 Markdown 文件
- 原因：连续运行触发 provider cooldown/timeout，0 个 claim 达到支持阈值；产品没有用模型预训练知识伪造结论
- 该结果用于验证多 run 的诊断和失败终态隔离，不替代上面两个成功 run 的 N03 证据

## 执行中发现并闭环的产品缺陷

1. 旧会话中断的 assistant tool call 与 orphan tool result 会破坏 ContextSegment：已增加安全历史投影，保留数据库原记录但移除不完整协议字段。
2. 含 `Python` 的明确“搜索”请求被 code 关键词抢先分类：已把显式 web lookup 意图提前，真实 W01 重新走 Gateway。
3. WebView 内联 `display:grid` 覆盖 `hidden`，且展开列表发生 flex shrink 重叠：已显式 `display:none/grid` 并锁定 `flexShrink:0`。
4. “隐藏工具消息”误隐藏 Artifact：现在只隐藏执行轨迹，成功产物卡始终可见。
5. durable workflow Artifact 只落 SessionDB、不实时广播：增加持久化后的 `tool_result` 广播与前端 event-id 去重；第三个真实 run 已验证无需重载即时出现。

## 补充观察

- 连续 DeepResearch 会暴露 Search Gateway cooldown/timeout，系统会明确 degraded 或 no_results，不会伪造支持结论；后续可单独优化突发查询容量与 provider 健康策略。
- backend 日志仍可见辅助 FactExtractor 的旧 relay key 401 warning，但本轮普通聊天、Gateway 与多个 native DeepResearch run 均可真实执行；该 warning 未阻断本计划路径。

## 固定多类别基准补测

为关闭 AC-OBS-02，本轮又通过同一个真实消息框执行固定三类样本，原始指标见 `deepresearch-live-benchmark.json`：

| 类别 | run | 终态 | 耗时 | 独立域 | 引用 | support rate |
|---|---|---:|---:|---:|---:|---:|
| 官方技术文档 | `12f4fca62dac42ed8bb199fe8de8e841` | completed | 270.548s | 8 | 12 | 0.109 |
| 中国现行政策 | `e3f1a6a4d64b4b6680c95e76f1288c1a` | completed | 226.795s | 12 | 12 | 0.750 |
| Web 标准动态信息 | `d087a50e20bd4e01864a7194b89d5b58` | failed/no_results | 15.250s | 0 | 0 | 0.000 |

- 固定集完成率 2/3（66.7%），nearest-rank P95 为 270.548s；失败样本保留在全部分母中。
- 政策报告真实抓取国务院、网信办、司法部等来源，最终 32 个解析论断中 24 个获得支持，生成 13,280-byte Markdown Artifact；界面未刷新即出现文件卡。
- WebGPU 样本被持久化 `search:cooldown` 拦截，两次都诚实交付 no-results 报告。这证明失败语义可靠，同时暴露了下一轮应优先修复的突发查询容量问题。
- `synth_stage_claim_count` 是撰写阶段产出的初始论断数；Coverage 中的 claim/support 计数来自最终解析与引用核验，两者统计时点不同，不应混写。

## 最终投递修复真机信号

- 修复后的新 run 使用 `workflow.final_assistant`，只生成 `session_message` 与 `websocket` 两条 delivery，不再被 checkpointer 自动派生错误 receipt。
- 兼容处理器已把三个历史 assistant receipt delivery 收敛为 delivered，未伪造 completion evidence。
- 补测结束时 `workflow_deliveries WHERE status != 'delivered'` 为 0。
