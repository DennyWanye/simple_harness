# COLD-1 真机测试步骤卡 — 干净用户数据目录冷启动 + 主聊天首条消息

> 绑定：**COLD-1 场景 / S1-AC-6 / TO-S1-6**（`plans/2026-08-19-sdk-usability-optimization/acceptance.md` 测试场景矩阵，`manual_required=是`，`gate_type=positive-value`）
> 终端期望：**SDK runtime ready + 非空 assistant 回复出现在前端**
> 性质：真机验证，非脚本 replay（acceptance 原文："真机验证，非脚本 replay"）。本卡由真人在真机上逐步执行并留证。
> 平台：macOS ARM64（assurance OOS-2：非 macOS 平台范围外）。

---

## Step 0 — 前置清场

**动作**：确认没有残留的 app / dev 实例。

```bash
lsof -ti:5173 -ti:8100 || echo "ports free"
```

**通过判据**：5173 与 8100 均无占用（dev.sh 自己也会清理，但执行人需确认起始状态干净，避免把旧实例的日志误判为冷启动日志）。

---

## Step 1 — 定位并移走用户数据目录

macOS 下 Tauri 应用的用户数据目录**可能位置**（本项目 fork 自 DeskPet，identifier 可能仍带 deskpet 字样，black-box 下不预设确切路径）：

- `~/Library/Application Support/` 下匹配 `*harness*` 或 `*deskpet*` 的目录（最常见）
- `~/.simple_harness/` 或 `~/.deskpet/`（dotdir 风格）
- `~/Library/Caches/`、`~/Library/Preferences/` 下同名条目（可选，主数据在 Application Support）

**动作**：

```bash
# 1) 发现实际数据目录（以真实输出为准，记录到证据）
ls -d ~/Library/Application\ Support/* 2>/dev/null | grep -iE 'harness|deskpet'
ls -d ~/.simple_harness ~/.deskpet 2>/dev/null

# 2) 备份移走（示例，路径以第 1 步真实发现为准）
TS=$(date +%Y%m%d-%H%M%S)
mv "<发现的数据目录>" "<发现的数据目录>.bak-cold1-$TS"

# 3) 如是首次执行、任何位置都找不到数据目录：记录"无既有数据目录，天然干净"，直接进入 Step 2
```

**通过判据**：所有发现的用户数据目录均已移走（或确认本机从未生成过），终端留有发现/移走记录可截图。
**数据安全**：只 `mv` 不 `rm`（assurance 最大可接受影响：不得导致用户数据丢失/损坏）。测试结束后可用备份目录还原。

---

## Step 2 — 干净状态冷启动

**动作**：在仓库根目录执行：

```bash
./scripts/dev.sh 2>&1 | tee /tmp/cold1-dev-$TS.log
```

等待 Tauri 窗口出现、Vite 前端（5173）与后端（8100）就绪。

**通过判据**：
1. dev.sh 未因端口冲突/venv 缺失退出；
2. 桌面应用窗口真实弹出并渲染出 Workbench UI（非白屏）；
3. 启动日志中**无** fail-closed 拒绝（如出现 SHA mismatch / wheel 校验失败 / `verify_sdk_candidate` 报错字样，直接判 FAIL——这同时命中 S1-AC-2 的正向面）。

---

## Step 3 — 观察 SDK runtime ready slot

**动作**：在 `/tmp/cold1-dev-$TS.log`（dev.sh stderr 并入）中检索：

```bash
grep -n "sdk_runtime_ready" /tmp/cold1-dev-$TS.log
```

**通过判据**：
1. 日志中出现 `sdk_runtime_ready`（ready slot 已发布）；
2. 其后无与该 slot 相关的 error/exception 回滚记录。
**证据**：`grep -n -C2 "sdk_runtime_ready"` 输出摘录，存入 `evidence/cold-1-sdk-runtime-ready-<timestamp>.log`。

---

## Step 4 — 主聊天发送首条消息（真人真机）

**动作**：在应用主窗口的**主聊天**输入框中真人键入并发送：

```
你好，介绍下你自己
```

（与既有脚本 `scripts/e2e/e2e_text_chat.py` 的首条 prompt 一致，便于交叉对照——但本步必须是真人 UI 操作，不是脚本回放。）

**通过判据**：
1. 消息出现在聊天流中（user 气泡可见）；
2. **有限时间内**（建议上限 120 秒，考虑首次冷启动模型/relay 延迟）出现**非空 assistant 回复**——有实际文本内容，不是空气泡、不是错误占位、不是 "budget_exceeded" 类降级态；
3. 过程中窗口不崩溃、不白屏、后端进程不退出。

**判 FAIL 的任一信号**：超时无回复 / 回复为空 / 前端报错 toast / 后端 traceback 指向 SDK runtime 装配失败。

---

## Step 5 — 证据归档

**动作**：将以下证据放入 `testcase/2026-08-19-sdk-usability-optimization/evidence/`：

| 证据 | 内容 | 建议文件名 |
|------|------|-----------|
| 截图 | 主聊天中 user 消息 + 非空 assistant 回复同框 | `cold-1-screenshot-<ts>.png` |
| 日志摘录 | `sdk_runtime_ready` 行及上下文（Step 3） | `cold-1-sdk-runtime-ready-<ts>.log` |
| 数据目录记录 | Step 1 的目录发现/移走命令与输出 | 附在 evidence 目录一个 `.txt` |
| 启动日志 | 完整 dev.sh 输出（`/tmp/cold1-dev-$TS.log` 拷贝） | `cold-1-dev-full-<ts>.log` |

**通过判据**：四项证据齐全且内容可复核（截图里 assistant 文本肉眼非空；日志含 `sdk_runtime_ready`）。

---

## 总结判（COLD-1 PASS 条件）

Step 0–5 全部通过，且：
- SDK runtime ready slot 就绪（Step 3）
- 主聊天一条消息得到非空 assistant 回复（Step 4）

任一 Step 判 FAIL 则 COLD-1 整体 FAIL，S1-AC-6 不通过，Slice 1 不得进入 Task 10 文档回写。

## 善后

- 测试结束后关闭 app（dev.sh 终端 Ctrl-C，Rust 侧会连带回收后端与 vite）。
- 如需还原个人数据：退出 app 后将 `*.bak-cold1-<ts>` 目录 `mv` 回原路径。
