# CODEX 任务：Phase 1 — 把工具 research_run 更名为 deepresearch

你在 DeskPet 仓库（G:/projects/deskpet）。只做 Phase 1（更名），不做其它 Phase。

## 必读
先读 `plans/deepresearch-upgrade/00-upgrade-plan.md` 的 §1.3、§2（R1-R12 表）、§2.1、§4。严格照它执行。

## 要改的（代码级，逐处）
1. **R1** `backend/deskpet/tools/research_tools.py:1520`：`_RESEARCH_SCHEMA["name"]` 从 `"research_run"` 改 `"deepresearch"`。
2. **R2** 同文件 :1721：`registry.register("research_run", ...)` 的注册名改 `"deepresearch"`。
3. **R3** 同文件 :1716/:1736：`_register_research_tool` → `_register_deepresearch_tool`（含模块级调用）。
4. **R4** 同文件 :967：`async def research_run(` → `async def deepresearch(`；在文件**末尾**加一行 `research_run = deepresearch  # deprecated alias: use deepresearch`。同步把 :1600 内部 `await research_run(...)` 改成 `await deepresearch(...)`。
5. **R5** 同文件 :1574/:1724：`_handle_research_run` → `_handle_deepresearch`（定义 + 注册引用）。
6. **R7** `backend/deskpet/tools/code_tools/registration.py:128`：字符串里「请改用 research_run」→「请改用 deepresearch」。
7. **R8** `backend/deskpet/skills/builtin/deep-research/SKILL.md`：把所有 `research_run` 改 `deepresearch`（含「## 2. 调 research_run 工具」标题）；frontmatter `version: 0.2.0` → `0.3.0`。
8. **R12** `backend/deskpet/skills/builtin/ppt-generate/SKILL.md:117`：把 `research_run` 改 `deepresearch`。
9. **R9** `backend/main.py` :639 / :1069 注释里的 research_run 改 deepresearch（仅注释）。
10. **R11** `backend/scripts/e2e_ppt_deepresearch.py`：import 和调用从 research_run 改 deepresearch（靠别名本可跑，但同步改新名更干净）。

## 硬约束（不许做）
- **不要**改 `[research]` 配置段名（它是领域配置：site_directed/query_expansion/direct_sources/js_render/reranker/search_engines 等，不是工具名）。`_research_raw()` 读的就是它，改了会全失配。
- 别名 `research_run = deepresearch` 必须保留（让旧 import 不破）。

## 漏网核查（改完跑）
```
grep -rn 'research_run' backend/deskpet/ --include='*.py' --include='*.md' --include='*.json'
```
凡是「按工具名字符串引用」的旧名残留全部改掉（注释/docstring 可保留历史但建议一并更新）。确认无遗漏。

## 验收（必须达到）
1. `cd backend && .venv/Scripts/python.exe -m pytest tests/test_deskpet_research_tools.py -q` → 仍 **75 passed**（别名保证 import 不破）。
2. **新增测试**到 `backend/tests/test_deskpet_research_tools.py`：注册工具后断言
   - registry 里能取到 `"deepresearch"`；
   - registry 里取不到 `"research_run"`（注册名已换）；
   - 模块别名成立：`from deskpet.tools.research_tools import research_run, deepresearch; assert research_run is deepresearch`。
   （如何拿 registry：参考该文件/research_tools.py 里 `_register_deepresearch_tool` 用的 registry，或直接 import 全局 registry 调 get。）
3. 跑全 research 测试通过（含新增）。

## 完成后
输出：改了哪些文件、新增测试名、pytest 结果（passed 数）。不要 commit（我来 commit）。
