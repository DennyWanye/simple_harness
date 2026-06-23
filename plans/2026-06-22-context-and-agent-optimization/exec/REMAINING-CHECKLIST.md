# 上下文+对标优化 plan — 完整"未处理完"清单（2026-06-23）

> 本会话已交付：5 阶段（P0-P3）代码全实现 + 独立子代理评估（P0/P1/P2=100%、P3=7/8）+ ~36 commit。真机 windows-mcp PASS：1B-1 / HM-1 / OH-2 pin / OC-2 / CC-3 / OH-4 / CC-5。
> **OH-4 真测价值留痕**：OH-4 修复过程暴露并修掉 **3 处单测测不到的生产 wiring 死链**（① 出厂 flag 漏配传播 ② curation 计数器跨回合归零 ③ CC-5 auto_learnings 漏传）——"单测全绿但生产全死"，这是本 plan 真测纪律的最高价值产出。
> 本清单只列**未处理完**的，按"性质 + 可完成性"分类，供 review + 后续排期。
> **v2（2026-06-23，经子代理对抗挑战迭代）**：补 1A-4 memory 治理 2 个软性子项（之前漏列）；其余 done/undone 经实读+git+真测文档核验无硬误判。

---

## A. 代码层 — 真正还没做完的（需写代码）

| 项 | 状态 | 缺什么 | 卡点 |
|---|---|---|---|
| **OH-3 高频流 caller 接线** | 🟡 原语+flag+单测 done | manager.write(light=)/put_doc_light 原语就绪，但**无生产调用方**真用 light 路径 | 当前架构无干净的"高频低信息写入点"（接到对话消息会损召回，是错的）；等真有高频流（截屏感知流式入库等）时一行接上。**前瞻扩展点，非缺陷** |

> 注：OH-4 curator 死链（之前列的代码缺口）**已修复 + 真机 PASS**，移出本清单。

---

## B. ON 态 windows-mcp 真测 — 还没跑（代码+单测都 done，OFF=BC 默认态在运行）

### B1 — 可真机跑（值得继续做）
| 项 | 真机怎么验 | 备注 |
|---|---|---|
| **TG-1 goal_task** | 开 `[agent] goal_mode=true` + 设长目标 → 桌宠用 goal_task_create 建带依赖任务 → 重启验持久化 | 需 goal_mode 专项会话 |
| **TG-2 审批聚合面板** | 开面板 flag（默认 enabled=false）+ 并发触发 2+ 权限请求 → 聚合面板批量批准 | 需关 auto_mode 造权限请求 |
| **OH-2 重启持久化** | pin 一条偏好 → 重启 → 下轮 prompt 仍含 📌 偏好（`preference_profile_injected`）| pin 真调已验，仅"重启后仍注入"这步没跑 |

### B2 — 设计使然 companion 模式跑不出（不是没做，是天然不触发）
| 项 | 为什么 companion 跑不出 |
|---|---|
| **1B-2 压缩 toast** | toast 仅压缩真发生时浮；companion 压缩天然 inert（working_messages 结构性小，已诊断 by-design，见 compaction-trigger-diagnosis.md） |
| **1B-3 自适应 compact_at_pct** | 同上，依赖压缩真触发 |
| **1B-4 摘要质量回路** | 需压缩后用户困惑场景，companion 压缩不触发 |
| **1B-5 size-aware microcompact** | 需 microcompact 真触发 |
> 这 4 项要 **code 模式 / 长 agentic 任务**（working_messages 真涨大）才能真机验；companion 单测已兜底。

### B3 — 防御层/模式挡住
| 项 | 卡点 |
|---|---|
| **OC-1 depth 上界** | `_strip_forbidden` 已防子代理嵌套 spawn → 显式 depth 拒绝分支真机几乎走不到（20 单测已验逻辑）|
| **CC-2 plan 只读** | 需 **code 模式** + plan_confirm_gate 挂起窗口内抢跑写类工具才能验 deny（24 单测已验）|

---

## C. 留给用户照做（我无法驱动交互面板）

| 项 | 影响 | 怎么做 |
|---|---|---|
| **1A-2 裁未用 MCP 服务器/插件** | **圈圈满的最大头**（~250 工具+~150 skill 来自自动装 marketplace 插件），预估省 3-8K token | 交互式 `/plugin` 关 Blender/design/chrome 等不用的 + `/mcp` 确认 + **重启会话**。指南见 exec/P0-1A-DONE.md |
| **1A-4 memory 治理（软性子项，部分完成）** | 降 recall 注入量（次要，plan 标软性"可归一组"/"可降级或删"）| ✅ 已做：4 真测记忆合并为 `feedback_real_test_discipline.md`（27→24 条）。**未做**：② 工具稳健性 3 文件（`bash_cwd_venv`/`powershell_chinese_files`/`toolcall_serialization_corruption`）未归组；③ 已 ship 项目记忆（`goal_completion_upgrade`/`backend_orphan_fix`/`self_update_pipeline`）未删/降级。这些是我的 auto-memory，可由我直接做或用 `anthropic-skills:consolidate-memory` 收尾——非硬缺口 |

---

## D. 待决策（需用户拍板才动）

| 项 | 现状 | 决策点 |
|---|---|---|
| **HM-1 verify_gate strict 升级** | 出厂 = `shadow`（安全，全档观测不阻塞）| 升 `strict`（真守门拦截重试）需先真机确认 companion 无 claim 闲聊不被误阻塞 |
| **companion 压缩（选项B）** | 诊断结论 = companion 压缩天然 inert（by-design）| 若要让 companion 也能压上下文 = 独立架构 plan（碰 memory-召回护城河）|

---

## E. follow-up / 旁路产出

| 项 | 状态 |
|---|---|
| **relay (chinzy.com) 故障报告** | ✅ 备好（exec/RELAY-ERROR-REPORT.md，间歇 5xx/504/空 body/streaming 高发 504），待用户转发 relay 项目方 |
| **压缩触发器 companion 不触发** | ✅ 已诊断 = by-design（触发器本身正常，working_messages 结构性小），非 bug |

---

## F. 明确不做（plan E 类，避免过度工程）

| 项 | 为什么不做 |
|---|---|
| **CC-4 通用 hook 层** | DeskPet 运行时无 hook 且不应加（单机桌宠过度工程）；已有 verify-gate end_turn 守门 = Stop-hook 等价物 |
| **OH-1 五路检索第五路 lane** | 决策 no-op：四路 RRF 已实现，提升第五路碰 1200+ 测试、边际收益低，留 backlog |

---

## 一句话总结
**真正还需动手的代码缺口 = 0**（OH-3 是前瞻扩展点，OH-4 已修+真机PASS）。剩下的是：**B1 三项可补的真机真测**（TG-1/TG-2/OH-2 重启）+ **1A-2 裁插件 + 1A-4 memory 软性归并**（用户/我可收尾）+ **2 个待决策**（HM-1 strict / companion 压缩）。B2/B3 是设计/防御层使然真机跑不出，单测已兜底。
