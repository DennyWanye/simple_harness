# Simple Harness

跨平台桌面 AI 工作台：会话 / 技能中心 / 产物库 / 设置四视图 + 本地后端 Agent 能力（语音规划走中转站 Realtime）。

由 [DeskPet](https://github.com/DennyWanye/deskpet-private) 分叉而来，经两次改版：

1. **fork（2026-08-04）**：移除 Live2D 全链路（零第三方版权资产）+ macOS 支持
   （NVML 检查仅 Windows；ASR device/compute "auto"：Win+NVIDIA→CUDA fp16，
   Mac→CPU int8）。
2. **Workbench 改版（2026-08-05）**：桌宠形态退役，改为普通窗口工作台——侧栏
   （会话列表 + 技能中心 + 产物库 + 底部设置）+ 常挂载 ChatView（消息流/Harness
   巡检/模型切换/ContextRing）；companion 特权链路整体迁移到主窗（五处硬编码
   同步：前端常量/Rust 白名单/Python 白名单/ingress 标签/companion.db 迁移 007）。

**技术栈：** Tauri 2 + React（工作台 UI：侧栏 + 会话/技能/产物/设置四视图）·
Python FastAPI（后端；语音规划走中转站 Realtime）。

> 2026-08-05：桌宠形态退役，改版为普通窗口工作台（见
> `plans/2026-08-04-workbench-ui/` 与 `ARCHITECTURE/UI.md`）。

## 架构文档

模块级架构、项目状态与历史决策见 [`ARCHITECTURE/index.md`](./ARCHITECTURE/index.md)；
部署层与目录布局概览见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。

## 快速开始（macOS / Linux，直接跑源码）

不发安装包——像 OpenClaw 那样 clone 下来直接跑：

```bash
git clone git@github.com:DennyWanye/simple_harness.git
cd simple_harness
./scripts/setup.sh   # 一次性：检查 node/cargo/uv，装前端依赖 + 后端 venv（首次下 torch 较久）
```

```bash
./scripts/dev.sh     # 日常启动：一条命令拉起 Rust 壳 + 前端 + Python 后端
```

`dev.sh` 会设置 `DESKPET_BACKEND_DIR` 指向仓库的 `backend/`，Rust 壳
自动用 `backend/.venv/bin/python` 拉起后端并在退出时回收，不需要另开终端。
默认 manual edition：LLM key 在设置面板自填（OpenAI / Anthropic / 本地 Ollama）。

前置依赖：Node ≥ 20、Rust 工具链、[uv](https://docs.astral.sh/uv/)；
Linux 另需 Tauri 系统库（webkit2gtk 等，`setup.sh` 检测不到会给出安装命令）。

> Windows 付费版（relay edition）构建仍可用 `npm run dev:relay` /
> `npm run build:relay`，详见 `docs/legacy-deskpet-README.md`。

## 与 DeskPet 的差异清单（Workbench 改版后现状）

- 删除（fork 期）：Live2D 全链路——`live2dcubismcore`/`pixi-live2d-display`/`pixi.js`
  依赖、cubismcore 运行时、`assets/live2d/` 模型、`licenses/LIVE2D-*.md`。
- 删除（Workbench 改版）：桌宠渲染与动画全部子系统（pet-anim/pet-engine/
  PetCanvas/petCharacter/petTransform/.dpet 格式）、message-panel 第二窗口、
  点击穿透、FPS 徽章、桌宠形象选择。
- 新增：工作台 UI（`WorkbenchShell`/`Sidebar`/`SessionList` + 四视图 `views/`）；
  产物库（Rust `list_artifacts` command）；窗口几何真持久化（用户拖拽尺寸）。
- companion 特权链路：仅主窗（`main` label）可发起——前端常量/Rust 白名单/
  Python 白名单/ingress 标签/companion.db 迁移 007 五处一体迁移。
- `gpu_check`（NVML）模块与 `nvml-wrapper` 依赖挂 `cfg(windows)`。
- `tauri.conf.json`：普通窗口（decorations，1000×700，进 Dock）；`macOSPrivateApi`
  已随透明窗退役移除；updater 暂时停用（原 DeskPet 更新源不适用）。

## 许可证

主体代码沿用 BUSL-1.1（见 `LICENSE`），2030-05-27 自动转 Apache-2.0。
本仓库不再包含任何 Live2D Inc. 专有内容。
