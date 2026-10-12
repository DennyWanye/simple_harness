# 代码质量整治 交接

最后更新：2026-10-13 凌晨（暂停）
当前计划：代码质量整治计划-v2.md（2.3 修订）
用户授权：2026-10-12 说"好的，请你开始吧"，按四条建议执行：现在开工；全量回归一次授权 7 次（另加阶段 0 基线一次）；证据目录移出 git 不重写历史；阶段 1～4 建议暂停功能开发。

## 进度（2026-10-13 凌晨，用户要求暂停，等用户说再继续）

| 阶段 | 状态 | 记录 |
|---|---|---|
| 0 安全网 | 完成，已推送 | 阶段0-实施记录.md |
| 1 自动修复与格式化 | 代码已合并主分支并推送；全量回归 SDK 无新增失败，Host 13 个与前端 1 个新失败已修复、断言改动已签字 | 阶段1-实施记录.md、阶段1-SDK发版独立核验.md、阶段1-回归修复断言签字.md |
| 1 收尾 | **未完成**：SDK opt.187 发版（暂停时停在执行图验收门，未提交，已恢复）；数据副本启动检查；架构文档 | — |
| 2 拆主入口 | 分支 cq-phase2（工作树 simple_harness-cq）已提交三片：收全局变量（2.0b）、日志与全局单例（2.0a 一）、启动装配五段（2.0a 二）；main.py 17311→16448 行 | 阶段2-切片设计.md |
| 2 待验证 | 75 个相关测试文件只跑了一部分（前 25 个全过），需重跑 | — |

## 明天接着做（按顺序）

1. 主检出重跑发版：先写新回执（源码没变，旧回执仍可用：.local-test-evidence/2026-10-13/taskgraph-gate/review-030112.json），
   `bash scripts/release_sdk_opt.sh 186 187 "SDK opt.187：代码质量整治阶段 1（全库格式化、安全自动修复、删未用导入、改坏清单重锚；行为不变）" <回执>`。
   跑之前确认主检出干净；跑期间不改主检出。
2. 发版后在开发数据副本上启动（做法见记忆 data-copy-startup-check）。
3. 工作树重跑相关测试：`python3 scripts/code_quality/run_related.py backend @<清单>`（清单生成见阶段 2 切片设计）；
   再跑 `python3 scripts/code_quality/patch_targets.py`，"缺失"必须为 0。
4. 阶段 2 下一片：模块级剩余零散语句（会话库与技能初始化、应用对象创建）与函数组搬家，按切片设计顺序。
5. 阶段 2 改结构的片（生命周期、SDK 运行栈、控制通道）开工前写特性化测试。

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

- 搬函数后，测试对 main 的打桩可能失效（读取方已在新模块），用 patch_targets.py 查；中继测试曾靠打桩隔离真实用户配置文件。
- 工作树借用主检出的虚拟环境：SDK 测试要设 PYTHONPATH=src；启动快照脚本已自动改写安装来源路径。
- 禁区新增：编解码器钉死的 10 个契约文件、测试冻结的起始快照 2 个文件、工作流节点 16 个文件（见 ruff.toml）。
- 模块级同名局部变量：main.py 里 state 是 43 处局部变量，改写工具会拒绝撞名前缀。

- 基线全量回归在跑时不许改任何源码。SDK 逐文件跑会读到改动后的代码。
- macOS 临时目录经过符号链接，状态库拒绝。脚本里已用 realpath。
- 公开接口快照要固定哈希种子，否则 frozenset 顺序每次不同。
- jscpd 默认跳过 100KB 以上的文件，必须加 --max-size。
