<!-- SPDX-FileCopyrightText: 2026 DennyWanye -->
<!-- SPDX-License-Identifier: BUSL-1.1 -->

# DeskPet 发布 & 自动更新运行手册

> 历史文档：本页描述分叉前 DeskPet 的旧发布链路，不是 `simple_harness` 当前发布入口。
> 其中仓库名、制品名、签名文件和数据路径仅作为历史记录保留。

> 本文档记录**当前真正跑通的**发布流程（本地构建 + 签名 → 公开 GitHub release + 腾讯 COS 国内源 → 客户端自动更新）。
> 与历史 [`docs/RELEASE.md`](../docs/RELEASE.md)（讲 CI/签名理论，但那套 CI 因缺后端构建步骤从未真跑通）不同——**以本文为准**。
> 末次验证：2026-06-05，真机 GUI E2E `beta.2 →(检测→下载→安装→重启)→ beta.3` 自动更新全程跑通。

---

## 1. 架构一句话

**私密仓 `deskpet-private` 本地构建 + 签名 → 发布到公开仓 `deskpet` 的 Release(Latest) + 腾讯 COS(国内主源) → 客户端 `tauri-plugin-updater` 启动检查/手动「检查更新」自动升级。**

- 安装包**瘦身**：ML 模型不内嵌（`DESKPET_BUNDLE_MODELS=0`），首启从 COS 下 ~3.9GB 模型。Live2D 人物形象（~81MB）**自 beta.7 起内嵌**（estella 默认 + hiyori + Azuki-san + HoshinoAi + Snow Leopard + Estella-DG），装机即带形象 → 安装包 ~347MB。
- updater endpoints：**COS 主**（国内快）+ **GitHub 备**（fallback）。
- 安装为 **per-user（HKCU，免管理员）**，所以自更新无需提权。

## 2. 当前发布 & 分享链接

| | |
|---|---|
| 当前版本 | **v0.6.0-beta.8**（最新已发布/可分享版本） |
| 国内直链(COS) | `https://defaultbucket-1300194691.cos.ap-guangzhou.myqcloud.com/deskpet/DeskPet_0.6.0-beta.8_x64-setup.exe` |
| GitHub Release | https://github.com/DennyWanye/deskpet/releases/latest |

**分享给用户**：发上面任一下载链接（国内发 COS）。用户**双击装一次**（per-user，免管理员）→ 之后**全自动更新**，不用再分享。
> ⚠️ 首次启动会从 COS 下 ~3.9GB 模型（语音/记忆，仅一次，有进度横幅），下完才有完整 ASR/记忆。

> 2026-07-08 audit: `v0.6.0-beta.9` now has a full Tauri release rebuild and regenerated NSIS candidate, but it is **not publishable yet**. MSVC Build Tools are installed and full build works. The remaining blockers are: updater signing key is empty (`TAURI_SIGNING_PRIVATE_KEY` length 0 in `.env`, no usable `~/.tauri/deskpet.key`), the existing beta.9 `.sig` is still the old 2026-06-28 file, and real UI E2E has not passed. Do not upload beta.9 or update `latest.json` until a valid `.sig` is generated and UI E2E passes.

## 3. 构建/发布前置（一次性，本机已就位）

| 资源 | 位置 / 值 |
|---|---|
| **CPU-torch 构建 venv** | `F:\deskpet-build\venv`（torch **2.6.0+cpu** + pyinstaller）。**必须用它**——主仓 `backend\.venv` 是 CUDA torch(4.5GB)，会撑爆 NSIS makensis(~2GB 上限) |
| **签名私钥** | `~/.tauri/deskpet.key`（无口令，key id **5E3B6A21**）。⚠️ **唯一副本，务必备份**；丢了永远没法推更新 |
| pubkey | 已写进 `tauri-app/src-tauri/tauri.conf.json > plugins.updater.pubkey` |
| **coscli** | `F:\deskpet-build\coscli.exe`，配置 `~/.cos.yaml`（桶 `defaultbucket-1300194691` / `ap-guangzhou` / **公有读私有写**） |
| COS 凭据 | 主仓 `.env` 的 `COS_SECRET_ID` / `COS_SECRET_KEY`（gitignored） |
| 中转站地址 | `tauri-app/.env.relay.local`：`VITE_RELAY_BASE_URL=https://chinzy.com`（gitignored；CI/别人构建需自行注入） |
| 模型源文件 | `F:\DeskPetData\models\{bge-m3-int8, faster-whisper-large-v3-turbo}` |

## 4. 出一个新版本（步骤）

> 工作区 = worktree `fix-backend-orphan`（junction `backend/dist-portable`→`F:\deskpet-build\dist` 已配）。
> ⚠️ **签名步骤一律走 Git Bash**（PowerShell 传空口令会让签名静默卡死，见 §5）。下面命令都按 Git Bash 写。

```bash
# 0. bump 版本(4 处)：tauri.conf.json / Cargo.toml / package.json / Cargo.lock(deskpet 包) → 0.6.0-beta.N

# 1. Live2D 人物形象资源(beta.7 起内嵌)
#    模型解压到 tauri-app/public/assets/live2d/<干净目录名>/<name>.model3.json(+ .moc3/textures/motions)。
#    vite petModelsManifestPlugin 扫目录生成 /assets/live2d/models.json → 进 dist → NSIS 随 frontendDist 打包。
#    设置面板「桌宠形象」下拉读 models.json；默认形象 = petModels.ts DEFAULT_PET_MODEL_ID(estella)，
#    目录缺失则回退程序化占位图。⚠️ 第三方模型授权自核(多数仅限直播/视频，软件分发授权另说)；
#    残缺(无 model3.json)的目录扫描器会跳过，别白占体积。安装包每带 ~81MB 模型 → ~347MB。

# 2. 瘦 backend
#    backend 自上版未改 → 直接复用 F:\deskpet-build\dist 里的 bundle，跳过本步(省 PyInstaller 重打)。
#    backend 改了 → 用 CPU venv 重打(主仓 .venv 是 CUDA torch，会撑爆 NSIS makensis ~2GB 上限)：
cd backend
DESKPET_BUNDLE_MODELS=0 F:/deskpet-build/venv/Scripts/python.exe -m PyInstaller deskpet-backend.spec \
    --noconfirm --clean --distpath F:/deskpet-build/dist --workpath F:/deskpet-build/build

# 3. 构建 + 签名 NSIS —— Git Bash，空口令 + < /dev/null(任何隐藏密码 prompt 立即 EOF，不挂死)
cd ../tauri-app
TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/deskpet.key)" \
TAURI_SIGNING_PRIVATE_KEY_PASSWORD="" \
    npm run tauri build -- --bundles nsis < /dev/null
# → target/release/bundle/nsis/DeskPet_<v>_x64-setup.exe (+ .sig，428B)

# 4. 生成 latest.json 两版(用 Python，别用 PowerShell —— 中文 notes 会坏)
#    latest.json      : platforms.windows-x86_64.url → GitHub  (作 GitHub release 的 latest.json 资产)
#    latest.cos.json  : 同上但 url → COS                       (coscli 传成 .../deskpet/latest.json)
#    signature = .sig 文件原文；version / notes / pub_date 一致

# 5. 发 GitHub(必须 --latest 且非 prerelease；API 走代理，大包不带代理传)
HTTPS_PROXY=http://127.0.0.1:7897 gh release create v0.6.0-beta.N --repo DennyWanye/deskpet --latest \
    --notes "..." latest.json DeskPet_<v>_x64-setup.exe.sig          # 先附小文件(走代理)
gh release upload v0.6.0-beta.N DeskPet_<v>_x64-setup.exe            # 大包单独传(不带代理，Clash 掐空闲长连接)

# 6. 传 COS(coscli 直连，不走代理)
F:/deskpet-build/coscli.exe cp DeskPet_<v>_x64-setup.exe cos://defaultbucket-1300194691/deskpet/DeskPet_<v>_x64-setup.exe
F:/deskpet-build/coscli.exe cp latest.cos.json     cos://defaultbucket-1300194691/deskpet/latest.json

# 7. 验证两 endpoint latest.json 都返回新版本号 + 安装包 HEAD 200
```

**模型只需首次传一次**（之后版本复用，除非模型本身变）：
```powershell
python scripts/publish_models_to_cos.py --models-dir F:/DeskPetData/models   # 策展后 ~3.9GB
```

## 5. 踩过的坑（务必记住）

- **prerelease 必须 `false`** —— endpoint 用 `/releases/latest/download/`，GitHub "latest" **不含 prerelease**，否则永久 404。`release.yml` 已改 false；`gh release create` 用 `--latest`。
- **签名密钥轮换的代价** —— 旧 key `5F623E5C` 口令遗失 → 换新 key `5E3B6A21`。**轮换后，旧 pubkey 的老版本用户无法自动升级（签名不匹配），需手动重装一次**；新 key 从 beta.2 起接管。
- **NSIS 必须 per-user** —— 默认 `currentUser`(HKCU)，自更新免管理员。**别装 per-machine MSI**（要管理员 + 和 NSIS 共目录会触发 "must be Administrator" 弹窗）。
- **hf-mirror 不可用** —— 与 `huggingface_hub 0.36` 下载 API 不兼容（元数据 HEAD 失败，关 Xet 无效）→ 模型改走**自建 COS 直下**（`backend/deskpet/model_provisioner.py`）。
- **构建必须 CPU torch** —— 见 §3，CUDA torch 撑爆 NSIS。
- **★ 后端打包源 `backend/dist-portable` 必须真指向最新 `F:\deskpet-build\dist`（2026-06-28 重大事故）** —— PyInstaller `--distpath F:\deskpet-build\dist` 把新后端写到 dist，但 NSIS 从 `tauri.conf.json > bundle.resources` 的 `../../backend/dist-portable/deskpet-backend` 取后端。**这俩必须是同一份**（dist-portable 应是指向 dist 的 junction）。事故现场：dist-portable 退化成一个停在 6/8 的**真实旧目录**（不是 junction），于是 beta.4~8 连续多版**把 6/8 旧后端打进安装包**，后端侧所有修复（空 Bearer 护栏、路径绑定）从未真正发出，用户更新后崩溃照旧。**每次构建前必查**：`(Get-Item backend\dist-portable).LinkType` 应为 `Junction`、`.Target` 应为 `F:\deskpet-build\dist`；不是就 `Remove-Item` 旧目录后 `New-Item -ItemType Junction`。
- **★ 构建 venv 缺运行时依赖 → frozen 静默降级（2026-06-28）** —— `F:\deskpet-build\venv`（CPU torch）是独立 venv，漏装 pyproject 声明的运行时依赖时 PyInstaller 的 hiddenimport 形同虚设（构建时模块不存在就打不进）。已踩：缺 `tomlkit` → `feature_flag_merge_skipped`（新 flag 不点亮）；`datasets` 装了但没被 spec `collect_submodules` → 嵌入器 worker `No module named 'datasets'` 静默退 mock。**dev venv 有、表现正常会掩盖问题**。
- **★ 发版前必须验"真 frozen 产物"，不是源码（2026-06-28 根本教训）** —— windows-mcp 真测时若注入 `DESKPET_BACKEND_DIR` 跑的是**源码后端**，会掩盖"安装包里是旧/残后端"。发版前必须**直接跑打进包的 `deskpet-backend.exe`**（见 `scripts/verify-frozen-backend.ps1`），确认启动日志含本版新增字段（如 `config_loaded ... portable= env_pinned=`、`provider_registry_ready`）、无 `No module named X`、`is_mock=False`。
- **签名步骤会卡在密码 prompt（致命，曾挂一整夜）** —— `~/.tauri/deskpet.key` 是 minisign **加密**私钥（第一行解码=`rsign encrypted secret key`），即便口令为空，签名也要拿到那个空口令去解密。PowerShell 里 `$env:..._PASSWORD = ""` / `-p ""` 都不可靠（空值被吞），`tauri build` 内嵌签名就**静默卡在隐藏密码 prompt 等 stdin**（无 .sig、无报错、无退出码）。**根治办法**：安装包先建出来（unsigned 也没关系），再用 **Git Bash** 单独签：`TAURI_SIGNING_PRIVATE_KEY_PASSWORD="" npx tauri signer sign --private-key-path <key> <setup.exe> < /dev/null`。`< /dev/null` 关 stdin（任何 prompt 立刻 EOF），bash 的 `VAR="" cmd` 能正确传空口令——秒签不挂。

## 6. 相关文档 / 脚本

- 设计与计划：[`plans/2026-06-05-nsis-model-externalization/PLAN.md`](../plans/2026-06-05-nsis-model-externalization/PLAN.md)
- 模型上传脚本：[`scripts/publish_models_to_cos.py`](../scripts/publish_models_to_cos.py)
- 历史签名/CI SOP（参考）：[`docs/RELEASE.md`](../docs/RELEASE.md)
- 客户端 updater 逻辑：`tauri-app/src/hooks/useUpdateChecker.ts`（启动检查）、`tauri-app/src/components/SettingsPanel.tsx` 的 `UpdateSection`（手动「检查更新」）
