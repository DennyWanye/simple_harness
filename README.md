# Simple Harness

跨平台桌面 AI 伙伴：sprite 自研渲染桌宠 + 全本地语音交互管线（VAD → ASR → LLM → TTS）。

由 [DeskPet](https://github.com/DennyWanye/deskpet-private) 分叉而来，两个核心变化：

1. **彻底移除 Live2D** —— 渲染改为 100% 原创的 sprite 引擎（Canvas2D），零第三方版权资产。
   内置程序化角色开箱即用；把透明背景立绘 PNG 放到
   `tauri-app/public/assets/pet/character.png` 即自动切换为立绘渲染。
   pet-anim 动画系统（眨眼 / 视线跟随 / 口型同步 / 拖拽反应等）完整保留，
   通过 `pet-engine` 的参数字典驱动 sprite 变换。
2. **macOS 支持** —— NVIDIA/NVML 前置检查仅在 Windows 生效；ASR 的
   `device`/`compute_type` 默认 `"auto"`（Windows+NVIDIA → CUDA fp16，
   Mac / 无卡机器 → CPU int8）；打包目标含 `dmg`/`app`。

**技术栈：** Tauri 2 + React（前端，sprite Canvas2D 渲染）· Python FastAPI +
faster-whisper + Silero VAD + edge-tts + Ollama（后端）。

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

## 与 DeskPet 的差异清单

- 删除：`live2dcubismcore`、`pixi-live2d-display`、`pixi.js` 依赖；
  `public/lib/live2dcubismcore.min.js`；`public/assets/live2d/`（Hiyori 等模型）；
  `licenses/LIVE2D-*.md`。
- `Live2DCanvas` → `PetCanvas`：渲染循环直接驱动 `pet-engine`（SpritePetEngine）+
  `petCharacter`（立绘/程序化角色）+ `petTransform`（参数 → 整体变换）。
- `gpu_check`（NVML）模块与 `nvml-wrapper` 依赖挂到 `cfg(windows)`。
- `tauri.conf.json`：窗口去掉硬编码多屏坐标改为 `center`；开启
  `macOSPrivateApi`（macOS 透明无边框窗）；updater 暂时停用（原 DeskPet
  更新源不适用，待新发布渠道就绪后重开）。
- 自研 `.dpet` 模型格式（`pet-engine/dpet-format.ts`）保留为未来 `mesh`
  后端的扩展点。

## 许可证

主体代码沿用 BUSL-1.1（见 `LICENSE`），2030-05-27 自动转 Apache-2.0。
本仓库不再包含任何 Live2D Inc. 专有内容。
