# 任务：对抗式挑战一份实施计划（只读评审，找出"不能照做"的硬伤）

你是一名严格的架构评审员。下面这份 plan 声称"100% 可被原原本本执行"。你的工作是**证伪它**——找出任何会让实现者照着做却失败、或做出错误结果的地方。**必须读真源码核对**，不要凭空假设。

## 要评审的 plan（精读全文）
`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`

## 你这一份的聚焦范围
WI-8（落盘到安装目录/DeepResearch/ + index.md + README + 打包）、WI-5（flag/config 读取）、以及本 plan 对"子代理 driver 计划"的依赖是否成立。

## 必须读码核对的文件（用真实 file:line 验证 plan 的每一处引用）
- `G:/projects/deskpet/backend/paths.py` — `_install_dir`(:50) / `_portable_userdata_dir`(:57) / `user_data_dir`(:123) / `output_dir`(:141)。核对 plan 的 `deepresearch_dir()` 设计是否真能解析到"安装目录"，frozen 与 dev 两分支是否正确
- `G:/projects/deskpet/backend/deskpet/tools/research_tools.py` — `_save_report`(:1770) 现状、`_research_raw()` 真实实现（b05823b 后的安全读法）、`_handle_deepresearch`(:1711)
- `G:/projects/deskpet/backend/main.py` — `PROJECT_ROOT`(:103)、lifespan 里 scheduler/features 构造、`config` 模块级用法
- `G:/projects/deskpet/backend/config.py` — `[research]` 段怎么读、`FeaturesConfig`、`subagent_driver` flag 是否真存在（driver 计划是否落地）
- `G:/projects/deskpet/plans/2026-06-21-subagent-concurrency-driver/02-implementation-plan.md` 与 `00-PRD.md` — 确认 `features.subagent_driver` 与 scheduler 是否真已实现并可被本 plan 依赖

## 重点挑战这些点（逐条给结论）
1. **安装目录解析**：plan 的 `deepresearch_dir()` 要落"安装目录/DeepResearch/"。核对 `_install_dir()`(frozen=exe 父) 与 `_portable_userdata_dir()` 的上跳/probe-write 逻辑——plan 说"抽 `_install_root_writable()` 公共 helper"，现有代码结构是否支持这样抽取？frozen 下安装根到底是 exe 父还是上一级（Tauri layout backend/ 子目录问题）？
2. **dev 分支**：plan 说 dev 用 `Path(__file__).resolve().parents[1]` 作 repo 根。核对 paths.py 的真实位置（`backend/paths.py`）→ `parents[1]` 是否真等于 repo 根 `G:/projects/deskpet`？worktree 场景对不对？
3. **不落 C 盘的保证**：plan 强调不走 `user_data_dir()`（那是 %AppData%）。但兜底分支又用 `user_data_dir()/DeepResearch`——这个兜底会不会在**经典安装（非便携）**下默认触发，导致还是落了 C 盘？经典安装时 `_install_dir()` 返回什么？是否可写？请判断"经典安装"这个场景下报告到底落哪，是否违背用户"不进 C 盘"的要求。
4. **打包是否真便携**：核对项目是否真出便携包（看 driver/nsis 相关或 paths.py 注释 P4-S22 portable mode）。如果用户实际装的是经典 MSI（非便携），plan 的"安装目录"假设是否成立？
5. **index.md 原子写/去重**：plan 的"读现有→插首行→原子写(tmp→replace)→按文件名幂等"在并发（fan-out 同时多份？不，落盘在最外层一次）下是否安全？中文路径/编码（项目有 PowerShell 中文 mojibake 坑）下写 index.md 用什么编码？
6. **flag 依赖链**：本 plan 的 fan-out 依赖 `features.subagent_driver=ON` 才有 scheduler。请确认 driver 计划是否**真的已实现并合入**（不是只写了 plan）。若 driver 未落地，本 plan 的 fan-out 部分是否根本无法验收？这是否应在 plan 里标为前置 BLOCKING 依赖？
7. **config 读取健壮性**：`_research_raw()` 真实实现是否对 `[research].subagent_fanout` / `fanout_subrun_timeout` 等新键安全（缺失返默认、无 .raw 不抛）？plan 是否需要在 config.py 加任何东西，还是纯 raw-read 就够？
8. **README 更新**：repo 根 `README.md` 是否存在、现有结构能否自然插入"DeepResearch/index.md 作用"一节？

## 输出格式（严格）
按严重度分级，每条给：`[BLOCKING|MAJOR|MINOR|GAP]` + 一句话标题 + **证据（真实 file:line + 摘录）** + **具体修法（落到 plan 哪一节怎么改）**。
最后给一行总判定：`VERDICT: EXECUTABLE-AS-IS` 或 `VERDICT: NOT-EXECUTABLE (N blocking, M major)`。
特别地：明确回答"经典安装时报告是否会落 C 盘"和"driver 是否真已落地"这两个关键问题。
只报真问题，不要客套；没问题的点也明确说"已核对正确"。
