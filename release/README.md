<!-- SPDX-FileCopyrightText: 2026 DennyWanye -->
<!-- SPDX-License-Identifier: BUSL-1.1 -->

# DeskPet 发布 & 自动更新运行手册

> 本文档记录**当前真正跑通的**发布流程（本地构建 + 签名 → 公开 GitHub release + 腾讯 COS 国内源 → 客户端自动更新）。
> 与历史 [`docs/RELEASE.md`](../docs/RELEASE.md)（讲 CI/签名理论，但那套 CI 因缺后端构建步骤从未真跑通）不同——**以本文为准**。
> 末次验证：2026-06-05，真机 GUI E2E `beta.2 →(检测→下载→安装→重启)→ beta.3` 自动更新全程跑通。

---

## 1. 架构一句话

**私密仓 `deskpet-private` 本地构建 + 签名 → 发布到公开仓 `deskpet` 的 Release(Latest) + 腾讯 COS(国内主源) → 客户端 `tauri-plugin-updater` 启动检查/手动「检查更新」自动升级。**

- 安装包**瘦身**：模型不内嵌（`DESKPET_BUNDLE_MODELS=0`，304MB 安装包），首启从 COS 下 ~3.9GB 模型。
- updater endpoints：**COS 主**（国内快）+ **GitHub 备**（fallback）。
- 安装为 **per-user（HKCU，免管理员）**，所以自更新无需提权。

## 2. 当前发布 & 分享链接

| | |
|---|---|
| 当前版本 | **v0.6.0-beta.3** |
| 国内直链(COS) | `https://defaultbucket-1300194691.cos.ap-guangzhou.myqcloud.com/deskpet/DeskPet_0.6.0-beta.3_x64-setup.exe` |
| GitHub Release | https://github.com/DennyWanye/deskpet/releases/latest |

**分享给用户**：发上面任一下载链接（国内发 COS）。用户**双击装一次**（per-user，免管理员）→ 之后**全自动更新**，不用再分享。
> ⚠️ 首次启动会从 COS 下 ~3.9GB 模型（语音/记忆，仅一次，有进度横幅），下完才有完整 ASR/记忆。

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

```powershell
# 0. 在构建/发布工作区(worktree fix-backend-orphan，junction backend/dist-portable→F:\deskpet-build\dist 已配)
# 1. bump 版本(3 处)：tauri.conf.json / Cargo.toml / package.json → 0.6.0-beta.N

# 2. 重打瘦 backend(含最新代码)
cd backend
$env:DESKPET_BUNDLE_MODELS = "0"
F:\deskpet-build\venv\Scripts\python.exe -m PyInstaller deskpet-backend.spec --noconfirm --clean `
    --distpath F:/deskpet-build/dist --workpath F:/deskpet-build/build

# 3. 签名 NSIS
cd ..\tauri-app
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $env:USERPROFILE\.tauri\deskpet.key -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
npm run tauri build -- --bundles nsis
# → target/release/bundle/nsis/DeskPet_<v>_x64-setup.exe (+ .sig)

# 4. 生成 latest.json(COS 版 url→COS、GitHub 版 url→GitHub、signature=.sig 内容)
# 5. 发 GitHub(Latest, 非 prerelease!)
gh release create v0.6.0-beta.N --repo DennyWanye/deskpet --latest `
    DeskPet_<v>_x64-setup.exe DeskPet_<v>_x64-setup.exe.sig latest.json
# 6. 传 COS(installer + latest.json)
F:\deskpet-build\coscli.exe cp DeskPet_<v>_x64-setup.exe cos://defaultbucket-1300194691/deskpet/DeskPet_<v>_x64-setup.exe
F:\deskpet-build\coscli.exe cp latest.json cos://defaultbucket-1300194691/deskpet/latest.json
# 7. 验证两个 endpoint latest.json 都返回新版本号
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

## 6. 相关文档 / 脚本

- 设计与计划：[`plans/2026-06-05-nsis-model-externalization/PLAN.md`](../plans/2026-06-05-nsis-model-externalization/PLAN.md)
- 模型上传脚本：[`scripts/publish_models_to_cos.py`](../scripts/publish_models_to_cos.py)
- 历史签名/CI SOP（参考）：[`docs/RELEASE.md`](../docs/RELEASE.md)
- 客户端 updater 逻辑：`tauri-app/src/hooks/useUpdateChecker.ts`（启动检查）、`tauri-app/src/components/SettingsPanel.tsx` 的 `UpdateSection`（手动「检查更新」）
