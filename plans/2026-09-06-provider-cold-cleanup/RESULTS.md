# Provider cold cleanup 结果

2026-09-06：Dirac已对固定7b9f4721源码和唯一必要控制最终限定ACCEPT；已合primary候选，未重跑旧绿，不外推新组合native。

日期：2026-09-06。分支 `feat/provider-cold-terminal-cleanup`，base `2c8c57c6`。
产品 `f0f72650`；最终测试源 `79508593`。Dirac 产品源码预审通过，执行结果终审待续；未合主。

## 实际分批

所有原始输出在本树 `.local-test-evidence/2026-09-06/provider-cold-cleanup/`，未提交Git。

| 批次 | 结果 | 原因/范围 | 资源 |
| --- | --- | --- | --- |
| r1 | 1FAIL/2deselected | 旧075 producer错误沿当前Host pin，wheel SHA拒绝，未进业务断言 | PG81327 exit1，remaining[] |
| r2 | 1FAIL/2deselected | fixture假定directURL有hashes字段，实际缺失 | PG81403 exit1，remaining[] |
| r3 | 1FAIL/2deselected | fixture又假定有旧hash字段，实际archive_info为空；此无效重试属执行失误 | PG81440 exit1，remaining[] |
| r4 | **1PASS/2deselected，4.32s** | frozen075 manifest验证实际旧wheel；原candidate verifier继续检查version/directURL；真实production resolver冷恢复 | PG81519 exit0，remaining[]，cleanup_error=null；5.040s总耗时，峰456544KiB，最低1802MiB磁盘 |

只有 **1项 unique** 受影响检查通过，不将重跑或子断言相加。测试载体为既有077 installed-target + M616 installed及075 legacy target，复用venv074614通用依赖；不是H077/M617、独立完整安装或native。

覆盖：旧075真实授权过期/Stop后重建Host+SDK stack；用真正 ProductForegroundProviderPort/main resolver替换原fixture no-op port；原Run公开恢复到唯一终态、Host FAILED receipt与公开proof一致；零新Provider/不重prepare；后置effect gate已释放；非法terminal state拒绝；已有真实binding的同名内部KeyError传播，恢复后正常清理；第二新stack同proof、无工作重放。

## 命令

在本树执行，`run_tests.py` 是保留的 ignored 载体，固定上述三个SDK路径及本树backend，未修改生产pin或安装：

```sh
PY=/Users/denny/projects/simple_harness-typed-recall-context-use-full/.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python
"$PY" /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir "$PWD/.local-test-evidence/2026-09-06/provider-cold-cleanup/r4" -- "$PY" -I -B "$PWD/.local-test-evidence/2026-09-06/expiry-terminal-public-host/run_tests.py" "$PWD/.local-test-evidence/2026-09-06/provider-cold-cleanup/r4-temp" -k real_old_host_expiry
```

这是已执行命令；未来必要重跑须换新的证据/basetemp目录，不能覆盖原红绿。

## r8 只读取证与边界

08:45Z使用正确 `uri=True + mode=ro + query_only` 读取r8复用的原r6 userdata：唯一SDK `run.failed` 恢复事件，经Host S1 observation绑定到 generation3 FAILED receipt。Host落库08:31:14.501562Z，先于KeyError08:31:14.515087Z。此为只读forensic，不是跨库原子快照/公共consumer测试，不宣称原库以后不变。

最终索引 `r8-terminal-identity-bound.json` SHA256 `0b618bee4c201492336e4b3a541228d124f89c48231cc67b1dd3e114a14240e7`。前两次错误读取漏uri=True，不能归因环境；第一次直接比较Host S1与SDK event混淆命名域，原文件保留，以上后继按S1内raw SDK身份对齐纠正。后续原生取证只用主保存的diagnostic-copy-v3，不再读原库。

原生r8长召回/遗忘zero/图谱可见是主采证；旧blank未复现不等于代码修复。其退出resource125与最终remaining[]保持独立失败事实。本叶不修改SDK/原库，不复跑原analysis14/cold其它两控，不声称完整native或program通过。
