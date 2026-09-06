# Closure physical guard：限定交付

2026-09-06。分支 `feat/closure-physical-request-guard`，base `666b475b`；主体 `5375fc85`，生产读事务窄修 `4a86ecb0`。本树默认 main closure factory 已接专用guard；尚未合主候选，不改 SDK、冻结wheel、原userdata、Carver ACK/context_authority 或旧compactor。

## 实际结果与原失败

| 批次 / 固定源 | 实际结果 | 原因及范围 | 资源 |
|---|---|---|---|
| r1 / 5375 | BUSY75，NOT_RUN | r9 native持锁，未重试 | 未启动child |
| r2 / 5375 | 3 PASS、5 FAIL / 36.47s | allow真实send1+no_mutation、缺carrier、无来源event通过；其余锁/取消/结果阶段错误原样保留 | PG87522 exit1 / 37.551s / peak399072KiB |
| r3 / f8e89a6c | 原5红 5 PASS、3 deselected / 9.39s | 改真实after_enqueue/drain单driver，取消只清已done/cancelled的自有fixture task；slow要求事件真实commit后拒绝 | PG88161 exit0 / 10.275s / peak388160KiB |
| r4-resume / c0a8a124 | 新1 FAIL、8 deselected / 9.16s | 两实际SDK Run已complete，CREATE_NEW与RESUME_EXISTING实际route存在；第二Host RUNNING，无第二closure attempt，database is locked | PG88432 exit1 / 10.109s / peak376384KiB |
| r5-resume / 4a86ecb0 | 只此红 1 PASS、8 deselected / 3.99s | 两Run完整公开scope来源→两次closure真实MockTransport发送→两个no_mutation/COMPLETED | PG88793 exit0 / 4.676s / peak375104KiB |

**共9个唯一场景分批通过，不是重跑全部9项，不把累计执行次数计作质量分。** 每批 `remaining_group_members=[]`、`cleanup_error=null`，未被资源阈值停止；最后最低磁盘1526MiB。槽已释放。原analysis14、Provider cold1及旧包未重跑。

r2的测试直接 `_drive_once` 未登记 `_driver`，真实keeper可在SDK终态后启动另一个driver；slow原DB已存physical rejection，而测试最后outcome为incomplete，late还出现keeper取消。改生产唤醒入口后五项全部绿，产品未改。此解释只用于本批fixture；不能覆盖r4独立生产读事务问题。取消reservation已验证输入S1和attempt同TX全回滚，但不宣称Runtime.close对已取消driver的行为通过。

r4与r5单独证明实际resume依赖会递归调用 `open_exact`，`materialized_only=True`仍产生访问审计。修复只释放closure外层读事务后再执行该公共reader/Memory检查；保留实际access receipt、原reservation写事务及异步读取后的精确Host事实复核。未延长超时、改journal为WAL或吞异常。

## 已验证契约

- 真实Host入场、SDK CREATE_NEW/RESUME_EXISTING/tool write/final answer产生scope和S1，再由真实 main resolver/ProductProviderAdapter 到 httpx.MockTransport。正向必须物理send且no_mutation，不把pending算成功。
- 完整provider request篡改、缺carrier、晚遗忘、同scope缺来源、实际慢检查期间scope变更均零closure发送。成功和UNKNOWN原优先分支不重新prepare、不再发；传输未知保留sent_unknown。
- actual attempt/ordinal/hash/model/binding/subject/来源成员/完整carrier与当前原披露、lease/generation/scopehead绑定；observer在原reservation TX内。外部请求不冒充前台typed handoff。

本机使用借用generic依赖Python与既有 **installed H077/M616 targets**，Host本树源码测试；不是本树完整新安装身份验证，也不是H077/M617/native。请求通过真实适配器边界但HTTP为MockTransport，未调用外部模型、启动native、build或install。

## 剩余边界

- 非空scope `resume`、无完整公开来源的修改goal/任意旧pending prose等仍为 `closure_source_incomplete` pending；不是完整Closure、所有原长旅程或program完成。
- 9项没有独立foreign Run carrier、owner/generation换代、披露token换代动态负控；此类目前仅源码检查，不宣称已测。
- 保留Host/Memory跨库最后检查到send的非原子窗口；原scope全subject/历史扫描成本未优化，32/64输出限额不等于全局工作/P99有界。
- compaction未发现已接通同类生产链，本叶未恢复旧compactor。真实主组合/native闭环由主后续验证。
- Dirac已对 `4a86ecb0` 源码delta、原8分批与新增resume原红→绿作限定ACCEPT；明确保留间接access写和跨库窗口，未要求重复旧绿。非主组合/native终审。

## 最小复跑命令与原始证据

树根 `/Users/denny/projects/simple_harness-typed-recall-context-use-full`；raw前缀 `.local-test-evidence/2026-09-06/closure-physical-guard/`。旧raw留存，不在Git。

```sh
PY=/Users/denny/projects/simple_harness-typed-recall-context-use-full/.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python
LEAF=/Users/denny/projects/simple_harness-typed-recall-context-use-full
EVIDENCE="$LEAF/.local-test-evidence/2026-09-06/closure-physical-guard"
"$PY" /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py \
  --evidence-dir "$EVIDENCE/<new-run>" -- "$PY" -I -B "$EVIDENCE/run_tests.py" \
  "$EVIDENCE/<new-run>-temp" -k resumed_scope
```

原r2未加`-k`（当时只有8项）；r3筛选 `tamper or late_forget or unknown or cancel_reserve or slow_scope_change`；r4/r5筛选 `resumed_scope`。wrapper只置入既有H077/M616 installed targets及本树backend，fixture候选身份采用冻结H077 wheel；实际stack.start verifier保持。默认OS共享锁2GiB/180s及磁盘阈值，BUSY不换锁。

| raw相对路径 | SHA-256 |
|---|---|
| r2/command.log | `79f69f9fb8eb06f9aa5bfb6f4647cb587b9a1839f9807a71184501f967099ab6` |
| r2/resource.json | `e6763247c45c872c56d6c02f8466ab8fc6d7a3c727945264b8e308da06c6f166` |
| r3/command.log | `00e481da004d1190931f9e6caaf469154659f7becf96d9cf2a107dc669d989e5` |
| r3/resource.json | `e938ed9d2740ba4461f77d3c282858123456aaa85ef802990de30ef00f76b009` |
| r4-resume/command.log | `c25fb941b0cbc2836eaf4248dbcccb9c6db25ede5125a831da91377ed63f89f4` |
| r4-resume/resource.json | `d2e9d96015437a830ff2c42b93590b69d295d934bcea72cd14bc134b56dd0fdc` |
| r5-resume/command.log | `929de2bc1f2e18b9e7c0957c7505da9e4f9ce4c17e2cf6219aa9e599c3003ac5` |
| r5-resume/resource.json | `34767f9c6e06e382dd7f9005c1d27d2015551851d68b392c2368fff54c3a9c07` |
