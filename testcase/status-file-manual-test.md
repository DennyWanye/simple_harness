# 手工测试用例 — STATUS 全局项目状态文件 + 更新纪律

> **被测功能**: `STATUS/status.md` 全局项目状态文件 + README/CLAUDE.md 链接 + STATUS 更新纪律
> **对应交付 commit**: `704d114`（docs(status): 新增全局项目状态文件 + STATUS 更新纪律）
> **测试类型**: 文档存在性 + 链接可达性 + 纪律可执行性（纯人工核对，无需 windows-mcp）
> **测试环境**: master 工作树 `G:\projects\deskpet`，任意 Markdown 阅读器（VS Code / GitHub 网页 / typora 均可）
> **最后更新**: 2026-05-31

---

## 测试前置准备

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| P-1 | 用 VS Code 打开 `G:\projects\deskpet` 工作区 | 资源管理器能看到根目录 `STATUS/`、`README.md`、`CLAUDE.md` |
| P-2 | 确认在 master 分支：终端跑 `git branch --show-current` | 输出 `master` |
| P-3 | 确认已是最新提交：`git log --oneline -1` | 出现 `704d114 docs(status): 新增全局项目状态文件 + STATUS 更新纪律`（或更新的 commit） |

---

## TC-01 — STATUS 文件存在且结构完整

**目的**: 验证全局状态文件已创建且包含全部 7 个章节。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 打开 `STATUS/status.md` | 文件存在，可正常打开 |
| 2 | 查看文件顶部 | 第一行标题 `# DeskPet — 全局项目状态`；下方有 `**最后更新**:` 日期行 |
| 3 | 逐章核对小标题 | 依次出现 7 个章节：①项目一句话 ②活跃工作流 ③核心功能模块完成度 ④最近里程碑 ⑤已知问题/测试纪律 ⑥文档索引 ⑦如何更新本文件 |
| 4 | 查看 §2 表格 | 有"Worktree / 分支 \| 负责模块 \| 状态 \| 文档"四列，且每行状态列是 ✅ 或 🟡 |
| 5 | 查看 §3 表格 | 有"模块 \| 状态 \| 关键文档"三列，覆盖语音管线/记忆/工具层/技能等模块 |

**判定**: 7 章节齐全 + §2/§3 表格列完整 → **PASS**；缺任一章节 → **FAIL**

---

## TC-02 — README 顶部 STATUS 链接存在且可跳转

**目的**: 验证用户/开发者从 README 入口能找到并跳到 STATUS 文件。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 打开 `README.md` | 文件正常打开，首行为 `# DeskPet` |
| 2 | 看简介段（前 6 行内） | 出现 📊 引用块，文字含"项目整体状态一页看清"，链接 `STATUS/status.md` |
| 3 | 在 VS Code 中 `Ctrl+点击` 该链接（或 GitHub 网页直接点） | 跳转到 `STATUS/status.md`，正确打开，不是 404 |

**判定**: 链接存在 + 点击成功跳转到 status.md → **PASS**；链接缺失或 404 → **FAIL**

---

## TC-03 — CLAUDE.md 顶部 STATUS 链接存在且可跳转

**目的**: 验证子代理/助手接手时从 CLAUDE.md 能找到状态文件。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 打开 `CLAUDE.md` | 首行为 `# CLAUDE.md — DeskPet 项目级 Claude 工作笔记` |
| 2 | 看前 8 行 | 出现 📊 引用块"接手前先读全局状态"，链接 `STATUS/status.md` |
| 3 | `Ctrl+点击` 该链接 | 跳转到 `STATUS/status.md`，正确打开 |

**判定**: 链接存在 + 跳转成功 → **PASS**；否则 **FAIL**

---

## TC-04 — STATUS 更新纪律章节存在且措辞为 HARD

**目的**: 验证"任务完成必须更新 STATUS"的纪律已正式写入 CLAUDE.md。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 在 `CLAUDE.md` 中查找标题 `## ✅ STATUS 更新纪律` | 该章节存在，标记 `（HARD — 不可妥协）` |
| 2 | 阅读"触发条件" | 写明：WI/slice/功能模块跑通验收（pytest/vitest/cargo/手工 E2E 全绿）= "完成" |
| 3 | 阅读"强制动作" | 列出 4 条：更新 §3 完成度 / 追加 §4 里程碑 / merge 时改 §2 / 改顶部日期 |
| 4 | 阅读"判定"行 | 出现"改了代码/跑过测试但没更新 STATUS = 任务未完成" |
| 5 | 打开 `STATUS/status.md` §7 | 标题为"如何更新本文件（HARD 纪律）"，含铁律措辞 + 反向链接回 CLAUDE.md |

**判定**: CLAUDE.md 纪律章节 4 条强制动作齐全 + status.md §7 呼应 → **PASS**；否则 **FAIL**

---

## TC-05 — STATUS 文件内部相对链接有效性抽查

**目的**: 验证 status.md 内引用的 plan / 文档相对路径不指向死链。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 在 `STATUS/status.md` §6 文档索引中，`Ctrl+点击` `../docs/INDEX.md` | 跳转到 `docs/INDEX.md`（若该文件存在则打开；不存在记为已知缺口，见判定） |
| 2 | `Ctrl+点击` `../README.md` 链接 | 跳转回根 README，正常打开 |
| 3 | `Ctrl+点击` `../CLAUDE.md` 链接 | 跳转回根 CLAUDE.md，正常打开 |
| 4 | 抽查 §2 中任一 plan 链接（如 `../plans/2026-05-25-companion-code-skill-upgrade/`） | 跳转到对应 plan 目录，目录存在 |

**判定**: §6 的 README/CLAUDE.md 链接 + §2 抽查 plan 链接全部可达 → **PASS**；
docs/INDEX.md 若确实不存在，记为 **PASS-with-note**（在报告里注明该文档待补，不算功能 FAIL）。

---

## TC-06 — 纪律可执行性演练（模拟一次"任务完成 → 更新 STATUS"）

**目的**: 端到端走一遍纪律，验证它真的可被执行、不含糊。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 假设场景：某 worktree 的"X 模块"刚跑通验收。照 CLAUDE.md 纪律找出该改哪几处 | 能明确定位到 status.md §3（改状态）+ §4（加里程碑）+ 顶部日期，无歧义 |
| 2 | 在 status.md §3 临时把某行 🟡 改成 ✅（演练用，**不保存**或改完 `git checkout` 还原） | 能顺利定位到对应行并修改 |
| 3 | 还原改动：`git checkout STATUS/status.md` | 文件恢复原状，`git status` 干净 |

**判定**: 纪律指向的更新位置清晰可定位、演练顺畅 → **PASS**；纪律含糊到不知改哪 → **FAIL**

---

## 测试结果汇总表（执行时填写）

| 用例 | 判定 | 备注 |
|---|---|---|
| TC-01 STATUS 结构完整 | ☐ PASS / ☐ FAIL | |
| TC-02 README 链接 | ☐ PASS / ☐ FAIL | |
| TC-03 CLAUDE.md 链接 | ☐ PASS / ☐ FAIL | |
| TC-04 更新纪律章节 | ☐ PASS / ☐ FAIL | |
| TC-05 内部相对链接 | ☐ PASS / ☐ PASS-with-note / ☐ FAIL | |
| TC-06 纪律可执行性 | ☐ PASS / ☐ FAIL | |

**整体结论**: ☐ 全 PASS（功能交付合格） / ☐ 有 FAIL（需修复后复测）
