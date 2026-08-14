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
│   ├── memory/                # Three-tier memory + sqlite-vec
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
- Three-tier memory: short-term / episodic / entity (BGE-M3 embeddings + sqlite-vec)
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
