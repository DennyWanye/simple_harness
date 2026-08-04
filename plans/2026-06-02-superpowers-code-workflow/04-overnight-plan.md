# 过夜自主执行计划 — 2026-06-02 夜

> **作者**: Claude（应用户"制定一个能跑一晚上的计划"）
> **范围（用户选定）**: A superpowers 收尾 + C 质量加固 + B1/B2/B3 项目级欠账
> **验证（用户选定）**: 代码 + 单测自验;**每块尝试真机 E2E**,app 崩/登录失效导致卡住的
> 留早上 checkpoint。
> **基线**: master @ `19e17d8`（superpowers Layer 1A+①②③ 已 push）。

---

## 执行纪律（自主跑必须遵守）

1. **每个 WI 闭环**：实现 → 单测绿 → tsc/lint 绿（涉前端）→ 尝试真机 E2E → **小步 commit**。
2. **不绿不算完**：测试红就修或回滚，绝不"看起来对就过"。
3. **真机 E2E 失败 ≥3 次不同 workaround** 才标"留早上",并记具体障碍。
4. **merge 风险控制**（B2/B3）：`git merge --no-commit --no-ff` 先看冲突；冲突非平凡或
   测试红 → `git merge --abort` + 标"留早上人工 merge"，**绝不强 merge**。
5. **push 策略**：Phase A/C（低风险自验）阶段末 push；**Phase B merge 只本地 commit、
   不 push**，留早上 review。不 `push --force`。
6. **安全护栏**：不 `rm -rf` / 不写 secrets / 不动 `.env` / 破坏性操作跳过留早上。
7. **进度落盘**：每个 WI 完成即更新本文件 §进度日志 + 必要时 STATUS。崩溃可续。
8. **dev app**：真机 E2E 块前用既定 env 启动（DESKPET_BACKEND_DIR + CDP 9222），
   测完不强杀（留 checkpoint 用）。登录 token 在 keychain，重启通常仍在。

---

## WI 分解（按价值×低风险排序；B 最后，最坏只是没做）

### Phase A — superpowers 收尾（与本 session 连贯，低风险）

- **WI-A1 意图记忆接线（决策1）**
  - record from outcome：`_run_chat` agent loop 后，本轮有 tool_call → `record(text,"task","intent")`，否则 `"ask"`（仅 in_code_mode + pref_mem 存在 + 非 sentinel）。
  - match before turn：turn 起始 `match(text,"intent")`，命中 "ask" → 注入 system hint
    "用户以往把这类消息当纯提问，直接回答、别调工具改动"；命中 "task" → "这类是派活，进工作流"。
  - 误记防护：只在"干净轮"记（无 error）；高分才注入。
  - 验收：单测（record/match/hint 注入纯函数）+ 真机（问模型→记 ask→再问类似→零工具直答）。

- **WI-A2 `/prefs` slash 命令**
  - `dispatch_slash`：`/prefs`（list 意图+计划记忆）、`/prefs clear [intent|plan]`。
  - 复用 `PreferenceMemory.list_entries/clear`（已就绪）。前端 slash_command_result 渲染。
  - 验收：单测 dispatch_slash /prefs + 真机 `/prefs` 看到条目、`/prefs clear` 清空。

- **WI-A3 verify_gate 出厂默认决策落地**
  - 出厂 `verify_gate_mode` off→shadow + `emit_receipts` false→true（invariant 要求）。
  - ⚠️ 涉所有用户：**先实现为可选,在本文件记利弊,最终翻不翻留早上确认**（不擅自改出厂默认）。
  - 验收：单测 invariant + 启动不报错。

- **WI-A4 plan 消息持久化（边角）**
  - 现 rehydration 丢 awaiting plan。轻量修：awaiting plan 也写 SessionDB / 或 rehydration 保留。
  - 若改动面大 → 降级为"记 known issue"留早上。验收：单测 + 真机 rehydrate 后按钮还在。

- **WI-A5 文档**
  - README/docs 增 superpowers code 工作流段（3 flag + 行为 + 如何开）。docs/SKILLS 若相关。
  - 验收：文档自洽、链接有效。

### Phase C — 质量加固

- **WI-C1 全套 backend pytest 跑一遍 + 修红**（排除已知 flaky；记录 baseline）。
- **WI-C2 新代码边角覆盖**：plan 门 timeout/cancel、preference_memory embed 失败/空、
  verify strict nudge 耗尽→ephemeral→放行 路径。
- **WI-C3 tsc + 前端 lint 全绿；新 py 文件 ruff/lint 清。**

### Phase B — 项目级欠账（风险最高，最后）

- **WI-B1 修 flaky `test_enqueue_small_batch_flushes_on_interval`**（time-based → 注入时钟/
  事件确定化）。验收：连跑 20 次全绿。
- **WI-B2 merge `feat/memory-stage2-followup-f1f2` → master**（--no-commit 看冲突；测试绿才提；
  冲突非平凡 abort 留早上）。
- **WI-B3 merge `feat/companion-code-v2` → master**（同 B2 纪律）。

---

## 早上 checkpoint 清单（人工/有人值守才做）

- 真机 E2E 复核：意图记忆学习行为、`/prefs` UI、（若 merge）合并后功能 smoke。
- verify_gate 出厂默认是否翻 shadow（WI-A3）拍板。
- Phase B merge 的 push（overnight 只本地 commit）。
- 过夜卡住项（标 "留早上" 的）逐个过。

---

## 进度日志（自主跑时追加，倒序）

_（开始执行后在此追加每个 WI 的结果 + commit hash + 测试证据）_
