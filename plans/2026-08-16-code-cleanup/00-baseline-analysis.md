# Phase 0: 架构基线分析 — 代码精简

## 当前状态

### 架构文档校准锚点
- `ARCHITECTURE/ARCHITECTURE.md` 最后校准: `42fbbd0f` (2026-08-16)
- 当前 HEAD: `42fbbd0f` (相同)
- git diff 自上次校准: **0 commits, 83 个 modified 文件**（未提交的工作区修改）

### 工作区状态
当前有 83 个已修改但未提交的文件，主要分布在：
- `ARCHITECTURE/` 文档
- `backend/deskpet/sdk_adapters/` SDK 适配器模块
- `backend/deskpet/tools/` 工具实现
- `backend/deskpet/capabilities/` 能力系统
- 其他 backend 模块

**关键发现**：工作区有大量未提交修改。在进行代码清理前，必须先确认这些修改的状态：
1. 是否是正在进行的工作？
2. 是否需要先提交/stash？
3. 是否包含需要保留的代码？

## 架构基线（从 ARCHITECTURE.md 提取）

### 1. 模块结构

```
backend/
├── main.py                    # FastAPI app + WebSocket routes
├── config.py                  # Configuration management
├── agent/                     # ⚠️ Legacy P3 agent code（AC-12 废弃目标）
├── deskpet/                   # P4+ new architecture
│   ├── agent/                 # Assembler + classifier
│   ├── memory/                # Three-tier memory + sqlite-vec
│   ├── tools/                 # Tool implementations
│   ├── skills/                # Skill loader + builtin skills
│   ├── mcp/                   # MCP client
│   ├── workflows/             # Workflow engine
│   ├── companion/             # Companion growth system
│   ├── harness/               # Harness (RunKernel + Driver + UoW)
│   └── sdk_adapters/          # SDK v0.1.1 adapters
├── llm/                       # LLM provider adapters
├── providers/                 # ASR/TTS providers
└── tests/                     # pytest test suite

tauri-app/
├── src/
│   ├── components/            # UI components
│   ├── views/                 # Four main views (Chat/Skills/Artifacts/Settings)
│   ├── stores/                # Zustand state management
│   └── chat/                  # Chat UI components
└── src-tauri/
    └── src/                   # Rust native layer
```

### 2. 已知废弃模块（AC-12 目标）

根据 ARCHITECTURE.md §1-2:
- **`backend/agent/`**: Legacy P3 agent code
  - 已被 `backend/deskpet/` P4+ 新架构替代
  - 当前执行 authority 在 `RunKernel + ReAct/Workflow Driver`
  - `AgentLoop` 已成为 ReAct Driver 引擎，不再是独立 owner

需要验证：
1. `backend/agent/` 是否仍有被 `backend/deskpet/` 或 `main.py` 引用？
2. 是否有测试依赖 `backend/agent/`？
3. 删除后是否会破坏任何现有功能？

### 3. 当前生产执行链（不可删除）

根据 ARCHITECTURE.md §2:
```
Main Session text
  -> Product Venue Adapter
  -> ProductTurnPreparer (backend/deskpet/harness/)
  -> RunKernel (backend/deskpet/harness/)
       -> ReAct Driver (backend/agent/agent_loop.py::AgentLoop)
            -> workflow_spawn() -> child ReAct/Workflow Driver
  -> EffectBatchExecutor -> ToolRegistry V2
  -> RunPresenter
  -> SessionDB + WS + TTS + UI
```

**关键保护点**：
- `main.py`: WebSocket/audio adapters, service composition
- `backend/deskpet/harness/`: 核心执行引擎
- `backend/agent/agent_loop.py`: ReAct Driver（虽在 agent/ 下，但已重新定位为引擎）
- `backend/deskpet/tools/`: ToolRegistry V2
- `backend/llm/`: LLM provider adapters
- `backend/providers/`: ASR/TTS providers

### 4. 当前功能模块（验收边界 AC-5～AC-8）

四个核心 view 必须保持可用：
1. **Sessions (Chat)**: WebSocket 消息、AI 响应
2. **Skills**: 技能列表、技能详情
3. **Artifacts**: 工件列表
4. **Settings**: 设置查看/修改

对应的关键模块：
- Frontend: `tauri-app/src/views/` (Chat.tsx, Skills.tsx, Artifacts.tsx, Settings.tsx)
- Backend: `main.py` (WebSocket routes), `deskpet/skills/`, artifact handlers

## 下一步计划

### Phase 0 任务清单

1. **处理未提交修改** (BLOCKING)
   - 与用户确认 83 个修改文件的状态
   - 决定是否先提交/stash

2. **静态分析扫描**
   - 扫描 `backend/agent/` 引用情况
   - 识别死代码（无引用的函数/类/文件）
   - 识别未使用的 imports
   - 识别注释代码块

3. **重复代码检测**
   - 运行重复代码检测工具
   - 记录重复逻辑位置

4. **过度抽象识别**
   - 查找只有一个实现的抽象层

5. **调试代码扫描**
   - 搜索 `console.log`（frontend）
   - 搜索 `print()`（backend，排除测试）
   - 搜索 `debugger`（frontend）

6. **创建清理计划**
   - 根据分析结果，生成分阶段清理计划
   - 每个阶段独立验证和提交

7. **架构挑战**
   - 派发 challenger 子代理验证本基线分析

## 阻塞项

**BLOCKED**: 工作区有 83 个未提交修改。必须先处理这些修改，才能安全进行代码清理。

建议行动：
- 选项 A: 先 `git stash` 保存当前修改，清理完成后再恢复
- 选项 B: 先 `git commit` 当前修改，然后在新的干净状态下清理
- 选项 C: 在当前修改的基础上清理（风险：难以区分清理前后的差异）

**需要用户决策**。
