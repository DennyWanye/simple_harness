# DeepResearch v7 简化编排手工回归

> 状态：本轮 required 场景与 AC-1～AC-11 PASS；完整多类别发布矩阵为明确的后续候选
> 环境：Windows 11、源码 Tauri、真实 Relay LLM、真实 Search Gateway/FetchService
> 证据：`plans/2026-07-19-deepresearch-simplification/spike/result.md`

## TC-1：主 Agent 拆题、子代理监控、补救与统一报告

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 清理本仓库拥有的 8100/5173 listener，只启动 Tauri，并注入当前 backend 源码目录。 | 日志出现 `backend_launch Dev python=... backend_dir=...`、`deep_research/v7` 注册和 `Application startup complete`；无端口双占。 |
| 2 | 用 Computer Use 点击宠物输入框，输入：`请对 Tokio 异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，并给出选型建议。`，点击发送。 | UI 显示完整用户消息和工作中状态；日志产生唯一 v7 parent run。 |
| 3 | 等待 plan 和 child 调研。 | 生成 2–6 个子方向；每个 child 有稳定 `dr-i`、attempt、status、reason 和来源数；sibling 失败不取消其他 child。 |
| 4 | 若 child 首轮无证据/超时，检查主 Agent 处理。 | 记录诊断并以 attempt 2 继续；到达上限后标 insufficient，不无限循环，不把 0 引用结果纳入综合。 |
| 5 | 等待 parent 完成。 | 只有合格子报告进入统一 synth；engine 与 business status 分别可查；产生唯一 workflow.report、artifact_card、final_assistant。 |
| 6 | 人工阅读 Markdown 报告与宠物气泡。 | 报告含摘要、分主题发现、综合分析、局限、结论和可追溯引用；UI 显示正文开头而非 `(完成)` 占位。 |

### 2026-07-19 执行结果

- PASS：run `aa61dccfaa794f69a502d978adfb5ca0`，4 个子方向，3 valid / 1 insufficient。
- PASS：`dr-2` attempt 2 修复成功（2 来源）；`dr-1` attempt 2 超时后止损。
- PASS：业务 `partial`，5 来源 / 3 域，唯一报告与 Artifact，UI 显示报告正文。
- 人工质量：达到技术生态场景最低门；第一方来源比例偏低，作为后续质量增强项保留。

## TC-2：无结果与跨层契约回归（自动化）

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | mock Search Gateway 返回空结果。 | focused child 只生成有界候选 URL，并且必须抓取成功后才形成 citation。 |
| 2 | 用 FetchDocument 形状返回非空 `text`、ISO `fetched_at` 且无 legacy `ok`。 | passage 被接纳，ISO 时间不导致异常；显式 `ok=false` 仍拒绝。 |
| 3 | mock child 首轮 0 citation、次轮有效。 | manager continuation 成为次轮权威输入，attempt=2，最终只接纳有效结果。 |

### 2026-07-20 自动化门

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_deepresearch_output_dir.py tests/test_workflow_progress.py tests/test_workflow_product_delivery.py tests/test_deepresearch_simplified_v7.py tests/test_deepresearch_subagent_fanout.py

cd ..\tauri-app
node node_modules\vitest\vitest.mjs run src/stores/sessionsStore.test.ts src/components/workflow/WorkflowProgressGroup.test.tsx src/code-panel/SubagentProgressPanel.test.ts src/code-panel/ws.chat.test.ts src/code-panel/ArtifactCard.test.ts src/workflowFinalAssistant.test.ts
node node_modules\typescript\bin\tsc -b --pretty false
```

预期：全部通过；4 个方向 + 5 个 attempts 只渲染 4 行；明确覆盖 `child running seq10 → synth seq12 → child valid seq11` 与 `final seq20 → child terminal seq19`，children 最终状态仍补齐且主卡不从 terminal 回退；并发 callback 快照按方向单调收口；nested artifact 归一成一个 file card；相对目录 override 最终仍返回绝对路径。

## TC-3：真实 UI 子方向与文件操作 `[AC-9..AC-11]`

> 状态：PASS（2026-07-20，Computer Use 真坐标操作）

执行纪律：每个 UI 动作前先记 `坐标=(x,y)|动作=...|期望=...`，并按“Snapshot/Screenshot → 真实点击/输入 → 结果截图 → 日志判定”执行。失败时至少尝试 3 种不同 workaround 才能标环境受限，跳过必须由用户确认。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 结束当前 Tauri 及其托管 backend/Vite；记录并复用绝对 `DESKPET_USER_DATA_DIR=F:\projects\deskpet\backend\userdata`、`DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`，用 bundled Node 执行 `node_modules/@tauri-apps/cli/tauri.js dev --config ...\tauri-spike.conf.json`，日志固定写入 spike 目录。 | 启动日志确认 Dev python/backend dir 与相同 userdata；8100/5173 各只有一个 listener；应用能打开原 Session。 |
| 2 | 在原 Session 查看已完成的旧 Tokio run，记录 `old_run_id`。 | 旧 text `research_report` 被历史 `workflow.artifact_card` 中的 path 恢复为恰好 1 张 file Artifact；卡片显示“打开 / 另存为 / 在文件夹中显示 / 复制路径”。旧 run 仅验证历史兼容，不与新 run 做 hash 比较。 |
| 3 | 通过真实坐标原样发送 S-3 exact input：`请对 Tokio 异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，并给出选型建议。`；发送后立即记录同一 `session_id/new_run_id`。 | 单张 DeepResearch 卡先显示“主 Agent 拆出的 N 个子方向”（N=2–6），每行显示真实问题、状态、尝试次数/上限、来源数；通用“子代理并发”面板不再把内部 attempts 重复展示。 |
| 4 | 在运行中至少截图 queued/running 状态；等待终态；逐个 `dr-i` 对照日志/最终 child record。 | 行状态推进到 valid/insufficient；逐方向的 `question/status/attempt/max_attempts/n_sources` 全部一致；若自然发生补救，同一 `dr-i` 行从 attempt 1 更新到 2，不新增第 5 行。 |
| 5 | 在 `new_run_id` 的 file Artifact 上通过真实坐标点击“打开”。 | 系统默认 Markdown 应用打开新报告；截图可见新报告文件名或正文开头。 |
| 6 | 返回 DeskPet，在 `new_run_id` 卡上通过真实坐标点击“在文件夹中显示”。 | Windows Explorer 打开 `F:\projects\deskpet\DeepResearch`，并选中新 run 的 `<topic>-<new_run_id>.md`。 |
| 7 | 返回 DeskPet，在 `new_run_id` 卡上点击“另存为”；保存到 `F:\projects\deskpet\DeepResearch\ui-save-as-test-<new_run_id>-<执行时间戳>.md`。 | 卡片显示“已另存为”；唯一副本存在，不触发覆盖歧义。 |
| 8 | 返回 DeskPet，在 `new_run_id` 卡上点击“复制路径”。 | 卡片出现可见“路径已复制”反馈；不读取剪贴板内容作为替代证据。 |
| 9 | 完整退出 Tauri 和托管进程，再用步骤 1 完全相同的 userdata/启动命令重启并打开同一 Session。 | `new_run_id` 恰好 1 张 DeepResearch 主卡、N 个唯一 `dr-i` 行、1 张 file Artifact；无内部 attempt 重复行；方向终态仍在且主卡保持 terminal。 |
| 10 | 从 `new_run_id` 的原始 `workflow.artifact_card` 事件读取 canonical path/sha；对新原路径和步骤 7 新副本分别运行 `Get-FileHash -Algorithm SHA256 -LiteralPath '<path>'`，并用 `Get-Item -LiteralPath '<path>'` 记录绝对路径/文件名。 | 新 payload path 是绝对旧目录路径，文件名含 topic slug 与同一 `new_run_id`；`new payload.sha256 = 新原文件 hash = 新 save-as hash`；正文非空且引用可追溯。 |

### 失败与边界判定

- 任一文件操作失败时，ArtifactCard 必须显示 `role=alert` 可见错误；自动化 `ArtifactCard.test.ts` 覆盖拒绝与空路径。
- 不允许用 WebSocket 注入、直接调用 Tauri command、后端脚本回放或只看日志代替步骤 2～9 的真人 UI 操作。
- 本次按用户“快速修改 + 一个调研结果 spike”的明确范围，只要求 S-3/Tokio 一个真实 root 场景；其余语义类别仍保留为发布矩阵 PENDING，不冒充已测。

### 2026-07-20 证据记录

- `session_id`：`124b9030-ddd5-4f09-9dec-7be85c6d32fe`。
- `DESKPET_USER_DATA_DIR（首次启动 / 重启）`：两次均为
  `F:\projects\deskpet\backend\userdata`；重启日志确认 source backend 与启动完成。
- `new_run_id`：`9c42a6a145304123979f8ec6ae25cba0`。
- `engine terminal / UI terminal`：completed / “已完成 · 6/6 · 100%”；业务结果含 3 valid、1 insufficient。
- `artifact path`：
  `F:\projects\deskpet\DeepResearch\请对-Tokio-异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，-9c42a6a145304123979f8ec6ae25cba0.md`。
- `原文件实际 SHA-256`：`ACA7965492130FB8756A438CD7417168DB9AA1515A03D20C4887141DA4C2A2D0`；
  文件大小 8065 bytes。
- `save-as path / SHA-256`：`F:\projects\deskpet\DeepResearch\ui-save-as-9c42a6a1.md` /
  `ACA7965492130FB8756A438CD7417168DB9AA1515A03D20C4887141DA4C2A2D0`，与原文件完全一致。
- `文件操作`：打开触发 Windows 应用选择器（本机未关联 `.md`）；Explorer 打开既有目录并选中文件；
  另存为生成唯一副本；复制路径显示“路径已复制”。
- `重启恢复`：同一历史 Session 恢复 1 张 terminal 主卡、4 个唯一方向、1 张 file Artifact；
  retry attempt 没有重复成第 5 行。
- `screenshots`：
  - [`TC3-step-9-9c42a6a1-restart-history.png`](./evidence/TC3-step-9-9c42a6a1-restart-history.png)：重启后 4 个唯一方向与终态。
  - [`TC3-step-5-8-9c42a6a1-file-card.png`](./evidence/TC3-step-5-8-9c42a6a1-file-card.png)：唯一 Markdown file card 与四个操作。
  - [`TC3-step-5-9c42a6a1-open-with-dialog.png`](./evidence/TC3-step-5-9c42a6a1-open-with-dialog.png)：真实点击“打开”后的 Windows 应用选择器。
  - [`TC3-step-6-9c42a6a1-explorer-selected.png`](./evidence/TC3-step-6-9c42a6a1-explorer-selected.png)：既有 `DeepResearch` 目录、选中新报告及另存副本。
  - [`TC3-step-7-9c42a6a1-save-as-dialog.png`](./evidence/TC3-step-7-9c42a6a1-save-as-dialog.png)：真实点击后的 Windows 原生另存为对话框。
  - [`TC3-step-8-9c42a6a1-copy-path-feedback.png`](./evidence/TC3-step-8-9c42a6a1-copy-path-feedback.png)：可见“路径已复制”反馈。
- `logs`：`plans/2026-07-19-deepresearch-simplification/spike/tauri-ac9-11.stderr.log`、
  `tauri-ac9-11-restart.stderr.log`。

## 后续回归候选（不在本轮 required 范围）

- 政策/市场时效性、多源异构场景。
- 虚构/无公开资料场景的真实 UI 诚实降级。
- 多次并发 run、取消和进程中断恢复的 v7 专项真机矩阵。

这些项目不得由同一 Tokio 输入的重跑冒充，后续正式发布验收时单独执行。
