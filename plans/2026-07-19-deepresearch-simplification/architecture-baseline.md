# DeepResearch 简化编排：架构基线

> 本文前半段记录 2026-07-19 v7 实施前的历史基线；当前生产事实以文末
> “2026-07-20 增量校准”与 `ARCHITECTURE/DeepResearch.md` 顶部为准。

## 实施前主要矛盾（历史）

当前生产默认 `deep_research/v6` 把普通调研也装入 11 个串行 evidence/assessment/terminal 节点和多套专题契约；仓库同时保留了一条更贴近用户目标的成熟 fan-out core，但它只在 legacy blocking adapter 中启用，且对子结果只做一次 gather，失败后直接丢弃。主要矛盾不是缺少搜索、抓取或报告能力，而是**简单的“拆题—并行子调研—诊断续跑—统一综合”没有成为 durable 默认编排**。

## 实施前生产调用链（历史，解剖麻雀）

1. `deepresearch` 工具入口由 `backend/deskpet/tools/research_tools.py:_handle_deepresearch` 接收；workflow starter 已接线时立即转入 durable launcher。
2. `backend/main.py:4292-4369` 读取 `[workflows].deep_research_version`，当前 factory/default config 都是 `v6`，并通过 `WorkflowLauncher.launch()` 创建 durable run。
3. `backend/main.py:3995-4065` 为 v6 构造 blob/effect/repository/search/semantic/LLM runtime；`backend/main.py:4212-4228` 注册 v6 adapter 和 continuation/terminal extension。
4. `backend/deskpet/workflows/definitions/v6/deep_research.py:103-185` 声明固定 11 节点串行图：`compile_spec → plan_route → load_pages → extract_candidate_bundles → admit_facts → synthesize_inferences → register_inferences → assess_answer → render_claims → integrity → persist_manifest`。
5. 终态通过 v6 terminal manifest 投影到 Session、WebSocket 和 Artifact；旧 v1-v5 继续注册，只负责历史/恢复兼容。

## 实施前已存在但未成为默认的简化能力（历史）

- `backend/deskpet/workflows/definitions/research_core.py:1172-1241` 已有 `run_research_core()`：先 plan，再在 scheduler 存在、深度为 root、子问题达到阈值时进入 fan-out。
- `backend/deskpet/tools/research_tools.py:1340-1506` 已有 `_run_subagent_fanout()`：按 research lane 有界并行运行每个子问题、收集子报告并统一 synthesis。
- `backend/deskpet/agent/subagent_scheduler.py` 已提供全局 + research lane 双 semaphore、queued/running/completed/failed 进度和取消传播。
- `backend/deskpet/tools/research_tools.py:1299-1337` 已有统一报告综合和引用重编号逻辑，可作为 DeepResearch 报告模板的直接基线。

## 实施前缺口（历史）

1. fan-out 只在 legacy core 中启用；v6 新 run 不走这条主 Agent/子代理链。
2. `_run_subagent_fanout()` 对异常、无引用或无有效正文的子结果只记 failed/completed，不做质量分类、主 Agent 诊断或定向续跑。
3. 子方向 attempt 没有稳定的 attempt/status/diagnosis 结构，最终综合无法明确区分有效结果和重试耗尽的不足项。
4. legacy fan-out 的 durable 边界是整个外层 workflow node；若直接复用，需要确保 attempt 结果进入 checkpoint/effect 或至少在节点完成后形成可恢复快照。
5. `ARCHITECTURE/ARCHITECTURE.md` 在本轮前仍写 v5 默认，与 `config.toml:669`、`backend/config.py:23-65,601-607` 和 v6 注册事实冲突；本轮已先校准。

## 当时的最小目标结构

```text
deepresearch ingress
  -> durable deep_research/v7
  -> decompose (主 Agent：2-6 子方向)
  -> research_children (有界并行；逐 child 评估、诊断、总共最多 2 次尝试)
  -> synthesize (只使用 valid child + 明示不足项)
  -> finalize (唯一 report / final assistant / artifact)
```

## 当时的复用与边界判断

- 复用通用 workflow runner、checkpoint、progress、outbox、Search Gateway、legacy research core、SubagentScheduler、引用合并与报告落盘，不重写基础设施。
- 新增 immutable `v7`，不原地修改 v6 manifest；v1-v6 保持可恢复。
- v7 的业务主链只有拆题、子调研、综合和交付；搜索/抓取/评分留在每个 research 子代理内部，避免再把所有内部步骤膨胀为主图节点。
- spike 首先验证真实正向报告质量和 retry/insufficient 语义，再决定是否迁移 v6 的 continuation 高级控制；本次不得为了兼容所有 v6 专用控制重新复制其复杂度。

## 当时的未调查项与闭环

- v7 的终态可直接复用 legacy `delivery_intents` 还是必须注册 terminal extension：用 focused runner test 验证。
- 子调研 attempt 在节点内并行时的 effect 幂等边界：用故障注入测试验证重复 resume 不产生重复最终交付。
- 真实 provider 下拆题质量、子代理返回质量与总耗时：用自然语言 spike 验证并把结果写回本计划。

## 2026-07-20 增量校准：进度可见性与文件交付

- 已验证保存目录兼容：run `028f8c46...` 的 Markdown 已落到既有 `DeepResearch` 目录，文件名包含主题
  与 run id；目录逻辑无需重写。
- 当前 scheduler 事件身份是 attempt run id `dr-i.aN`，通用前端按 run id 建行，因此 4 个方向加 1 次
  重试被显示成 5 个子代理；该通道也没有方向文本、attempt 上限、来源数和业务终态。
- 当前 v7 artifact intent 将保存结果嵌套在 `payload.artifact`；产品投影只识别顶层文件字段或
  `artifacts[]`，随后因为顶层 `report` 存在而回退到 text Artifact，导致文件操作按钮消失。
- 最小结构修复：新增 parent-run owned 的 v7 child progress snapshot；以 stable child id 原位更新；
  v7 artifact intent 直接使用通用 file envelope，继续复用现有 ProductDeliveryAdapter、ArtifactCard 与
  Tauri 文件命令。
