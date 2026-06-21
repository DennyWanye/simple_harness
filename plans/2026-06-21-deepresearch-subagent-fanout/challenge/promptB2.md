# 任务：第 2 轮对抗验证（只读）——核实 v0.2 的 paths/打包/index/config 修复是否真对 + 猎杀新问题

这份 plan 刚吸收第 1 轮挑战升到 v0.2。你聚焦 **WI-8（落盘/路径/index/打包）+ WI-5 的 config 读取**。两步：(1) 逐条验证 v0.2 修复；(2) **猎杀修复引入的新问题**（重点）。必须读真源码。

## plan（精读 v0.2，尤其 WI-8a/c/e、WI-5 helper、§13）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 必须读码核对
- `G:/projects/deskpet/backend/paths.py`（_install_dir:50 / _portable_userdata_dir:57 / user_data_dir:123 / output_dir:141 / 顶部 import）
- `G:/projects/deskpet/backend/deskpet/tools/research_tools.py`（_save_report:1770 / _handle_deepresearch 落盘块 1753-1765 / 顶部 import os,time,asyncio,math / _research_raw 实现）
- `G:/projects/deskpet/backend/config.py`（`[agent]`/`[research]` raw 结构、lane_caps 嵌套键、resolve_config_path/load_config）
- `G:/projects/deskpet/tauri-app/src-tauri/tauri.conf.json`（nsis/msi、install mode）
- `G:/projects/deskpet/README.md`（结构，WI-8d 插入点）

## 第 1 步：逐条验证（VERIFIED-CORRECT / STILL-WRONG+证据）
1. `_install_root_for_deepresearch()`：`_install_dir()` frozen 返回 exe 父，Tauri layout 下 backend exe 父是 `<install>/backend/`，`base.name=="backend"→root=base.parent` 对吗？probe `<root>/DeepResearch` 写删逻辑是否健全（mkdir+write+unlink 的异常面）？
2. dev 分支 `Path(__file__).resolve().parents[1]`=repo 根——paths.py 在 `backend/paths.py`，parents[1] 确切是 `G:/projects/deskpet` 吗？
3. 兜底 `<home>/DeskPet/DeepResearch` 不落 Roaming——确认不再调用 user_data_dir()，且 Path.home() 在 Windows 返回什么（会不会还是 C:\Users\x，是否违背用户"不进 C 盘"？请明确说明 home 兜底是否可接受，还是该改成报错要求 env）。
4. WI-8c index：`asyncio.Lock` + utf-8 + os.replace + 主题转义——`_INDEX_HEADER` 有没有在 plan 里定义？`_insert_row_after_header` 有没有定义？async 化后 `_handle_deepresearch` await 调用点对吗？
5. WI-8e 打包：NSIS/MSI 安装位置判断是否准确（Tauri NSIS 默认 installMode）？

## 第 2 步：猎杀新问题（重点）
- `_agent_raw()` 读 `config.raw["agent"]["concurrency"]["lane_caps"]["research"]`：核对 config.toml / config.py 里这个段的**真实嵌套结构和键名**（是 `[agent.concurrency]` 还是别的？lane_caps 是 dict 还是别的形态？）。读错路径会怎样？
- research_tools.py 顶部是否已 `import os`、`import math`、`import asyncio`、`import time`？WI-2/WI-8c 用到这些；缺哪个要在 plan 里点明补 import。
- `_DEEPRESEARCH_TOOL_TIMEOUT` 常量与注册处 :1870 `timeout_seconds` 共用——改 :1870 引用常量时，常量定义必须在注册调用之前（模块加载顺序）。plan 是否保证定义在 `_register_deepresearch_tool()` 之前？
- `deepresearch_dir()` 每次调用都 mkdir + （安装根分支）probe 写删——`_handle_deepresearch` 每次 deepresearch 都触发，频繁 probe I/O 是否可接受？probe 文件名固定会不会并发互删？
- README.md 已有 `DeepResearch.md`（STATUS 文档链接，见 README:8）——WI-8d 要加的 `DeepResearch/`（产物目录）与它**同名易混**，plan 是否需要消歧（目录 vs 状态文档）？
- 落盘根从 `OutPut/Research` 改到 `DeepResearch/`：现有是否有别的代码/测试/前端 ArtifactCard 依赖旧 `OutPut/Research` 路径？搜一下。

## 输出格式
第 1 步逐条 VERIFIED-CORRECT/STILL-WRONG；第 2 步新问题 `[BLOCKING|MAJOR|MINOR]`+证据+修法；末尾 `VERDICT: ...`。
特别明确回答："home 兜底是否仍算落 C 盘、是否可接受" 和 "_agent_raw 的 lane_caps 嵌套路径是否与真实 config 结构一致"。
