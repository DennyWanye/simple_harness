# ARP-EXEC-1.1 完整交付入口

只需保留本ZIP。它是对2026-09-23 Q01–Q20的统一修订；不是SDK补丁，未运行用户本地生产代码。仅原生BaseAgent/AgentRuntime/ReAct，不包含Pi、RuntimeBackend或热迁移。

## 阅读顺序

1. `DECISIONS.md`：20项决定、关闭条件与环境依赖。
2. `ARP-EXEC-1.1.zh-CN.md`：唯一当前主规格。
3. `implementation/INTERFACES.md`、`CONTEXT-SEARCH.md`、`JOBS-LIFECYCLE.md`、`SKILL-CATALOGUE.md`、`HOST-DTOS.md`：实际调用/状态/原事务合同。
4. `contracts/runtime-plane.schema.json`、字段/引用源表、`sql/`：机器合同与增量DDL。
5. `implementation/BODY-WIRED.md`、`TEST-PLAN.md`：主体优先，统一后置验收。
6. `review/CHALLENGE-REPORT.md`和`VALIDATION.md`：作者自审与实际验证边界。

inputs中的1.0原包和评审只用于追踪，不覆盖1.1正文。不要覆盖已有dirty SDK或共享Host，也不要将完整包直接git add。原始本地日志/数据库/源码盘点留`.local-test-evidence/`；Git仅人工审阅的结论/命令/nodeid/相对索引/hash。

## 先运行的轻量检查

在新解压目录执行，使用当前集成候选项目解释器：

```bash
SDK="/path/to/current-integrated-native-sdk"
KIT="$(pwd -P)"
PY="$SDK/.venv/bin/python"
test -x "$PY" || exit 2
"$PY" -B "$KIT/tools/verify_delivery.py" --root "$KIT"
"$PY" -B "$KIT/tools/check_plan.py" --root "$KIT"
```

不安装依赖、不打开产品数据库、不调用模型。实际后继路径来自当前TaskGraph23→Assurance交接，不自动指向旧HTN候选。源码盘点工具为`tools/capture_native_sources.py --sdk ... --host ... --out 新ignored目录`，只读取有界文件和AST，其fingerprint明确是selected-files，不冒充全仓dirty冻结。

## 主体后验收与参考检查分开

原60+新20 SDK场景、原16 SDK mutation、stateful、原生/真实模型均是后置实施验收。包内标准库参考测试不是它们的替代：

```bash
cd "$KIT"
"$PY" -B -m unittest discover -s tests -v
"$PY" -B tools/run_reference_mutations.py
```

这两条只在需要复核规格附件时使用，不要求WorkAgent编码前反复批量跑。真实SDK完成不由参考PASS签发。资源缺失按DEPENDENCIES具名处置，不切legacy、不用HashEmbedder冒充真实语义。
