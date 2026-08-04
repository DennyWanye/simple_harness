# HANDOFF — 任务漂移修复 v2（接手即可继续）

> 交接时间：2026-06-22 · 交接人：上个 session（Claude）· master @ **f5e878a4**（已 push，`master...origin/master` 同步）

---

## 0. 一句话状态
plan 经 7 轮 codex 对抗硬化定稿 → **4 个阶段（A/B/C/D）全部实现完成并提交、push**。核心修复（T0-1 + T1-1）已 windows-mcp 真机 PASS。**只剩收尾文档**：阶段 C+D 的 RESULTS + STATUS 更新 +（可选）整体真机交叉验证。代码层面任务已基本完成。

---

## 1. 任务背景
2026-06-21 真机复现「任务漂移」：在桌宠有旧主题历史时，发新的 deepresearch 请求会被旧主题污染——发「区块链」→ 实际搜「宁德时代/CATL」；发「固态电池」→ 实际搜「钠离子」。根因分三层（见 plan §0）：层1 主 loop 选 topic 时被旧 L2 attention-sink 污染；层2 deepresearch 内部以 LLM 漂移后的 topic 为准；层3 Tier2 相似度门控对相邻领域失效。

**权威 plan**：`plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`（标 EXECUTABLE-AS-IS）。关键章节：§3 关 Tier2 / §4 voice 全链路 / §5 会话切分接入点全清单 / §6 page-in / §8 实施顺序。

---

## 2. 已完成（4 阶段，全部 commit + 真机/评估）

| 阶段 | 内容 | commit | 真机/评估证据 |
|---|---|---|---|
| **A** T0-1 | deepresearch 原话夺权（层2纵深）：`research_tools.py` 16 处 `topic→request_topic` + prompt 双锚 | 已 push | ✅ 真机 TC-A1 相邻领域(钠离子史→**18 固态/0 钠离子**) + TC-A2 跨域(电池史→**Tokio/0 电池**)。评估 100% |
| **B** T1-1 | 会话切分（层1根治）+ T0-4 voice 全链路 + §8 sentinel + group + 前端 | `0e6211e8`+`6b927d38` | ✅ 真机 TC-B1 `/new 区块链`→组装 gate **session_id=task-default-1** + 新 scope **区块链 11/0 旧主题** + 前端「新话题」按钮真机渲染。评估 100%，389 passed + tsc0 + vitest2 |
| **C** T1-2 | L2 降级 external memory（page-in）+ /continue 透传 | `bc6955a7` | ✅ 评估 100%（512 passed），真机运行时 gate l2_page_in 生效 |
| **D** T0-3 | 关 Tier2（8 处 `topic_shift_gate`→false） | `f5e878a4` | ✅ 真机运行时 gate **topic_shift_gate=False + shift_path=off + l2_truncated=False**，Tier1(relabel/anchor)保留。473 passed 无破坏 |

**每阶段都走了完整闭环**：codex(gpt-5.5)实现 → 子代理严格评估 100%（0 GAP）→ 子代理生成手测文档 → windows-mcp 真机。

真机证据目录：`testcase/2026-06-21-task-drift-v2-phase{A,B,CD}/`（RESULTS.md + 截图 + TC-*-log.txt + 手测文档）。

---

## 3. 剩余工作（收尾，约 30 min）

1. **commit 阶段 C+D 真机证据**：`testcase/2026-06-21-task-drift-v2-phaseCD/` 下有未跟踪文件（`_shot.ps1`、`screenshots/phaseCD-tier2-off-runtime.png`、`TC-CD-gate-evidence.txt`、`tauri-dev.log`）。**先 `git add` 再 commit**（注意 memory：未跟踪新文件会被沙箱回滚消失）。
2. **写 `testcase/2026-06-21-task-drift-v2-phaseCD/RESULTS.md`**：仿 phaseA/phaseB 的 RESULTS 结构。核心结论：关 Tier2 运行时生效（topic_shift_gate=False）+ Tier1 保留 + page-in；判定锚点 = `task_drift_context_gate` log（已存于 TC-CD-gate-evidence.txt）。page-in/`/continue` 的纯单元行为由 512 测试 + 评估 100% 覆盖。
3. **更新 `STATUS/status.md`**（项目 HARD 纪律）：§3 模块完成度新增/更新「任务漂移 v2」行→✅；§4 最近里程碑倒序追加一行（v2 四阶段完成，核心真机 PASS）；改顶部「最后更新」日期为 2026-06-22。必要时 `testcase/index.md` 已含 phaseA/B，可补 phaseCD。
4. **（可选，按需）整体真机交叉验证**：若想再夯实，起桌宠 → 在 default 会话灌钠离子/电池历史（**不带 /new**）→ ① 普通对话验关 Tier2 后整体不漂 ② `/continue 它的竞品呢` 验 L2 page-in 保留续场 ③ 主窗+消息面板双窗 `/new` 验 group 同步。这些当前由单测+评估覆盖，真机受网络限制未逐一跑。
5. **push**。

---

## 4. 关键上下文 / 踩过的坑（接手必读）

- **⚠️ 网络受限（最重要）**：本机 google 被墙 / baike 返回少 → deepresearch **0 引用不落盘**（这是设计行为，**不是 bug**）。所以**判定锚点不要依赖落盘 .md**，改用更底层的 **deepresearch 搜索 query 主题**：`grep` log 里 `httpx ... GET .../search?q=` / `cdp_edge_render url=` / `subagent_scheduled ... run_id=<sid>.dr-N` + `task_drift_context_gate` + `task_drift_user_request_injected req_len=`。搜索 query 比落盘报告更直接证明主题。
- **磁盘 95%**（C 盘剩 ~11G）：Windows 偶发弹「磁盘空间不足」系统窗抢焦点，会打断 windows-mcp 输入 → 出现就先点「关闭」再重发。（如需清理可用 geek-c-drive-cleaner skill。）
- **桌宠坐标（前端加了「新话题」按钮，布局变了）**：输入框=**(3026,1509)**、「新话题」按钮=**(2846,1509)**、录音=(2711,1509)、Context usage=(3255,692)。每次启动可能漂，**先 Snapshot 确认**别用记忆坐标硬点（上个 session 因此落空过两次）。
- **中文输入流程**：`Clipboard set` → `Click 输入框` → `Shortcut ctrl+v` → **Screenshot 确认文字入框** → `Shortcut enter`（直接 Enter 易因焦点丢失落空）。
- **HARD GATE（真机前必验，否则白测）**：log 必须出现 `[backend_launch] Dev python=...\backend\.venv\...` + `p4_embedder_ready ... is_mock=False`。若看到 `Bundled exe=...` 说明跑的是旧 frozen（不含改动）。
- **手测纪律（项目 HARD CONSTRAINT）**：禁止用 WS 注入 / pytest / import 内部状态当 UI 证据；必须 windows-mcp 真模拟点击+输入+截图+log grep。详见根 `CLAUDE.md`。
- **codex 子代理写代码**：`D:/nodejs/codex exec -m gpt-5.5 -C /g/projects/deskpet "<自包含任务>" < /dev/null > log 2>&1`（后台跑），Claude 做 Lead 审查+集成+真机。
- **commit 纪律**：中文 message 用单行 `-m`（避免 heredoc 触发工具序列化错乱）；新文件立即 `git add`。

---

## 5. 环境配方（复制即用）

```bash
# 起桌宠（master backend，后台）— PHASE 换成你的阶段名
mkdir -p /g/projects/deskpet/testcase/<PHASE>
cd /g/projects/deskpet/tauri-app && DESKPET_BACKEND_DIR='G:\projects\deskpet\backend' \
  DESKPET_PYTHON='G:\projects\deskpet\backend\.venv\Scripts\python.exe' DESKPET_DEV_MODE=1 \
  npx tauri dev > /g/projects/deskpet/testcase/<PHASE>/tauri-dev.log 2>&1   # run_in_background

# 等启动
grep -a "p4_embedder_ready" /g/projects/deskpet/testcase/<PHASE>/tauri-dev.log

# 停桌宠 + 清端口
taskkill //F //IM deskpet.exe ; (netstat 找 5173/8100 LISTEN 的 PID → taskkill //F //PID)

# 跑后端测试
cd /g/projects/deskpet/backend && /g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -k "session or scope or voice or memory or assembler or task_drift or page_in" -q
```

---

## 6. 关键文件索引
- **plan**：`plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`
- **codex 实现指令**：`plans/2026-06-21-task-drift-fix-v2/codex-impl-phase{B-backend,B-frontend,C}.md`
- **真机证据**：`testcase/2026-06-21-task-drift-v2-phase{A,B,CD}/`
- **核心代码**：
  - 会话切分：`backend/deskpet/session/task_scope.py` + `backend/main.py:5461-5482`（resolve 切场）+ group `main.py:3103/3441`
  - T0-1：`backend/deskpet/tools/research_tools.py`（request_topic）
  - page-in + 关 Tier2：`backend/deskpet/agent/assembler/{policies/default.yaml, components/memory.py, policy.py, bundle.py, assembler.py}`
  - voice：`backend/pipeline/voice_pipeline.py` · sentinel：`backend/agent/agent_loop.py:611/1894`
  - 前端：`tauri-app/src/{App.tsx, message-panel/MessagePanelRoot.tsx, code-panel/ws.ts, code-panel/InputBar.tsx}`
