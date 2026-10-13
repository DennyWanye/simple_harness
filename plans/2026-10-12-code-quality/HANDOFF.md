# 代码质量整治 交接

最后更新：2026-10-13 上午（用户说"继续"，阶段 1～4 暂停功能开发）
当前计划：代码质量整治计划-v2.md（2.3 修订）
用户授权：2026-10-12 说"好的，请你开始吧"，按四条建议执行：现在开工；全量回归一次授权 7 次（另加阶段 0 基线一次）；证据目录移出 git 不重写历史；阶段 1～4 暂停功能开发。

## 进度

| 阶段 | 状态 | 记录 |
|---|---|---|
| 0 安全网 | 完成，已推送 | 阶段0-实施记录.md |
| 1 自动修复与格式化 | 完成：已合并推送；SDK opt.187 发版（Host 钉版 09b23ac54）；数据副本启动检查通过 | 阶段1-实施记录.md、阶段2-实施记录.md（发版一节） |
| 1 收尾 | 架构文档未更新（随阶段 2 一起写） | — |
| 2 拆主入口 | 分支 cq-phase2 已推送，已变基到 opt.187 主分支；main.py 17311→14883 行；81 个相关测试文件整批重跑通过 | 阶段2-切片设计.md、阶段2-实施记录.md |

## 接着做（按顺序）

1. 阶段 2 下一片：SDK 激活（_activate_product_sdk_runtime 等）→ 历史事件与伴侣收件箱 → SDK 执行与恢复 → 产品对话与续跑 → SDK 延迟单例与提供方绑定 → 路由（云配置、技能、命令、健康、指标、语音）→ 记忆消息处理。
2. 改结构的片（生命周期、SDK 运行栈、控制通道）开工前写特性化测试。
3. 每片收尾：`retarget_tests.py` 改测试打桩目标，`prune_host_imports.py` 删主入口无用导入。
4. 阶段 2 末：全量回归第 2 次（授权内），合并主分支，更新架构文档。

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
