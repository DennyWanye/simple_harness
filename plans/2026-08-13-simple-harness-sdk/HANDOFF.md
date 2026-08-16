# Simple Harness SDK v0.1.0 — Handoff 文档

**日期：** 2026-08-16  
**接收方：** 下一个执行验收 E2E 测试与 CI 推送的 agent  
**优先级：** HIGH — SDK v0.1.0 Release 已发布，两个遗留门未关闭

---

## 1. 全局状态总览

| 项目 | 状态 | 备注 |
|------|------|------|
| SDK 代码 | ✅ 完整 | 1122 测试全绿 |
| SDK wheel v0.1.0 构建 | ✅ 完成 | pure-Python `py3-none-any` |
| SDK GitHub 仓库 | ✅ 已推送 | `github.com/DennyWanye/simple-harness-sdk` |
| GitHub Release v0.1.0 | ✅ 已发布 | 含 wheel + sdist + SHA256SUMS |
| 产品 vendored wheel | ✅ 集成 | `backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl` |
| GitHub Actions workflows | ⚠️ 本地已提交，**未推送** | 网络/代理 TLS 问题阻断，见§3 |
| 桌面 E2E 测试 SDK-S1～S5 | ❌ NOT RUN | LLM provider 未配置，见§4 |

---

## 2. 关键身份信息

### SDK 仓库
- **本地路径：** `/Users/denny/projects/simple-harness-sdk`
- **Remote：** `https://github.com/DennyWanye/simple-harness-sdk.git`
- **当前 main HEAD（本地）：** `0e38532` — `feat: restore GitHub Actions workflows`
- **当前 main HEAD（remote）：** `54b62f6` — workflows 被临时删除的那个 commit（比本地落后一个 commit）
- **GitHub Release：** `https://github.com/DennyWanye/simple-harness-sdk/releases/tag/v0.1.0`

### Wheel 身份
```
文件名:  simple_harness_sdk-0.1.0-py3-none-any.whl
SHA256:  d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91
```
产品 vendored 路径：`/Users/denny/projects/simple_harness/backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl`  
release artifacts 路径：`/Users/denny/projects/simple-harness-sdk/dist/`

### 产品仓库
- **本地路径：** `/Users/denny/projects/simple_harness`
- **Remote：** `git@github.com:DennyWanye/deskpet`（**私有仓库，不得推送到公开 GitHub**）

---

## 3. 任务一：推送 GitHub Actions Workflows

### 背景
`.github/workflows/` 下有三个文件，在当前机器因代理 TLS 握手失败无法推送：

```
.github/workflows/ci.yml             — PR/push 构建 + 测试
.github/workflows/release.yml        — tag v* 触发自动发布
.github/workflows/platform-tests.yml — Linux/macOS/Windows 多平台验证
```

commit `0e38532` 已在本地 `main` 分支，内容完整，只需推送。

### 阻断原因
本机走 HTTP 代理 `127.0.0.1:7897`，代理对 `github.com:443` 做 TLS 拦截，LibreSSL 握手失败（`SSL_ERROR_SYSCALL`）。`gh auth` keyring token 失效；`gh auth refresh` 需要交互模式。

### 执行步骤

**前置：确认网络可达**
```bash
curl -s --max-time 5 -H "Authorization: token <TOKEN>" https://api.github.com/user | python3 -m json.tool | grep login
```

**推送 commit `0e38532`（workflows 已在其中）**
```bash
cd /Users/denny/projects/simple-harness-sdk
git log --oneline -3   # 确认 0e38532 在最顶
git push origin main
```

如果 `gh auth` token 失效，先重新登录：
```bash
gh auth login -h github.com   # 需要交互式浏览器授权
# 或者：
echo "<PAT_WITH_WORKFLOW_SCOPE>" | gh auth login --with-token
```

PAT 所需 scopes：`repo` + `workflow`

**验证推送成功**
```bash
gh api repos/DennyWanye/simple-harness-sdk/contents/.github/workflows 2>&1 | python3 -m json.tool | grep name
# 应看到 ci.yml / platform-tests.yml / release.yml 三个文件
```

---

## 4. 任务二：桌面 E2E 测试（SDK-S1～S5）

### 背景
这是 SDK v0.1.0 验收的**最后一道 required 门**。测试规范在：
```
/Users/denny/projects/simple_harness/testcase/2026-08-13-simple-harness-sdk/manual-test.md
```

需要在 **exact vendored wheel frozen backend** 上执行，使用真实 Tauri 桌面窗口点击/输入。

### 阻断原因
桌面应用 LLM 配置指向 relay server，该 endpoint 返回 HTTP 451（法律原因不可用）。需要配置可用的 LLM provider。

### 目标模型
```
deepseeker-v4-flash
```

### 环境准备

**1. 配置 LLM provider（通过 Settings UI，不要写入 config.toml 明文）**

启动应用后打开 Settings → LLM Provider，填写：
- Model：`deepseeker-v4-flash`
- Base URL：`<deepseeker API endpoint>`
- API Key：通过 UI 写入 OS Keychain（**不要**写入 `config.toml` 或任何文件）

> ⚠️ CLAUDE.md 硬约束：`config.toml` 的 `api_key` 字段必须保持空字符串 `""`。
> ⚠️ 不要写到 `.env` 或 `secrets/`（会被诊断 bundle 收集）。

**2. 启动应用（只能用这个命令）**
```bash
cd /Users/denny/projects/simple_harness
./scripts/dev.sh
```
> ⚠️ 绝对不要手动 `python main.py`，Tauri 的 `process_manager.rs` 会自动管理 backend。  
> 端口：backend=8100，Vite=5173。

**3. 验证 backend 使用了 vendored wheel**

启动日志中确认：
```
simple_harness_sdk version: 0.1.0
wheel SHA256: d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91
```

### 测试场景（按顺序执行）

证据写入：`.local-test-evidence/2026-08-16/simple-harness-sdk/<run-id>/`

#### SDK-S1 — 纯回答
精确输入：`用一句话解释什么是幂等性。`
- 新建对话，粘贴精确输入，等待 terminal
- 验证：中文一句话，不声称调用了 Tool 或 Workflow
- 对账：`root Profile=agent.general, status=completed, child_count=0, effect_count=0`
- 证据：截图 + Run ID

#### SDK-S2 — 单个只读 Tool
精确输入：`读取当前项目摘要，然后用中文告诉我重点。`
- 确认只读摘要 Tool fixture 已注册
- 验证：恰好一个 Tool 调用成功，不启动 Workflow
- 对账：`call/effect=1, settled`

#### SDK-S3 — durable 多步骤任务（需要 2 个独立 root）
精确输入：`分析这个项目的测试缺口，形成计划，执行获准的检查并给出可审计结论。`
- Run A：新会话执行，HITL 出现时真点击批准
- Run B：在已有 **≥10 轮历史**的 Product Session 再执行同一输入
- 验证：两个 root 独立，child lineage 可恢复

#### SDK-S4 — Personal 候选绑定（需要 2 个独立 root）
Fixture：`weekly_work_planner` + `fitness_training_coach`  
精确输入：`安排下周项目优先级，并提醒我周五做一次复盘。`
- 验证：模型选择 `workflow.personal_v1` + 正确 candidate ID
- 第二个 root 重跑一次；另做伪造 graph/version 的受控 negative

#### SDK-S5 — Capability 缺口构建（需要 2 个独立 root）
精确输入：`完成一项当前 catalog 没有能力处理、且允许安装新能力的任务。`
- 先建立安全目标 `fixture.text.normalize` 的历史上下文
- 在 install/activate 前真点击授权
- 验证：`capability_build` workflow 绑定 durable_task 特化

### 证据要求（每个 root）
每步操作前记录：`坐标=(x,y)|动作=...|期望=...`，随后截图。  
证据文件命名：`<scenario>-<run-id>-<step>.png`

禁止：WebSocket 注入、直接 import backend、pytest 回放。

---

## 5. 文件位置速查

| 路径 | 用途 |
|------|------|
| `/Users/denny/projects/simple-harness-sdk/` | SDK 仓库 |
| `/Users/denny/projects/simple-harness-sdk/.github/workflows/` | 待推送的 3 个 workflow 文件（本地已有） |
| `/Users/denny/projects/simple-harness-sdk/dist/` | release artifacts（wheel + sdist + SHA256SUMS） |
| `/Users/denny/projects/simple_harness/backend/vendor/` | 产品 vendored wheel |
| `testcase/2026-08-13-simple-harness-sdk/manual-test.md` | 完整测试规范 |
| `plans/2026-08-13-simple-harness-sdk/acceptance.md` | 验收标准 |
| `.local-test-evidence/` | 测试证据写入目录（gitignored） |

---

## 6. 完成条件

- [ ] `git push origin main` 成功，`github.com/DennyWanye/simple-harness-sdk` 的 `main` 分支包含 `.github/workflows/` 目录
- [ ] SDK-S1 PASS（截图 + Run ID 证据）
- [ ] SDK-S2 PASS（截图 + Run ID 证据）
- [ ] SDK-S3 PASS，两个独立 root（截图 + Run ID 证据）
- [ ] SDK-S4 PASS，两个独立 root（截图 + Run ID 证据）
- [ ] SDK-S5 PASS，两个独立 root（截图 + Run ID 证据）
- [ ] 所有证据写入 `.local-test-evidence/2026-08-16/simple-harness-sdk/`

以上全部完成后，SDK v0.1.0 验收关闭。

---

## 7. 安全约束（必须遵守）

- ⚠️ 产品仓库 `git@github.com:DennyWanye/deskpet` 是**私有仓库**，不得推送到任何公开位置
- ⚠️ API key / token 不得写入 `config.toml`、`.env`、`secrets/` 目录
- ⚠️ 截图中不得包含密码、API key、token、cookie 或完整 Provider body
- ✅ SDK 仓库 `github.com/DennyWanye/simple-harness-sdk` 可以正常操作
