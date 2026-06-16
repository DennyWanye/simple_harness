# Plans 索引

> DeskPet 所有功能规划 / PRD / 路线图的目录。每个 `plans/<日期-主题>/` 文件夹是一个
> 独立工作项（PRD/PLAN/ROADMAP + 子文档）。本索引一页看清"有哪些规划、各在干什么"。
>
> - 状态：✅ 已落地 · 🟡 进行中 · 📋 规划/参考
> - 落地的功能状态以 [`STATUS/status.md`](../STATUS/status.md) 为准；本索引只做导航。
> - `archive/`、`manual-results-*`、`fun-*`、`test-*` 为归档/手测记录/一次性产物，不在下表。

## 按时间倒序

| 工作项 | 主文档 | 一句话 | 状态 |
|---|---|---|---|
| **compaction-bestpractice-upgrade** | [00-PLAN](2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md) | 上下文压缩升级对标 Claude Code/Hermes/OpenClaw：单调级联(microcompact→结构化摘要→截断兜底)+剩余 token buffer 触发+pre-flush 防丢任务+目标钉死不可压+接已有 L1/L3 记忆；做完才默认开 compaction | 📋 |
| **effective-llm-model-resolution** | [00-PLAN](2026-06-16-effective-llm-model-resolution/00-PLAN.md) | 根治 P-B：让读模型名的代码(压缩窗口/stub/ppt复审)统一读**运行时有效模型**(llm_runtime.json 覆盖的 gpt-5.5)而非 config 旧种子(gemma)；加 effective_llm_model 访问器 + raw 同步 | 📋 |
| **context-compaction-optim** | [00-PLAN](2026-06-16-context-compaction-optim/00-PLAN.md) | 上下文压缩四方向优化(任务保活结构化摘要+token 统一+观测)；真机测揪出并修"压缩永不触发"核心 bug + 防摘要反射 prompt；留 P-B 窗口取错模型 follow-up | 🟡 |
| **crawl4ai-fetch-tier** | [00-PLAN](2026-06-16-crawl4ai-fetch-tier/00-PLAN.md) | deep-research 抓取加一级真浏览器渲染兜底(治 JS/SPA 空壳站)；POC 后**主线转 Tauri 自带 WebView**(三端零体积零安装),CDP-系统Edge 为 Win 加速档,Crawl4AI 降 dev 高级档 | 📋 |
| **research-reranker** | [00-PLAN](2026-06-14-research-reranker/00-PLAN.md) | deep-research 召回后精排：默认 LLM 重排(gpt-4.1-mini,免下载)+本地 bge-reranker 可选 | 🟡 |
| **deep-search-best-practices** | [00-ROADMAP](2026-06-14-deep-search-best-practices/00-ROADMAP.md) | 不接付费 API 的深搜最佳实践调研 + 升级路线图(SearXNG/Jina Reader/reranker/一手源) | 📋 |
| **deep-research-v8** | [00-PLAN](2026-06-13-deep-research-v8/00-PLAN.md) | 搜索+deep-research 升级到 DeepResearch V8(分层打分/反思迭代/源质量过滤/落报告) | ✅ |
| **pet-interactions-tier1** | [00-plan](2026-06-13-pet-interactions-tier1/00-plan.md) | 桌宠互动 Tier1 | 🟡 |
| **mesh-engine-s3** | [00-PRD](2026-06-13-mesh-engine-s3/00-PRD.md) | 3D mesh engine S3 | 📋 |
| **ppt-beauty-mainline** | [00-PLAN](2026-06-09-ppt-beauty-mainline/00-PLAN.md) | 精美 PPT 主线(A 模板填充 + B AI 整页生图 + 视觉评估闭环) | ✅ |
| **nsis-model-externalization** | [PLAN](2026-06-05-nsis-model-externalization/PLAN.md) | 模型外置瘦包(NSIS 304MB)+首启 COS 下载+发布流水线 | ✅ |
| **goal-completion-upgrade** | [00-PLAN](2026-06-04-goal-completion-upgrade/00-PLAN.md) | 目标完成度升级 FP-1~FP-5(持久化/抗漂移/自纠错/记忆人格/技能自创) | ✅ |
| **voice-msgpanel-sync** | [00-fix-spec](2026-06-03-voice-msgpanel-sync/00-fix-spec.md) | 语音/消息面板同步修复 | ✅ |
| **superpowers-code-workflow** | [05-LOCKED-spec](2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md) | Code 模式工作流纪律(superpowers 全套) | ✅ |
| **live2d-rewrite** | [00-PRD](2026-05-28-live2d-rewrite/00-PRD.md) | Live2D 重写 | 🟡 |
| **pet-animation-ux(-v2)** | [v1](2026-05-24-pet-animation-ux/) · [v2](2026-05-25-pet-animation-ux-v2/) | 桌宠动画 UX | ✅/🟡 |
| **companion-code-skill-upgrade** | [00-PRD](2026-05-25-companion-code-skill-upgrade/00-PRD.md) | Slash 命令 + /goal + 多 agent v2 | ✅ |
| **tool-layer-optimization-v3** | [00-PRD](2026-05-24-tool-layer-optimization-v3/00-PRD.md) | 工具层优化 v3(toolset 门控/dangerous 白名单/disabled) | ✅ |
| **tool-last-mile-upgrade** | [00-PRD](2026-05-23-tool-last-mile-upgrade/00-PRD.md) | 工具调用 last-mile 升级(artifact/verify-gate/receipt) | ✅ |
| **memory-system-stage2** | [00-PRD](2026-05-23-memory-system-stage2/00-PRD.md) | 长期记忆 Stage 2 | ✅ |
| **memory-system-upgrade** | [00-PRD](2026-05-22-memory-system-upgrade/00-PRD.md) | 长期记忆 v2 升级 | ✅ |
| **relay-login-integration** | [00-PRD](2026-05-22-relay-login-integration/00-PRD.md) | 中转站登录集成 | ✅ |

## 其它目录
- `archive/` — 已归档工作项
- `manual-results-*` — 真机 windows-mcp 手测记录（截图 + 报告）
- `fun-*` / `test-*` — 一次性诊断脚本 / 临时产物
