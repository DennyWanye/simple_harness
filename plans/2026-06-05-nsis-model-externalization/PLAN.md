# NSIS 化 + 模型外置 + 私密→公开发布流水线（Option A）

> 状态：规划中（2026-06-05）。分支 `fix/backend-orphan-cleanup`（兼作构建/发布工作区）。
> 目标：让"你修 bug → 用户点检查更新 → 升级到最新版"对国内用户真正可用。

## 背景 / 已定架构

- **私密 `deskpet-private`(origin)**：构建+签名**中转站商业版** → 产物发到**公开 `deskpet`** release + **腾讯 COS** 供 updater 下载。
- 装机包**统一 NSIS(.exe)**（NSIS→NSIS 静默就地升级；自更新 latest.json 指向 nsis）。
- 国内模型源：**hf-mirror**（下载脚本已内建 `--mirror hf-mirror`）。

## 关键发现（大量基础设施已就绪）

| 能力 | 现状 |
|---|---|
| 瘦包（不打模型） | ✅ spec 已支持 `DESKPET_BUNDLE_MODELS=0`（`deskpet-backend.spec:165`） |
| 模型多级解析 | ✅ `paths.resolve_model_dir`：DESKPET_MODEL_ROOT → user_models_dir → bundle → assets |
| 下载脚本 + hf-mirror | ✅ `scripts/{download_bge_m3,download_faster_whisper,setup_models}.py`（snapshot_download + 断点续传 + `--mirror hf-mirror`） |
| faster-whisper 懒下载 | ✅ 缺失时首次转写自动从 HF 拉 |
| **首启自动下载 + 进度 UX** | ❌ **缺**——这是本计划的核心新增 |
| 孤儿进程修复 / 检查更新按钮 | ✅ 已做（本分支前序提交） |
| prerelease:false / 统一 nsis / 死代码清理 | ✅ 已做 |

NSIS 撞 makensis 32 位 mmap ~2GB 上限（3.7GB 内嵌模型导致）——spec 注释早有记载。瘦包后 ~1GB 可打。

## 工作分解

### Phase 1 — 瘦包 NSIS 验证（解锁打包，小）
1. `DESKPET_BUNDLE_MODELS=0` 重打后端 → `backend/dist/deskpet-backend`（~1GB）
2. worktree 资源指向瘦包 → `npm run tauri build --bundles nsis`（免签）验证 NSIS 能出包 + 体积
3. 产出 ~1GB 的 setup.exe 即 Phase 1 通过

### Phase 2 — 首启模型下载 + 进度 UX（核心新增）
- **后端**：
  - `huggingface_hub` 加入运行时依赖（spec 已 collect，但运行时 deps 要确认有）
  - 启动流程：检测 `user_models_dir` 缺 `bge-m3-int8` / `faster-whisper-large-v3-turbo` → 触发 `setup_models`（默认 hf-mirror）后台下载
  - 下载进度经 control WS 推前端（`model_download_progress` 事件）
  - 模型未就绪时：ASR/记忆功能优雅降级 + 提示"模型下载中"
- **前端**：首启下载进度卡片（2.6GB，一次性），完成后自动启用相关功能
- **降级**：离线 / 下载失败 → 明确提示 + 重试入口（不是静默 mock）

### Phase 3 — deskpet-private 发布 CI
`deskpet-private/.github/workflows/release.yml`（在公开版基础上改）：
1. Setup Python + 装后端构建依赖（CPU torch 或现有）+ `DESKPET_BUNDLE_MODELS=0` build backend
2. 注入 `VITE_RELAY_BASE_URL`（写 `.env.relay.local`，值来自 repo var）
3. 签名 NSIS（secret：`TAURI_SIGNING_PRIVATE_KEY` + `_PASSWORD`）
4. 生成 latest.json
5. 跨仓库发布到公开 `deskpet` release（`softprops/action-gh-release` 的 `repository:` + `token:`，需 `PUBLIC_RELEASE_TOKEN` secret，对公开仓库有 contents:write）
6. 上传 latest.json + setup.exe + sig 到 **腾讯 COS**（`coscli`，secret：`COS_SECRET_ID/KEY`；非密 var：桶名/地域/域名）
7. tauri.conf.json updater.endpoints 加 COS 域名（置首，GitHub 置次做 fallback）

### 所需 secret / var（放 deskpet-private）
| 名 | 类型 | 谁 |
|---|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | secret | 可由助手用 gh 从本机 key 文件设 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | secret | 仅用户（需确认还记得口令，否则轮换密钥） |
| `PUBLIC_RELEASE_TOKEN` | secret | 用户（PAT，对 deskpet 公开仓 contents:write） |
| `COS_SECRET_ID` / `COS_SECRET_KEY` | secret | 用户（腾讯云 CAM 子账号） |
| `VITE_RELAY_BASE_URL` | variable | = https://chinzy.com |
| COS 桶名 / 地域 / 公开域名 | variable | 用户提供 |

## 发布 beta.2 收尾（验收）
- 私密 tag `v0.6.0-beta.2` → CI 出签名瘦 NSIS → 发公开 release(Latest) + COS
- 真机：装 beta.1 → 点检查更新 → 升到 beta.2 → 验证 ① 孤儿端口释放 ② 首启模型下载 ③ 更新按钮闭环
- 更新 STATUS/status.md

## 风险
- 首启 2.6GB 下载：UX 要做好（进度/续传/失败重试），否则首次体验差
- CI 后端构建 greenfield（PyInstaller 在 CI 首次跑，可能要几轮）
- 口令若遗失 → 轮换密钥（现有 beta.1 用户需手动重装一次 beta.2）
