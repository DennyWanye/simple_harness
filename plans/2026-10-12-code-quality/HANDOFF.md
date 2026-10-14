# 代码质量整治 交接

最后更新：2026-10-14 晚（阶段 3 完成并合并主分支 57b91b0c6；虚拟环境事故已修 f97b5c6f8）（用户说"继续"，阶段 1～4 暂停功能开发）
当前计划：代码质量整治计划-v2.md（2.4 修订）
用户授权：2026-10-12 说"好的，请你开始吧"，按四条建议执行：现在开工；全量回归一次授权 7 次（另加阶段 0 基线一次）；证据目录移出 git 不重写历史；阶段 1～4 暂停功能开发。

## 进度

| 阶段 | 状态 | 记录 |
|---|---|---|
| 0 安全网 | 完成，已推送 | 阶段0-实施记录.md |
| 1 自动修复与格式化 | 完成：SDK opt.187 发版（Host 钉版 09b23ac54）；数据副本启动检查通过 | 阶段1-实施记录.md |
| 2 拆主入口 | **完成并合并主分支（19dbb3aa2，2026-10-13）**：main.py 17311→288 行；后端全量回归新失败 5 个已修；独立核验两轮通过；架构文档 HOST_ENTRY.md | 阶段2-实施记录.md、阶段2-独立核验.md |
| 3 函数内导入与循环依赖 | **完成**：函数内导入 3818→575，剩余每处有原因注释；循环依赖后端 79、SDK 87 移交阶段 6、5；3.4 推到阶段 8；SDK 改动待阶段 4 发版 | 阶段3-实施记录.md、阶段3-偏差单-剩余函数内导入.md、阶段3-移交-循环依赖清单.md |

全量回归授权 7 次，已用：阶段 0 基线（不计）、阶段 1 第 1 次、阶段 2 第 2 次（只跑后端）。

## 接着做

1. 阶段 4：后端与 SDK 边界收口（计划 v2 §阶段 4）。开工前先出切片设计。
2. 阶段 4 发 SDK opt 版本时：跑执行图验收门把部署清单写回 VALIDATED（阶段 3 每批重生成过，现为 NOT_RUN）（Host 继续从 commit_service 导入预算账户名字——它们在 commit_service 的公开 __all__ 里；budget_accounts 是内部模块）。
3. 阶段 5（SDK）、阶段 6（后端）：循环依赖导入清单见 阶段3-移交-循环依赖清单.md，阶段末上限 SDK ≤ 30、后端 ≤ 20（指标 cycle_imports_sdk / cycle_imports_backend）。
4. 阶段 8：死代码待删 7 处（`grep -rn "按需导入：死代码待删"`）；sys.path 修改 26 处（grok_build_runtime 白名单）；阶段 2 记下的死代码。

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
