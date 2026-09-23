# Agent Runtime Plane 完整实施包

**只需下载此完整 ZIP。**主文档：`ARP-EXEC-1.0.zh-CN.md`。

范围：原BaseAgent运行层＋按用户公式动态N的Context＋每AgentSession临时向量SQLite＋Capability/Provider＋Skill＋Tool。保留HTN/TaskGraph/Assurance/OCC/OPS及原执行账本；不是生产SDK补丁。

## 阅读顺序

主计划§0–6 → §7–15 → implementation/FIELD-CONTRACTS、INTERFACES、HOST-DTOS、seams → BODY-WIRED → TEST-PLAN → review。完整SQL在sql两个不同数据库脚本；完整Schema在contracts一个文件的32份定义。inputs只作原要求追踪，不将其旧表述重新盖过新裁定。

## 参考包校验（不是WorkAgent实现前批量测试门）

从解压根运行，PY必须是项目既有`.venv/bin/python`或同项目`uv run --frozen --no-sync python`，不要系统pip安装依赖：

```bash
PY=/Users/denny/projects/simple-harness-sdk-h1h-impl/.venv/bin/python
"$PY" -B tools/verify_delivery.py --root .
"$PY" -B tools/check_plan.py --root .
"$PY" -B -m unittest discover -s tests -v
```

参考自测仅Python标准库/SQLite，FTS5能力必须实际存在，缺失明确报环境不足不skip。参考SQL用最小父键fixture，绝非完整SDKschema验收。reference模型使用token charge，绝非实际512K模型实测。

## 施工

一次本地来源盘点，不reset/clean/rebase/pull，不用历史远端覆盖dirty源码。按RP-A/B/C/D完成主体与跨层接线（期间只处理明确blocker做小检查），BODY_WIRED后RP-E统一验收。真实本地SQL runner、脚本/工具Sandbox、Embedding/Provideradapter、Host和独立代码审查不由referencePASS代替。

原始log/DB/trace/screenshot源码快照留`.local-test-evidence/`。Git只保存实施结论、命令、nodeid、相对索引与hash。完整验收后新Agent默认ON，旧Agent冻结profile不变，其他未完成模块门禁不自动开放。

本次实际工具/参考结果见`VALIDATION.md`和`reports/`；实际SDK60场景及16变异全部PENDING_SDK_EXECUTION。
