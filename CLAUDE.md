# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Simple Harness** is a cross-platform desktop AI workbench with four main views (Sessions / Skills / Artifacts / Settings) plus a local backend Agent with autonomous capabilities. Forked from DeskPet in 2026-08 with significant architectural changes.

**Tech Stack:**
- **Backend:** Python 3.11+ FastAPI (`backend/`)
- **Frontend:** React 19 + Vite (`tauri-app/src/`)
- **Shell:** Tauri 2 + Rust (`tauri-app/src-tauri/`)

**Key Differences from DeskPet:**
- Removed Live2D rendering system entirely
- Removed hosted account system (manual provider configuration only)
- Changed from desktop pet window to standard workbench UI
- macOS support added (NVML GPU checks Windows-only)

---

## Essential Commands

### Development Setup (First Time)

```bash
./scripts/setup.sh
```

Installs all dependencies: frontend npm packages + backend Python venv (includes PyTorch download, takes 15-30 min first run).

**Prerequisites:** Node.js ≥20, Rust toolchain, [uv](https://docs.astral.sh/uv/)

### Daily Development

```bash
./scripts/dev.sh
```

Starts everything in one command: Tauri shell spawns Python backend + Vite frontend automatically. Backend runs at port 8100, Vite at 5173.

**IMPORTANT:** Don't manually start backend with `python main.py` — Tauri's `process_manager.rs` already spawns it. Manual start causes port 8100 conflict (error 10048 on Windows). Let Tauri manage the backend lifecycle.

### Testing

```bash
# Backend tests
cd backend
python -m pytest                    # Run all tests
python -m pytest -v                 # Verbose
python -m pytest -m perf            # Performance regression tests (skipped by default)
python -m pytest -m model_required  # Tests requiring BGE-M3 model weights
python -m pytest tests/test_agent_loop_pipeline.py  # Single test file

# Frontend tests
cd tauri-app
npm test                # Run all Vitest tests
npm run test:watch      # Watch mode
npm run typecheck       # TypeScript type checking
npm run lint            # ESLint

# Build
cd tauri-app
npm run build           # Frontend production build
npm run tauri:build     # Full Tauri application build
```

---

## Architecture Overview

### Module Architecture

The **canonical source of truth** for architecture and project status is [`ARCHITECTURE/index.md`](./ARCHITECTURE/index.md). Always read that first when picking up a task.

Key architecture documents:
- [`ARCHITECTURE/PROJECT_STATUS.md`](./ARCHITECTURE/PROJECT_STATUS.md) — Module completion status, active worktrees, recent milestones, known issues
- [`ARCHITECTURE/AGENT_HARNESS.md`](./ARCHITECTURE/AGENT_HARNESS.md) — Current Agent Harness production facts
- [`ARCHITECTURE/UI.md`](./ARCHITECTURE/UI.md) — UI theme, shared styles, page coverage
- [`ARCHITECTURE/AgentLoop.md`](./ARCHITECTURE/AgentLoop.md) — ReAct loop, tool registration, completion gate

### Code Structure

```
backend/
├── main.py                    # FastAPI app + WebSocket routes
├── config.py                  # Configuration management
├── agent/                     # Legacy P3 agent code
├── deskpet/                   # P4+ new architecture
│   ├── agent/                 # Assembler + classifier
│   ├── memory/                # Host 会话账本 + S1 证据链（名字是历史遗留；
│   │                          # 认知记忆 SDK 已于 2026-09-10 移除）
│   ├── tools/                 # Tool implementations (ppt/web/OCR/etc)
│   ├── skills/                # Skill loader + builtin skills
│   ├── mcp/                   # MCP client
│   ├── workflows/             # Workflow engine
│   └── companion/             # Companion growth system
├── llm/                       # LLM provider adapters
├── providers/                 # ASR/TTS providers
└── tests/                     # pytest test suite

tauri-app/
├── src/
│   ├── components/            # UI components (WorkbenchShell/Sidebar/etc)
│   ├── views/                 # Four main views (Chat/Skills/Artifacts/Settings)
│   ├── stores/                # Zustand state management
│   └── chat/                  # Chat UI components
└── src-tauri/
    └── src/                   # Rust native layer (IPC, keychain, process mgmt)
```

### Important Patterns

**Backend:**
- Agent loop uses ReAct pattern with tool registration system
- No long-term memory: the cognitive Memory SDK was removed on 2026-09-10
  (`plans/2026-09-10-remove-memory-sdk/`). The Harness SDK's mandatory
  `AgentMemoryPort` is satisfied by an honest empty port that recalls
  nothing and retains nothing.
- LLM providers: Anthropic / OpenAI / Google Gemini adapters with fallback chain
- Tools register via `deskpet.tools` with effect policies

**Frontend:**
- Zustand for state management
- WebSocket connection to backend control channel
- Four-view workbench: Sessions / Skills / Artifacts / Settings

---

## Development Discipline

### ARCHITECTURE Update Rule (HARD CONSTRAINT)

**Any task that passes tests MUST update [`ARCHITECTURE/`](./ARCHITECTURE/) in the same delivery.**

When a feature/module passes acceptance (pytest/vitest/cargo/manual E2E):
1. Update corresponding `ARCHITECTURE/<MODULE>.md` with production chain/boundaries/validation status
2. Update [`ARCHITECTURE/PROJECT_STATUS.md`](./ARCHITECTURE/PROJECT_STATUS.md) module completion
3. If milestone-level → append to PROJECT_STATUS recent milestones (reverse chronological)
4. If worktree merged to master → update PROJECT_STATUS worktree status
5. Update "last updated" date in relevant architecture docs

**"Code changed + tests pass but ARCHITECTURE not updated" = task INCOMPLETE.**

`STATUS/` directory is legacy only (historical link compatibility) — DO NOT write new status content there.

### 开发期不做旧数据兼容（硬约束，用户 2026-09-25 / 09-27 / 09-30 / 10-01 四次强调）

**现在是开发阶段，不处理旧数据兼容。** 要改一样东西，就直接改成当前最合理的做法，把旧的删掉：

- 不留"新旧版本双分支"、不留 `legacy` 回落路径、不把旧路径"隐藏"起来（除非用户明确要求保留）。
- 提示词模板 / 工具说明 / 契约 / 配置：**只保留一份当前版本**。要改就改当前这份（或换成新版本后删掉旧版本），不要 `_revise` 链式保留历史版本，不要为历史版本写钉哈希测试；钉哈希基线随当前版本重生成。
- 开发库里按旧契约建的任务不保证能跑：遇到时明确报错，不静默回落、不迁移。
- 报告和取舍里不要拿"影响正常使用 / 老任务起不来"当理由。
- 已知欠账：代码里仍有大量 `legacy` / 兼容分支（2026-10-01 盘点约 298 个文件），要单独立项逐步删除；新改动不得再增加。

**"做了功能又加一层兼容" = 违反本约束。**

### Testing Phase: Immediate Default-ON (HARD CONSTRAINT)

**Current phase is testing. Completed capabilities must be default-ON immediately, no gradual rollout.**

- "Developed but flag still default OFF" = **NOT ACCEPTABLE**
- When a capability passes unit tests + implementation complete, flip its default flag **ON** in the same delivery
- **Exceptions** (still OFF): ① incomplete/half-done implementations, ② dangerous/irreversible without guardrails, ③ unrelated to current mainline
- Do not write "shadow mode / gradual rollout / batch A/B / default OFF until observation passes" — that's for production phase, not now

---

## Common Pitfalls (踩过的坑)

1. **Tauri spawns backend automatically** — Don't manually run `python main.py`. Tauri's `process_manager.rs` spawns backend to `DESKPET_BACKEND_PORT` (default 8100). Manual start causes port conflict → "os error 10048" → app fails to start. **Correct:** Only start via `./scripts/dev.sh` or `npm run tauri:dev`.

2. **Backend logs go to stderr** — Backend stdout is piped by Rust (reads SHARED_SECRET then drains). structlog writes to stderr → `Stdio::inherit()` → logs appear in tauri dev output. Check tauri dev logs to see backend logs.

3. **Running worktree backend requires `DESKPET_BACKEND_DIR`** — `backend_launch.rs::resolve_with` priority: ① `DESKPET_BACKEND_DIR` env (runs `<dir>/.venv/Scripts/python.exe main.py`), ② bundled `target/debug/backend/deskpet-backend.exe` (PyInstaller frozen, from main checkout, **doesn't include worktree changes**). To test worktree code, must set `DESKPET_BACKEND_DIR=<worktree>/backend`. Verify logs show `[backend_launch] Dev python=... backend_dir=<worktree>`, not `[backend_launch] Bundled exe=...`.

4. **`tauri dev` runs `beforeDevCommand` automatically** — `tauri.conf.json` already starts vite dev server via `beforeDevCommand`. Don't manually run another `npm run dev:relay` → two vite instances fight over `DESKPET_VITE_PORT` (strictPort) → second exits or shifts port → devUrl mismatch → blank screen. **Correct:** Either pure `npx tauri dev` (self-managed vite) OR use `--config '{"build":{"beforeDevCommand":""}}'` to disable auto-vite then manually start one. Pick one, not both.

5. **Unit tests alone don't prove completion** — Must run end-to-end validation with real UI interaction when applicable. Scripted function replay is not E2E evidence.

6. **Cross-layer contract drift** — pytest + tsc both pass but backend/frontend disagree on field units → use `scripts/e2e_*.py` live smoke tests as final gate.

7. **Port isolation for worktrees** — Main tree uses backend=8100 / vite=5173 (defaults). Other worktrees must inject `DESKPET_BACKEND_PORT`/`DESKPET_VITE_PORT` to avoid conflicts (see `scripts/dev-worktree.ps1`).

---

## Project-Specific Context

### Branch Strategy

Master branch for direct development — no long-lived feature branches. Worktree topology and module completion status tracked in [`ARCHITECTURE/PROJECT_STATUS.md`](./ARCHITECTURE/PROJECT_STATUS.md) §2.

### Testing Credentials (DEV only)

LLM calls go through relay (default gpt-5.5 equivalent). Test credentials stored in `LOCAL-DEV-CREDENTIALS.md` (gitignored). Template: [`LOCAL-DEV-CREDENTIALS.md.example`](./LOCAL-DEV-CREDENTIALS.md.example).

**Security constraints:**
- ⚠️ Don't push to public GitHub
- ⚠️ Don't write to `.env` or `secrets/` (collected by diagnostic bundle)
- ✅ Repository is private (git@github.com:DennyWanye/deskpet)

### Configuration

Main config: `config.toml` at repository root. LLM providers configured in Settings panel (writes to OS keychain, not config file).

### Python Dependencies

PyTorch version strictly pinned (torch 2.7.1 + torchvision 0.22.1 + torchaudio 2.7.1). **All three must match minor versions.** See `backend/pyproject.toml` for detailed version rationale.

GPU deployment uses cu128 channel for Blackwell GPU support (sm_120):
```bash
pip install --index-url https://download.pytorch.org/whl/cu128 \
    torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1
```

### Key Documentation

- Quick start: [`README.md`](./README.md), [`QUICKSTART.md`](./QUICKSTART.md)
- Architecture baseline: [`ARCHITECTURE/index.md`](./ARCHITECTURE/index.md)
- Deployment overview: [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- Contribution guide: [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- License: BUSL-1.1 (see [`LICENSE`](./LICENSE), auto-converts to Apache-2.0 on 2030-05-27)

### Plans Directory

Implementation plans live in `plans/<date>-*/00-PLAN.md`. Architecture docs record current production facts; plans record implementation process. Check [`ARCHITECTURE/PROJECT_STATUS.md`](./ARCHITECTURE/PROJECT_STATUS.md) first for current module status before diving into plans.

---

## Quick Reference

**Start development:**
```bash
./scripts/setup.sh    # First time only
./scripts/dev.sh      # Daily
```

**Run tests:**
```bash
cd backend && python -m pytest
cd tauri-app && npm test
```

**Check architecture:**
- Start here: [`ARCHITECTURE/index.md`](./ARCHITECTURE/index.md)
- Status: [`ARCHITECTURE/PROJECT_STATUS.md`](./ARCHITECTURE/PROJECT_STATUS.md)

**Remember:**
- Don't manually start backend (Tauri manages it)
- Update ARCHITECTURE/ when task completes
- Default-ON for completed features in testing phase
- Unit tests + E2E validation required for completion

## 用户产品决定（2026-09-07，后续 Agent 必须遵守）

1. **权限模式只有 manual 与 auto 两种，默认 auto。auto 模式下不弹任何授权提示，所有工具效果默认允许执行**（含 context_route、prospective_ack 等原 confirm-only 类）。原 S5b Task 6「auto 永不授予 confirm-only」口径作废；实现见 `backend/deskpet/sdk_adapters/tool_authority.py`（auto 下二次规划 `explicit_only=False`）。manual 模式仍逐次确认。
2. **遗忘只针对记忆，不针对会话记录**：在 UI 忘记一条认知记忆，只影响该记忆的召回/图谱/工作记忆，**不得**把它的来源对话轮从主对话视图或短期历史中隐藏。原 acceptance HM-AC-1 中「相关内容立即退出六阅读视图/ResumePackage」应理解为"记忆派生内容"，不包含原始会话文本。显式删除会话记录是另一个尚未定义的功能。
3. 聊天区渲染原始工具回执 JSON、召回为空后模型循环重提同一路由：记为 followup（`plans/2026-09-06-typed-use-primary/FOLLOWUPS.md` F02/F03），本轮不处理。
4. 真实模型：主用 `gpt-5.6-luna`（svtun）；中转不可用时用 `.env` 的 `DEEPSEEKER_APIKEY`（DeepSeek 官方 API，`deepseek-v4-pro`）作为回退，并在证据中记录回退。

- 2026-09-08 用户决定：F01 事件触发本轮不做，记为下一轮 followup（首选方案 B：绑定工作区本地 git release tag）；release tag 待全部任务完成并真人验收后再授权；"通过对话遗忘认知记忆"暂不提前做 S5c 模型可见记忆视图（A6 T23 改 UI 面板遗忘，对话遗忘记 F10）。
- 2026-09-09 用户决定：DeepSeek 已充值，模型改用 **`deepseek-v4-flash`**（更便宜）；原生旅程与语料批次默认走 flash，`model_overrides.toml` 需为 flash 钉 32000 窗口。
- 2026-09-16 用户决定：**grok-4.6 走 SuperGrok 订阅（Grok Build CLI 登录态），不买 API credits**。一键脚本 `backend/scripts/grok_build_runtime.py write / probe / apply / restore`，端点 `cli-chat-proxy.grok.com` 需 4 个客户端 header（`llm_runtime.json` 新字段 `extra_headers`，按 host 匹配）。跑完必须 `restore`。用法与坑见 [`docs/GROK-BUILD-LANE.md`](docs/GROK-BUILD-LANE.md)。token 只在 `~/.grok/auth.json` 与用户目录的 `llm_runtime.grok.json`，禁止打印或提交。
