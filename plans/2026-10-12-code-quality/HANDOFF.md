# 代码质量整治 交接

最后更新：2026-10-18 下午（收尾补充 15 片完成，合并主分支；下一步真机一局，再做"删后台工作流引擎"）。（上一条：2026-10-18（阶段 11 完成并合并、SDK opt.192 已发 Host e7a536ea5，只剩真机一局；阶段 10 合并 bd4621386；阶段 9 合并 b56c14289；阶段 8：SDK opt.191，Host 钉版 3aac72923）（用户说"继续"，各阶段做完接着做后续阶段））
当前计划：代码质量整治计划-v2.md（2.12 修订）
用户授权：2026-10-12 说"好的，请你开始吧"，按四条建议执行：现在开工；全量回归一次授权 7 次（另加阶段 0 基线一次）；证据目录移出 git 不重写历史；阶段 1～4 暂停功能开发。

## 进度

| 阶段 | 状态 | 记录 |
|---|---|---|
| 0 安全网 | 完成，已推送 | 阶段0-实施记录.md |
| 1 自动修复与格式化 | 完成：SDK opt.187 发版（Host 钉版 09b23ac54）；数据副本启动检查通过 | 阶段1-实施记录.md |
| 2 拆主入口 | **完成并合并主分支（19dbb3aa2，2026-10-13）**：main.py 17311→288 行；后端全量回归新失败 5 个已修；独立核验两轮通过；架构文档 HOST_ENTRY.md | 阶段2-实施记录.md、阶段2-独立核验.md |
| 4 后端与 SDK 边界收口 | **完成**：SDK 公开接口清单与 `__all__`；产品代码引用 SDK 内部归零；6 个叶子模块下沉；import-linter 分层与 SDK 公开边界进 CI；**SDK opt.188 已发（Host 钉版 66936ff62）**；第 3 次全量回归无新失败（后端 1 个外部语音服务 403）；数据副本启动通过；独立核验通过 | 阶段4-切片设计.md、阶段4-实施记录.md、阶段4-SDK发版独立核验.md |
| 3 函数内导入与循环依赖 | **完成**：函数内导入 3818→575，剩余每处有原因注释；循环依赖后端 79、SDK 87 移交阶段 6、5；3.4 推到阶段 8；SDK 改动待阶段 4 发版 | 阶段3-实施记录.md、阶段3-偏差单-剩余函数内导入.md、阶段3-移交-循环依赖清单.md |
| 5 SDK 巨型类拆分 | **完成并合并主分支**：SDK opt.189/opt.190；第 4 次全量回归无新增（F-4 偶发观察）；循环依赖 SDK 87→40 | 阶段5-切片设计.md、阶段5-实施记录.md |
| 6 后端巨型类与超长函数 | **完成并合并主分支（6bbb744b2）**：超 1500 行的类 16→0；56 个原超 300 行函数全部 ≤150；后端循环依赖 79→19；第 5 次全量回归新增 43 条已修（os_tools 包根）；独立核验复核 PASS；大文件指标偏差裁决（计划 2.7） | 阶段6-切片设计.md、阶段6-实施记录.md、阶段6-偏差单-*.md |

| 7 异常收窄与日志 | **完成并合并主分支（fb503dd7b）**：静默宽捕获 316→10（编解码钉死除外）；产品代码宽捕获未说明 0；print 没写原因 0；11 处收窄配用例；修 structlog 丢异常信息；SDK 改动随阶段 8 发版（部署清单回到未运行） | 阶段7-切片设计.md、阶段7-实施记录.md |

| 8 查重与死代码 | **完成并合并主分支（18ff99c8b）**：SDK opt.191；新增 8K 按导入可达性删 58 个不可达模块与旧主循环 AgentLoop、20 个只读槽位；5 个大文件拆到 2000 行以下；分层例外清零；sys.path 26→2；Python 净减约 5.5 万行；第 6 次全量回归新增 2 条已修、1 条慢用例；签字核验通过；数据副本启动通过 | 阶段8-切片设计.md、阶段8-实施记录.md、阶段8-偏差单-*.md、阶段8-删除清单-*.md |

| 9 前端 | **完成并合并主分支（b56c14289）**：eslint 189→0 并进 CI；显式 any 75→0；react-hooks 清零（9D 行为变更签字）；四个超 2000 行前端文件拆开；内联样式 1453→195（转生成类、作者样式进 sh-base 层、WebKit 产物逐元素比对、两份偏差单裁决、产品修正一处：按钮不可选）；第 7 次全量回归后端/SDK 无新增、前端 1 条读源码测试已修；9I 独立核验签字；合并后 CI 前端与代码质量全绿 | 阶段9-切片设计.md、阶段9-实施记录.md、阶段9-9I-设计.md、阶段9-9I-批次记录.md、阶段9-偏差单-*.md |

| 10 注释与命名规范 | **完成并合并主分支（bd4621386）**：带代号的注释 382→10（全在禁区与钉死文件）；宽口径 482 条逐条判定；文件头改正后端 25、前端 5、SDK 1；每个提交证明只改注释（新工具 comment_only_check.py、ts_comment_only_check.mjs）；工作流五套身份不变；带版本号命名三类登记（F-23）；规范进 CONTRIBUTING.md、CLAUDE.md；独立核验签字；SDK 注释改动随阶段 11 发版 | 阶段10-切片设计.md、阶段10-实施记录.md、阶段10-*清单.md |

| 11 清库与收尾 | **完成并合并（db80fc7d8）**：截图删、24 份报告归档、证据目录移出 git（合并后写回本机文件并核对一致）；CI 加 SDK mypy 与查重硬阻断；尾巴清单；第 8 次全量回归无新增；SDK opt.192（Host e7a536ea5）；数据副本启动通过；**真机一局未做：鼠标操控授权框被拒，待用户点允许** | 阶段11-切片设计.md、阶段11-实施记录.md、阶段11-棘轮尾巴清单.md、阶段11-SDK发版独立核验-opt192.md |

| 收尾补充 | **完成**：用户逐项定的 9 项待决与"旧会话界面"三项（删 ChatView 整棵、用量环与切换模型接主对话、实时语音/伴侣卡/PPT 大纲卡下线）共 15 片；FOLLOWUPS.md 顶部有逐项结果与 F-26～F-31 后续 | 收尾补充-施工清单.md、收尾补充-旧会话界面-设计.md、FOLLOWUPS.md |

全量回归授权 7 次 + 2026-10-17 另授权第 8 次（阶段 11 已用），已用：阶段 0 基线（不计）、阶段 1 第 1 次、阶段 2 第 2 次（只跑后端）、阶段 4 第 3 次、阶段 5 第 4 次、阶段 6 第 5 次（只跑主机）、阶段 8 第 6 次（主机 + SDK）、阶段 9 第 7 次（三端）。**已用完；再跑全量回归要用户另行授权。** 2026-10-18 用户另授权第 9 次，只用于"删后台工作流引擎"第 6 片收尾验收。

## 接着做

1. **真机一局**（阶段 6、8、9、11 都欠着；用户 2026-10-17 授权不必在场，但系统的鼠标操控授权框要用户点"允许"，2026-10-18 凌晨被拒）。照 `阶段11-实施记录.md` 真机一节：调试版包已打好，隔离目录 `.local-test-evidence/2026-10-18/cq-real-click/`（`launch-app.sh` 启动），日卡转发在跑；逐个看主要界面、发一轮真实模型对话。
2. **其他检出合并主分支后要写回证据文件**：`git restore --source=72b9c07a0 --worktree -- .local-test-evidence`（a4、arp、opt 工作树与其他电脑）。
3. **待用户定**：FOLLOWUPS F-10、F-12、F-16～F-19、F-21～F-25（F-20 知悉即可）；第二轮整治先定后端工作流引擎去留（尾巴清单第 3 节）。
4. 开源合规检查（SPDX 头）仍红（1437 个文件），归后续单独处理。

## 阶段 10 新工具（scripts/code_quality/）

- comment_only_check.py（`--ref <提交>`，`--module-docstring` 只放开模块文档字符串）：Python 去注释后词法序列逐个相同；TS 部分调 ts_comment_only_check.mjs（语法树词法叶子，去掉 JSDoc 节点）。
- workflow_identity.py（product / official）：工作流清单哈希与实现指纹快照，改注释前后比对。
- 在工作树里跑会起子进程、比对源码字节的 SDK 测试，要导出 `PYTHONPATH=<工作树>/sdk/simple-harness-sdk/src`（共享虚拟环境的可编辑安装指向主检出）。

## 阶段 9 新工具（scripts/code_quality/）

- ts_move_defs.mjs / ts_move_check.mjs（前端跨文件纯移动与核对）、ts_emit_check.mjs（只改类型时产物不变）、ts_dispatch_check.mjs（分发表）。
- 9I：ts_static_eval.mjs、ts_inline_to_class.mjs（census / convert）、ts_inline_check.mjs（旧提交副本里真求值的静态核对，带金丝雀）、css_layer_check.mjs、webkit_style_compare.py（build / dump / compare / selftest；Python 用主检出 backend/.venv，WebKit 26.5 已装）；画廊 tauri-app/tests/browser/style-gallery*。
- 前端 lint 以 CI 为准（F-22）；本机可从 npm 缓存离线取锁文件版本复核（`npm pack <包>@<版本> --offline`，临时配置放 tauri-app 目录下跑完删）。

## 阶段 8 新工具（scripts/code_quality/）

- backend_reachability.py：从 main.py 出发的模块可达性（导入、函数内导入、点分与同包短名字符串、资源文件里的路径算边）。判整模块死代码用它，别用按词搜索。
- test_only_symbols.py：只剩测试引用的符号按语法树重分类。
- legacy_inventory.py：旧版字样分类清单。
- dead_symbols.py、delete_defs.py（测试里只带 pytest.mark 的测试函数可连装饰器删）、store_concepts.py。
- move_defs.py 加 --reexport（下沉时为钉死文件留转出口），修了带 `from __future__ import annotations` 时漏算注解名字。

## 阶段 7 新工具（scripts/code_quality/）

- except_inventory.py：宽捕获清单与甲乙丙分类、完成口径（metrics.py 共用）。
- add_exc_info.py：已有日志补 exc_info（只认白名单记录器）。
- apply_except_spec.py：按清单逐处改（记日志 / 只记类型 / 收窄 / 删 try / 写原因）；出错即停，**停之前写过的文件要取回重跑**。
- handler_diff.py：证明改动只在 except 处理块内，生成禁区比对放行清单；认出删 try。
- 后端测试子集用单进程跑（scratchpad 的 rt2.sh 做法：设 CQ_ORIGIN_REBASE、PYTHONPATH 加 scripts/code_quality、`-p origin_rebase_plugin`），比 run_related 逐文件快十几倍；SDK 仍逐文件（多目录一起收集撞 conftest 重名）。
- 提交前判定一律匹配"：通过"（"不通过"里也含"通过"）。

## 阶段 6 新工具（scripts/code_quality/）

- 拆长函数：state_rename.py + cut_phase.py（状态对象切段，inline_check.py 核对）；extract_method.py / plan_extract.py（显式参数，extract_check.py 核对）；closure_body_out.py、closure_to_method.py、lift_closures.py、split_dispatch.py、extract_expr.py。
- 解环：cycle_sim.py（模拟去边）、unroot_package.py（包根不再转出）、move_defs.py（定义下沉或挪出，全仓导入方改到新位置）、lift_tagged.py（上提）。
- 上提或搬家后必查：测试在原模块打桩是否还管得到读取方；读宿主源码文字的测试（tests/host_source.py）改宿主代码时必须一起跑。
- 调格式化一律带 --force-exclude（显式传路径时 ruff 不认排除名单）；导入排序在仓库根目录跑。

## 每片工具

- 搬家：`bash scripts/code_quality/slice.sh <目标> <说明> <名字列表>`
- 收尾：`bash scripts/code_quality/slice_finish.sh <模块名> <名字列表>`（改测试打桩、查打不到的打桩、删主入口转手导入、格式化、重验）
- 测试打桩改成主入口与 host 一起换：`to_patch_host_global.py`；测试读源码用 `tests/host_source.py`

## 每片必跑（计划 4.1）

```bash
python3 scripts/code_quality/import_smoke.py
python3 scripts/code_quality/import_smoke.py --fresh-changed HEAD
python3 scripts/code_quality/guard.py --ref HEAD
python3 scripts/code_quality/startup_snapshot.py
python3 scripts/code_quality/metrics.py check --new-since HEAD
python3 scripts/code_quality/patch_targets.py
```

- 改结构的片开工前先 `api_snapshot.py write --out <证据目录>/before.json`，改完 `api_snapshot.py diff`。
- 相关测试：只跑改动文件对应的测试，加新调用点所在函数的测试。
- 指标变好后：`metrics.py lower` 下调基线并提交。

## 坑

- **不要在工作树里跑 `uv sync`**：会把共享虚拟环境里 SDK 包的安装来源改成工作树路径（主检出的 SDK 候选身份检查依赖它）。要装依赖就回主检出跑 `uv sync --python 3.12 --extra dev --inexact`。
- **SDK 源码一变要跑改坏清单资产测试**（tests/orchestrator/acceptance_assets/test_acceptance_assets.py）：清单用源码原文定锚，结构改动会让锚点失效，发版门会挡；重锚后用 `scripts/acceptance/run_mutations.py <编号>` 确认仍 KILLED（直接改文件跑测试会被部署清单字节检查先拦，判不出真假）。
- 每片新增检查：`cd backend && lint-imports`、`python3 scripts/code_quality/backend_layers.py`、`python3 scripts/code_quality/sdk_boundary.py`。

- **工作树的 backend/.venv 是符号链接**：10-14 被 `git add -A backend` 提交过，合并时删了主检出的虚拟环境（已修，忽略规则改成 `.venv`，主检出用 `uv sync --python 3.12 --extra dev` 重建）。合并前查 `git diff --summary main..分支 | grep 120000`。

- 阶段 3 工具：上提 `lift_batch.sh <树> <种类> <说明> <路径…>`（每批全部门禁通过才提交）；补注释 `tag_inner_imports.py --tree X`，检查 `--check`；导入隔离约定登记 `import_contracts.py`，新增"导入某入口不得加载某模块"的测试要补登记。
- SDK 源码字节一变，执行图部署清单就要重生成（lift_batch 已自动做），否则约 10 个测试报 installed source bytes changed。
- SDK 有几个慢文件单个用例 150～280 秒，机器忙时会被判卡死、或时间敏感用例失败；SDK 批次用 CQ_STALL=900，别同时跑两组测试。
- 后端用的是装好的 SDK 包（opt.187），不是工作树里的 SDK 源码；改 SDK 名字位置时后端导入要等钉新版再改。

- 工作树里 SDK 候选身份、实时语音服务两个测试文件必失败（装好的 SDK 来源路径指向主检出），到主检出验证。
- 读主入口源码的结构测试改用 `tests/host_source.py`。

- 搬函数后，测试对 main 的打桩可能失效（读取方已在新模块），用 patch_targets.py 查；中继测试曾靠打桩隔离真实用户配置文件。
- 工作树借用主检出的虚拟环境：SDK 测试要设 PYTHONPATH=src；启动快照脚本已自动改写安装来源路径。
- 禁区新增：编解码器钉死的 10 个契约文件、测试冻结的起始快照 2 个文件、工作流节点 16 个文件（见 ruff.toml）。
- 模块级同名局部变量：main.py 里 state 是 43 处局部变量，改写工具会拒绝撞名前缀。

- 基线全量回归在跑时不许改任何源码。SDK 逐文件跑会读到改动后的代码。
- macOS 临时目录经过符号链接，状态库拒绝。脚本里已用 realpath。
- 公开接口快照要固定哈希种子，否则 frozenset 顺序每次不同。
- jscpd 默认跳过 100KB 以上的文件，必须加 --max-size。
