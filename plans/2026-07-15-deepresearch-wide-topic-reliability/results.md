# DeepResearch 宽主题可靠性与专业报告验收结果

状态：✅ 2026-07-15 最新生产代码的实施、自动化、连续真机运行和专业报告质量门全部完成。

## 最终结论

Session `16bbb4ce...` 是原始劣质结果基线；旧样本 `66720bcf...` 虽通过旧数量/support 门，但存在同实体重复、同分、英文 claim、串题和内部诊断过重，已永久降级为内容失败基线。早期改进样本 `37c53ab8...` / `f3e4ee36...` 又被人工复核发现摘要与正文 PoC 建议不一致，也不再作为最终 PASS。

最终实现坚持“质量优先，不为凑数放入弱候选”：报告允许 3～8 个真正过门发现，固定生成一页式执行摘要、组合建议、分主题 Top 技术、方法与局限；每项必须有核心变化、价值分析、成熟度、采用建议、风险、来源日期和可追溯引用。证据质量低于 3/3 时，摘要和正文都只能建议“先补齐一手来源与独立来源，再决定是否 PoC”。

## Xiaomi 同进程连续真机验收

| 顺序 | run / session | 工作流结果 | 报告质量 |
|---|---|---|---|
| 1 | `43e851a0d1264bcd8ecfe90f258c5afa` / `7cda06c1-f5f5-4c4d-b10e-056b8be632db` | PASS，v4 completed，`142129 ms`，恰好 1 report + 1 Artifact + 1 final assistant | 5 项、5 个 appendix citations 全部映射；当前 17 项质量检查全 PASS；弱证据 PoC 口径一致、无重复标点 |
| 2 | `59975eb1a85a409e97e0277dcfe4a705` / `f88880ed-944e-43fb-92e5-965c3f837a5a` | PASS，v4 completed，`141713 ms`，恰好 1 report + 1 Artifact + 1 final assistant | 5 项、5 个 appendix citations 全部映射；当前 17 项质量检查全 PASS；5 个分数各不相同 |

两轮使用同一 Tauri/backend 进程和相同题目指纹 `6e255f47e47108b5`。第二轮仍抓取 17 条、成功 16 条并保留 16 个证据段落，证明第一轮没有通过 cooldown 把后续请求连坐为零候选。应用保持运行，第二轮最终报告留在小米屏幕。

最终报告：

- `DeepResearch/可以帮我调研一下，现在-AI-相关的最新最有价值的技术相关的信息吗？-43e851a0d1264bcd8ecfe90f258c5afa.md`
- `DeepResearch/可以帮我调研一下，现在-AI-相关的最新最有价值的技术相关的信息吗？-59975eb1a85a409e97e0277dcfe4a705.md`

最终证据：

- `evidence/consecutive-success-run1.json`
- `evidence/consecutive-success-run2.json`
- `evidence/report-quality-43e851a0d1264bcd8ecfe90f258c5afa.json`
- `evidence/report-quality-59975eb1a85a409e97e0277dcfe4a705.json`
- `evidence/success-tauri.stderr.log`
- `evidence/backend-full-postaudit-junit.xml`

## 当前 17 项报告门

- 发现数为 3～8；少于 5 项时明确说明未用低质量候选补齐数量。
- 标题必须是清晰实体，禁止 `Plus`、`VLA`、`Context Window` 等泛化标题；同实体必须合并。
- 一页式摘要必须给出具体判断；成熟度、近期性和分数不能全部塌缩为占位值。
- 每项核心变化必须是有实质内容的中文表达，模板不能机械重复。
- 每项必须有引用；引用附录必须可解析且全部映射，不允许悬空引用。
- 过滤页面 chrome、投稿/作者/分享/导航、营销口号和无法验证的任务描述。
- 证据质量低于 3/3 时禁止直接建议 PoC/受控采用，摘要、组合建议和正文必须一致。
- 禁止双句号、双分号、双逗号等明显成文瑕疵。

## 最新自动化门禁

| 门禁 | 最终结果 |
|---|---|
| 报告/情报聚焦回归 | `114 passed`（新增采用建议一致性与重复标点测试） |
| v4、进度、delivery 矩阵 | `161 passed` |
| v1～v3 兼容矩阵 | `52 passed` |
| Search Gateway | 隔离复跑 `63 passed`；此前并行负载的 2 个 timing failure 未在隔离复跑出现 |
| 前端 | Vitest `77 files / 811 tests passed`；TypeScript 与 Vite production build PASS |
| 后端全量（最后代码后重跑） | `4643 passed / 14 skipped / 9 deselected / 10 known failures`；失败集合与固定基线完全相同，无新增失败 |

后端 JUnit 为 `evidence/backend-full-postaudit-junit.xml`。10 个既有失败仍是 agent 并行 timing、全局/env 配置污染、Ollama endpoint、git PATH 和旧 MemoryComponent stub 缺少 `time_remaining_ms`。

前端 scoped ESLint 的 36 errors 在固定基线提交 `0117ad...` 上完全相同：`ws.ts` 32 个旧 `any`，`MessagePanelRoot.tsx` 4 个旧 `any`/React effect 错误；本轮新增 command/retry 行不在报错位置。因此门禁按“相对固定基线无新增错误”通过，不伪写为 lint 全绿。全 `src` 仍有既有 `263 problems (253 errors, 10 warnings)`。

## 失败语义回归

隔离失败 run `4e6d1b0be1364dc196d47b5ae439a9de` 继续保持 PASS：无 report、无 Artifact、无 final assistant；后续阶段显示未执行，并提供服务端幂等“重新调研”。原始 query、URL、正文和 secret 不进入安全诊断。

## 文档事实源

当前事实已回写 `ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`、`ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/ARCHITECTURE.md` 与 `ARCHITECTURE/PROJECT_STATUS.md`。按用户要求，本轮不更新 `STATUS/status.md`。
