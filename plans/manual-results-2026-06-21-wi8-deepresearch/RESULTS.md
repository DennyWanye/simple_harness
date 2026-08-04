# Phase 1 (WI-8) windows-mcp 真机测试结果

> 日期：2026-06-21 16:00–16:16
> 被测：deepresearch 报告落 `DeepResearch/`（安装目录下，dev=repo 根）+ `DeepResearch/index.md` 总索引
> 环境：Tauri dev（`npm run tauri:dev`），backend = **Dev python 本树**（`[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`，非 frozen），`Application startup complete` + `Uvicorn on 127.0.0.1:8100`；relay **已连接**（顶栏「已连接」，尽管 Windows 网络标"无法访问 Internet"，relay 走代理可用）；DeepResearch/ 测试前不存在（干净基线）。
> 测试方式：windows-mcp 真模拟人工——剪贴板设中文 → Click 输入框(2935,1509) → Ctrl+V → Click 发送(3203,1509)；落盘文件 + index.md 内容 + backend log 为判定证据。

## 结果汇总

| case | 判定 | 证据 |
|---|---|---|
| ENV-WI8-00 Dev python 门禁 | ✅ PASS | log `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe`，非 Bundled exe |
| TC-WI8-01 happy 落点 repo 根 | ✅ PASS | 真发「钠离子电池…带引用报告」→ 145s 后 `G:\projects\deskpet\DeepResearch\钠离子电池…-1782029545.md`(8614B)，头部「调研覆盖 2 个来源 来自 1 个独立域名…引用自检 通过」+ TL;DR + `[^2]`(搜狗百科直连源) |
| TC-WI8-02 index 首建表头 | ✅ PASS | `DeepResearch/index.md` 自动创建：`# DeepResearch 报告索引` + 说明 + 表头 `\| 日期 \| 主题 \| 文件 \| 来源数 \| 域名数 \| 模式 \| 子问题 \|` + 分隔行，全对 |
| TC-WI8-03 第二份倒序+相对链接+列值+模式列 | ✅ PASS | 第 2 次研究后 index 2 行：**16:14 新行在分隔行正下方、16:12 旧行下移**(倒序)；两行文件列均 `[name.md](name.md)` 可点击相对链接；**模式列均 `flat`**（模式列泄漏档位 bug 修复真机验证——cov["mode"]=深度档但正确显示 flat）；列值 6/3、2/1 正确；无重复 |
| TC-WI8-05 中文 UTF-8 不乱码 | ✅ PASS | 中文文件名 `钠离子电池…-1782029545.md`、index 主题列、报告正文中文全可读，无 mojibake/问号 |
| TC-WI8-07 artifact 结果卡片 | ✅ PASS | 聊天出现「✓ deepresearch 结果」结果卡片（handler 返回 artifacts[] path 指向 DeepResearch/.md，代码 + 卡片双证） |
| TC-WI8-08 负向：非旧路径/非 AppData | ✅ PASS | `backend/OutPut/Research/` 无 16:12/16:14 新文件（仅旧文件）；`%AppData%\deskpet\OutPut\Research` 空——本次报告**未落旧路径/不进 C 盘 AppData** |
| TC-WI8-04 幂等去重 | ✅ 单测覆盖 / UI best-effort | TG-6 `test_update_deepresearch_index_*`（同文件名只 1 行）；UI 真机两份报告各 1 行无重复（间接佐证） |
| TC-WI8-06 `\|`/换行转义 | ✅ 单测覆盖 / UI best-effort | TG-6 转义断言；真机主题含「：」等特殊符未破坏表格（7 列结构完好） |
| TC-WI8-09 env 覆盖 | 🟡 单测覆盖（未跑真机重启） | TG-6 `deepresearch_dir` env 分支断言；真机需重启 Tauri 注入 `DESKPET_DEEPRESEARCH_DIR`，为纯 env-var 分支、风险低，未单独重启验证 |

## 关键铁证：DeepResearch/index.md（2 份报告后）

```
# DeepResearch 报告索引

运行时自动生成的 DeepResearch 报告总索引，新报告按倒序插入。

| 日期 | 主题 | 文件 | 来源数 | 域名数 | 模式 | 子问题 |
|---|---|---|---|---|---|---|
| 2026-06-21 16:14 | Sodium-ion batteries ... | [Sodium-ion-batteries-work-principle-indu-1782029683.md](...) | 6 | 3 | flat | 6 |
| 2026-06-21 16:12 | 钠离子电池的工作原理、产业现状与代表企业… | [钠离子电池…-1782029545.md](...) | 2 | 1 | flat | 6 |
```

## 观察（非 WI-8 缺陷）

- 第 2 次输入「固态电池」但桌宠调研漂回 sodium-ion（钠离子）——**已知任务漂移**问题（属 task-drift-fix plan），**不影响 WI-8**：第 2 份报告照常落盘 + 入索引。
- 报告来源数偏少（2/6）——Windows 网络标"无法访问 Internet"，google-cdp/wiki 等海外源不可达，仅国内直连源（搜狗百科等）+ relay 生效；**不影响 WI-8 落盘/索引验收**（有 citations 即落盘）。

## 判定

**WI-8 核心功能经 windows-mcp 真模拟人工点击+输入测试全部 PASS**：报告落安装目录 DeepResearch/（dev=repo 根，不进 C 盘 AppData/旧 OutPut/Research）+ index.md 倒序索引（表头/相对链接/模式列 flat/中文/列值全对）。设计阶段抓出并修复的模式列泄漏档位 bug 真机复验生效。
