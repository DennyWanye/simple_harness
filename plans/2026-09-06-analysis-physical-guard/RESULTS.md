# Analysis physical guard 限定交付

最后更新：2026-09-06。

产品固定 `bd5b1180`，测试补强固定 `9bda5f64`；base `9f745c7e`。
主提供的 resolver seam `e3b6a360` 在本树为 `951b1a62`。
主已报告合入产品，并于 `a1fe4f75` 将真实 `_analysis_adapter` 接入专属 guard。
本叶不改 main 接线或 SDK 制品，原 `232fca14` 审计分支保留。

14 个独立用例分批到绿，不把同一用例的必要重验加成新数量：

| 批次 | 结果 | 事实与纠正 | 进程/退出 |
|---|---|---|---|
| r1 | 1 setup ERROR | fixture 默认 subject 未随认证 subject 更新；尚未进入业务，83e42af1 修复 | 59687 / 1 |
| r2 | 4 PASS、1 FAIL，3.69s | 实际物理发送/物化/重开、text/temperature/metadata 通过；tool 变异构造使用 frozen nested DTO，8531e2e9 解冻公开 parameters 后再构造 | 59803 / 1 |
| r3 | 10 PASS、4 deselected，6.70s | 只执行余下十项，产品未改 | 59931 / 0 |
| r4 | 3 PASS、11 deselected，2.91s | 只强化 slow-change 三项：实际 configure 提交并重读相同 binding_ref，或 public suppress 返回匹配 decision 后才承认变化完成 | 60348 / 0 |

四批 remaining_group_members 均为空，cleanup_error 均为空。r4 resource 总耗时3.636s、
peak350912KiB、minDisk6143MiB，槽已释放；后续 native 由主单独占槽。本叶不再重跑已绿集合。

## 验证边界

实际生产 resolver → ProductProviderAdapter → HTTP MockTransport 是物理发送计数依据；
使用真实 Host attempt/原子 S1 carrier、公开 Memory job/mutation/recall/visibility。
成功正控实际得到 memory object_value=coffee，并验证 succeeded 响应恢复和 reopen 不新增发送。
七项输入/绑定变异均拒绝且0发送；三项已提交的权限/来源/候选变化在慢读后拒绝。
提交前/后故障验证 attempt 与完整输入同时消失/保留，且无物理发送。
默认 foreground guard 与显式 None/noncallable 拒绝仍在。

前置 source terminal 使用既有确定性 Host fixture，并非本叶新执行的真实 SDK/native Run。
恢复测试只证明同一 durable succeeded response/reopen 零重发，不声称撤权之后恢复已验证。
跨 Host/Memory 两库没有原子事务；证明的是物理 delegate 前的最终读取检查。
本叶没有真实模型/联网 Provider/native；主 r6 属独立验证，不能用14控宣称其通过。
closure/compaction 同类后台接线、原401/program完成度不由本叶替代。

## 复跑方式与原始索引

工作目录为本隔离树；复用既有 tiny Python 与明确 H075/M616 installed target，
不是新独立安装或候选 pin 验证。原始文件全部 ignored，不上传 Git。

```sh
PY=.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python
ROOT=.local-test-evidence/2026-09-06/analysis-physical-guard
"$PY" /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py \
  --evidence-dir "$ROOT/tests-NEW" -- "$PY" -I "$ROOT/run_tests.py" \
  "$ROOT/basetemp-NEW" -k slow_visibility_then_current_change
```

`NEW` 必须用未占用目录；不覆盖旧证据。r1/r2没有-k；r3使用
`-k 'not real_resolver_send and not text and not temperature and not metadata'`。
r4命令如上，实际后缀r4。资源入口保持默认共享锁/2GiB/180s/磁盘门槛。

证据根：`.local-test-evidence/2026-09-06/analysis-physical-guard/`。

| 相对文件 | SHA-256 |
|---|---|
| tests-r1/command.log | a76b7599aa17273479213864575e19c68434fbb14339cc0daf0c64fbe7f1c4d9 |
| tests-r1/resource.json | 7d615eef809660ecfa531b829b9222139f68f4c4deeda485e2555ad4033a2913 |
| tests-r2/command.log | 2b3146c87f3864ea925c44da2ceb13addd8296dc3292e81a87fabb0f2d8727f8 |
| tests-r2/resource.json | fd00849c57c095d8d495164c33ef8a2d2c83929288ae146570819ee197528c05 |
| tests-r3/command.log | 4717ec25139e1bdf1a096b011d4b5fbeb2a98b8537cb1fe18195c6157e8cdfb6 |
| tests-r3/resource.json | 0334e488edadf42f58fe478cc12eaf43222131af5be8c25fc3ae7fa06b16b903 |
| tests-r4/command.log | fadcf7e0ad5e34305020fc8585b74d4792f59b1bcfba3acc6a71f645d68d2a7b |
| tests-r4/resource.json | 5bbdc523a064d543086a29e5f4c9e79d642af00e13dc36490b2685ecdc1217d3 |

独立审查：Dirac 对9bda5f64最终限定 ACCEPT，已读 r2/r3/r4，确认14 unique分批绿、
变化成功提交断言与普通恢复注释边界；本叶无剩余P0/P1。主factory默认接线/native仍由主独立验证。
