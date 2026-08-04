# 任务：实现 DeepResearch 落盘目录 + 索引（plan Phase 1 = WI-8）

你是编码 Expert。严格按已锁定 plan 的 **WI-8** 实现，不要少做、不要自由发挥。

## 先读（必须）
1. plan（含 WI-8a~8e 的精确代码片段与 file:line）：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`（重点 §7 的 WI-8 全部、§13 修订记录里 R2/R3/R4 关于 WI-8 的修正）
2. 现状源码：
   - `G:/projects/deskpet/backend/paths.py`（`_install_dir`:50 / `_portable_userdata_dir`:57 / `user_data_dir`:123 / `output_dir`:141；顶部 import）
   - `G:/projects/deskpet/backend/deskpet/tools/research_tools.py`（顶部 imports:34 / `_save_report`:1770 / `_handle_deepresearch`:1711 落盘块:1753-1765 / schema 描述:1655）
   - `G:/projects/deskpet/backend/deskpet/skills/builtin/deep-research/SKILL.md`（:86）
   - `G:/projects/deskpet/README.md`（结构，:8 已有 DeepResearch.md 链接）

## 要实现的（严格按 plan WI-8）
### WI-8a — `backend/paths.py`
- 新增 `deepresearch_dir() -> Path`：解析顺序 ① env `DESKPET_DEEPRESEARCH_DIR` 覆盖 → ② frozen 安装根 `_install_root_for_deepresearch()` → ③ dev `Path(__file__).resolve().parents[1]/"DeepResearch"`（repo 根）→ ④ 兜底 `Path.home()/"DeskPet"/"DeepResearch"` + `logger.warning`（**绝不回落 user_data_dir()/%AppData%**）。每分支 `mkdir(parents=True, exist_ok=True)`。
- 新增 `_install_root_for_deepresearch() -> Path|None`：`base=_install_dir()`；None→None；`root = base.parent if base.name.lower()=="backend" else base`；`target=root/"DeepResearch"`；mkdir + 用**唯一名** probe `target/f".deskpet-dr-write-probe-{os.getpid()}"`（write_bytes(b"")+unlink）；成功 return target，OSError→None。**直接 probe DeepResearch 本身**（不要借 userdata 的可写性）。
- paths.py 顶部确认有 `import os`（:37 已有）。logger：paths.py 若无 logger，用 `import logging; logger=logging.getLogger(__name__)`（核对现状，若已有就复用）。

### WI-8b — `research_tools.py` 落盘切换 + 旧文案清理
- `_save_report`（:1770）：`from paths import output_dir; base = output_dir("Research")` 改为 `from paths import deepresearch_dir; base = deepresearch_dir()`；文件名仍 `<slug>-<ts>.md`，落 `<DeepResearch>/<slug>-<ts>.md`；header 元数据不变。`_save_report` 只负责写 md + 返回 path（**不在这里更新 index**）。
- 旧文案 `OutPut/Research` → `DeepResearch/`：schema 描述（:1655）、`_save_report` docstring/注释（:1751/:1771）、`SKILL.md`（:86 "OutPut/Research/*.md" → "DeepResearch/*.md"）。

### WI-8c — `research_tools.py` 索引维护
- 顶部加 `import os`（现 imports 在 :34 区，只有 asyncio/json/logging/re/sys/time/urllib.parse —— 缺 os）。
- 新增模块级常量 `_INDEX_HEADER`（含标题+说明段+表头行 `| 日期 | 主题 | 文件 | 来源数 | 域名数 | 模式 | 子问题 |` + 分隔行 `|---|...|`）。
- 新增 `_INDEX_LOCK = asyncio.Lock()`。
- 新增 `def _insert_row_after_header(existing, row) -> str`：找 `|---|` 分隔行，在其后插 row（倒序首行）；找不到则 `_INDEX_HEADER + row + "\n"` 兜底重建。
- 新增 `async def _update_deepresearch_index(report_path, topic, report)`：`async with _INDEX_LOCK`；`idx=report_path.parent/"index.md"`；主题转义（`|`→`/`、换行→空格）；row 用 `time.strftime('%Y-%m-%d %H:%M')` + 文件名链接 + `cov.get('n_sources',0)`/`n_domains`/`mode`(默认 'flat')/`n_sub_questions`；读现有（无则 `_INDEX_HEADER`）；若 `report_path.name in existing` 幂等 return；否则 `_insert_row_after_header` → 写 `idx.with_suffix(".md.tmp")`（`encoding="utf-8"`）→ `os.replace(tmp, idx)`。try/except 包裹不影响主流程。
- `_handle_deepresearch`（:1711 落盘块 :1753-1765）：`saved = _save_report(...)` 成功后 `await _update_deepresearch_index(saved, topic, report)`（在 try/except 内，失败只 log.debug 不影响返回）。

### WI-8d — `README.md`
- 加一节说明 `DeepResearch/`（安装目录下，运行时生成）汇集所有 deepresearch 报告，`DeepResearch/index.md` 是总索引（倒序、可点开），方便复用；打包应用同样在安装目录下生成。**消歧**：明确 `DeepResearch.md`（repo 内模块状态文档）≠ `DeepResearch/`（运行时报告目录）。

### WI-8e — 打包
- 无需改 installer（运行时产物）。`deepresearch_dir()` frozen 分支已覆盖。无代码改动，仅确认。

## 测试（WI-7 的 TG-6，新建 `backend/tests/test_deepresearch_output_dir.py`）
- `deepresearch_dir()` 四分支：env 覆盖 / frozen mock（monkeypatch `sys.frozen=True`+`sys.executable` 指向可写临时安装根，断言落 `<root>/DeepResearch`）/ dev（断言 `parents[1]/DeepResearch`）/ frozen 但安装根只读 → home 兜底**且不等于 user_data_dir()**。用 `monkeypatch.setenv("DESKPET_DEEPRESEARCH_DIR", tmp)` 验 env 覆盖最高优先。
- `_install_root_for_deepresearch` 唯一 probe 名。
- `_insert_row_after_header`：空表头插入、倒序（新行在旧行上）、找不到分隔行兜底重建。
- `_update_deepresearch_index`：首建写表头、幂等去重（同文件名不重复加）、utf-8（中文主题不乱码）、并发两次 await 只插两行不丢（用 asyncio.gather 模拟）。
- `_save_report`：落点为 `deepresearch_dir()`（用 `DESKPET_DEEPRESEARCH_DIR` env 指 tmp 验证），不再 `OutPut/Research`。

## 约束 / 验收
- **不可破坏现有 83 个 research 测试**（BC）：`cd backend && .venv/Scripts/python.exe -m pytest tests/test_deskpet_research_tools.py -q` 必须仍全绿。
- 跑你新写的：`cd backend && .venv/Scripts/python.exe -m pytest tests/test_deepresearch_output_dir.py -q` 全绿。
- 中文文件用 UTF-8 编辑，勿 mojibake。
- 完成后**简要报告**：改了哪些文件、新增哪些函数/常量、两个 pytest 结果。

python 解释器：`G:/projects/deskpet/backend/.venv/Scripts/python.exe`，跑 backend 测试时 cwd 用 `G:/projects/deskpet/backend`。
