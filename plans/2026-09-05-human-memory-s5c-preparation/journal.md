# S5c T1/T2 隔离执行 journal

2026-09-05。执行依据：用户批准在已创建隔离 worktree实现 T1/T2可独立部分，原 S5 Task7/A7/A8/A11不变。串行实施共享 schema/store，不实现其他任务。完整AC真实性仍由原acceptance约束，未另开/修改gate run、未改active-run、未替换原oracle。

- 输入：main `c183fe70`（Q1 `4eb1eb7c` + downgrade）合入 feature，merge=`c9c634d412a63c98288ee9cd1e4ac6b9130cd1b1`。原准备提交 `00f1a17c` 保留。
- 最小问题：跨库接续之前，Host能否耐久保存 exact registration/action请求，且崩溃不丢cursor、不把请求/claim变成权限或processed？先写决定性测试，再实现4表专用存储与只读ports，无通用event框架。
- 基线19 passed；先红为缺失s5c_schema模块（collection failure，非业务行为红）；首轮实现7 fail/3 pass，定位canonical JSON tuple类型；补action测试2 fail/13 pass，定位EvidenceRef.content_hash字段。均修复，失败日志原样保留，不能把这些失败抹掉或冒称生产失败。
- 当前累计 **66 passed**：新增22项、v46/Q1/downgrade19项、recovery25项。Ruff E/F/I通过。真实SQLite事务/重开、真实Host evidence入库、公开SDK DTO/ref verifier；Provider仅已有单测传输替身，0真实Provider调用、0backend/模型启动。
- 价值事实：before-commit回滚全部相关行；after-commit lost-ACK重开保留同ref/cursor；双连接同cursor只一个成功；旧默认v46 initializer拒绝v47且raw source守恒；DDL/fence丢失reopen拒绝；closing recovery拒绝迁移；requested不是grant、claimed不写v45presented；跨owner/ref变造拒绝。
- 矛盾转化：隔离 durable基础可运行，下一阻塞是完整Host consumer/issuer与SDK/Context契约，不能通过打开默认flag“完成接线”。本轮到T1/T2授权边界结束。
- G6按用户补充回写：v1ordinary payload字节保持、版本化schedule列/Schema/旧版拒绝、replay冲突必须在ingest早返回前检测、batch隔离/maxwait bypass、先reclaim与同principal active fence均不可放宽。主线程0.6.5 source freeze只登记为协调约束，不改本分支SDK/pin。
- 独立review **NOT_RUN**：当前可用工具无独立子代理/code-review接口；执行者检查不冒充独立review。按 [plan-task](/Users/denny/.codex/skills/plan-task/SKILL.md) 的“执行者不自审”，提交可审阅隔离代码，交主协调review，不声明phase3/gate收口。

## 复验命令

工作目录是本隔离worktree。共享venv只作解释器，`PYTHONPATH`必须指向本树backend。

```bash
PYTHONPATH="$PWD/backend" /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest \
  backend/tests/memory/test_s5c_store.py \
  backend/tests/memory/test_effect_closure_migration_v46.py \
  backend/tests/memory/test_analysis_episode_time.py \
  backend/tests/product_state/test_host_control_downgrade.py \
  backend/tests/execution/test_recovery_fence.py -q
```

原始日志相对本worktree `.local-test-evidence/2026-09-05/s5c-t1-t2/`：
`baseline.log`、`red.log`、`first-implementation.log`、`core.log`、`expanded.log`、
`expanded-fixed.log`、`focused.log`、`verification.log`；安装版本与实际源码路径见`runtime-identity.json`，
文件hash见`sha256.json`，提交后相同命令复验另存`postcommit.log`。测试安装身份为Harness0.7.2 / Memory0.6.3 / Service0.3.12，未由本线程安装/升级。

## G6 最小化后续（仅文档）

用户确认priority只是调度、不需重复授权框架；T1新增确切SDK消费合同。撤销旧analysis_schedule/action-ref方案，只需要typed analysis_priority和必要版本化列/不可变/replay/claim/open校验。Host store实现与66项结果不变；无SDK、pin、生产入口或业务代码变更。本次只核对文档链接和diff，不重复运行业务测试。

## 兑现与剩余

T1本切Host内部接口已定稿；T2显式schema/store/只读authority自动验证。action正向resolver用显式fixture-only authorized行，**无生产授权发行证明**。T3/T4实际调度、snapshot/presentation/ack/terminal未实现；T5 priority与T6披露阻塞；真实S5B-S5/S6、独立审查、全增量gate未执行。ARCHITECTURE只记录此边界，未改 main 生产状态或称program complete。

自我批评：首次fixture误用了EvidenceRef字段，扩展测试发现后已修；此后按公开DTO构造与verifier检验，避免只验证手写JSON。

VERDICT: BLOCKED — 本切Host基础自动验证通过、可独立review；S5c整体未完成，独立review及T5/T6契约仍待后续。
