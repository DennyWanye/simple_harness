# 代码质量整治 交接

最后更新：2026-10-13 傍晚（用户说"继续"，阶段 1～4 暂停功能开发）
当前计划：代码质量整治计划-v2.md（2.3 修订）
用户授权：2026-10-12 说"好的，请你开始吧"，按四条建议执行：现在开工；全量回归一次授权 7 次（另加阶段 0 基线一次）；证据目录移出 git 不重写历史；阶段 1～4 暂停功能开发。

## 进度

| 阶段 | 状态 | 记录 |
|---|---|---|
| 0 安全网 | 完成，已推送 | 阶段0-实施记录.md |
| 1 自动修复与格式化 | 完成：已合并推送；SDK opt.187 发版（Host 钉版 09b23ac54）；数据副本启动检查通过 | 阶段1-实施记录.md、阶段2-实施记录.md（发版一节） |
| 2 拆主入口 | 代码完成：分支 cq-phase2 已推送；main.py 17311→288 行；控制通道与生命周期已改成路由表与步骤函数 | 阶段2-切片设计.md、阶段2-实施记录.md |
| 2 收尾 | 待做：全量回归第 2 次（授权内）→ 修新失败 → 独立核验 → 合并主分支 → 架构文档 | — |

## 接着做（按顺序）

1. 阶段 2 末全量回归（授权 7 次里的第 2 次）。注意：测试配置已隔离用户目录，可能暴露原来依赖用户数据或用户配置的测试。
2. 新失败逐个修；断言改动交独立核验签字（同阶段 1 做法）。
3. 独立核验阶段 2（子代理 opus，只报阻断级）。
4. 合并主分支、推送；更新 ARCHITECTURE（host 包结构、控制通道路由表、生命周期步骤）。
5. 进入阶段 3（函数内导入与循环依赖）。

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
