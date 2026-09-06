# C01 首次评分交付与控制结果

2026-09-07。已验产品源码 `99d17c11`（基线 `b2da14da`）；合入顺序：`aa7675bd → 6f6fbe37 → a6a6c158 → 99d17c11`。本文件后继只记录结果。真实评分仍 **0条**，不称质量PASS。

## 必要控制与资源释放

| 批次 | 结果 | PG / 耗时 / 峰RSS |
|---|---|---|
| trace-r1 FAILED参数 | 先前PASS；本轮未重跑，含不完整观测unknown控制 | 原PG61549已清 |
| nonterminal-r2 | 1PASS，pytest 0.74s | 65098 / 1.291s / 121872KiB |
| init-r2 | 1PASS，pytest 5.87s | 65129 / 6.758s / 425072KiB |

后两批均exit0、remaining=[]、cleanup=null，共享锁已释放；最低磁盘分别1684/1670MiB。原trace-r1 NONTERMINAL与init-r1失败完整保留。临时vendor链接已还原，无installed修改。初始化实际APPLIED，生产authority重开，cleanup_errors=[]，embedder cold/not_started；未enqueue评分、未发网络Provider。

原始证据均在本树 `.local-test-evidence/2026-09-07/corpus-c01-controls/`：

| 相对文件 | SHA-256 |
|---|---|
| nonterminal-r2/command.log | b7b3aa92344a38bedb2ba74f765049f69319b8af87f14816cc3a4df38eeb870a |
| nonterminal-r2/resource.json | 10db03826fe07bf1360bed6ce0428fca61375b01baae7d554088797c8f74d6ff |
| init-r2/command.log | b46b4f2e5c4b2262b873374a783ed7c6381d1b4a4712b876eb0bf72c054f955e |
| init-r2/resource.json | 70b03aa0d936ad8fa180db041139d8fd79ea59c7e4353a53e0e5201282cfe34f |
| run.py | 51db2f9e34407bc6aab1ec837557c1f5a03ce4f8ac8abe3073110bc546a7bead |

## 首C01-10：主合代码后执行

**执行前必须再合222346d3/ac76e16a的精确审批与实际ingress打开补丁，见[新增组合结果](APPROVAL-RESULTS.md)。** 仅一例、一个独立root、无case重试/探针/judge请求。主合上述源码后在primary执行；不从动态主root混入自有树控制证据。下面资源目录与评分目录分开，二者均使用新的r1名称，已有则不得覆盖。

```sh
cd /Users/denny/projects/simple_harness-primary-candidate
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/memory619-artifact/installed:/Users/denny/projects/simple_harness-primary-candidate/backend \
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python \
/Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py \
  --evidence-dir /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/corpus-c01-real/resource-r1 \
  --rss-mib 2048 --seconds 180 -- \
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python \
  -m deskpet.quality.corpus_scoring \
  --corpus-root /Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20 \
  --compiler-root /Users/denny/projects/simple-harness-memory-sdk/scripts \
  --host-root /Users/denny/projects/simple_harness-primary-candidate \
  --installed-target /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/memory619-artifact/installed \
  --output /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1 \
  --case C01-10 --execute
```

资源预算2GiB/180s、默认共享锁/磁盘准入，不覆盖lockfile；超时保留失败，不补重试。生产driver保持25轮/50工具/同名同参数连续10次（参数hash改变会重置streak）；`BudgetPolicy()`无新增货币硬上限，不能把资源限时称金额限额。沿原config的gpt-5.5、max_tokens=8192、context_window_tokens=800000，不覆盖原模型参数/Provider deadline；实际物理请求以保存的原request为准。180s是外部本批限制，不改生产Run预算。

凭据仅worker从 `/Users/denny/projects/simple_harness/.env` 读取APIKEY/BASEURL进入进程；命令无secret，无复制.env/写keychain。worker输入仅原USER和公开setup执行所需字段；父进程在worker退出后打开oracle生成review packet。外部强停可能没有完整packet，此时以原始持久attempt/失败证据记录，绝不算通过。

## 原case与文件绑定

原r4 `01-exact.md:81`，C01-10，row_ordinal=10，category=exact；文件SHA-256：
`f40faead2d85c89d3c5b5f9d3c593a6ae28af530785c918f3681e38097fa0ac7`。
原冻结compiler核成员后记录source-manifest/original-documents/case.json，不改原件。

- setup：A：本人的待办清单按截止时间升序，没日期的放最后。
- 原USER：照我已存的待办排序约定，排一下：提交周五到期、复查无日期、取件周三到期。
- 原gold（仅事后评分）：取得A，取件→提交→复查；只是排序，不创建提醒。
- scenario_clock：2026-09-06T10:00:00+08:00，Asia/Shanghai；recent_messages=[]。本例不靠当前日期求日期值。C01-11等时间题仍不运行。

原required_types=semantic；类型命中不能替代原gold的事实、顺序与禁止创建提醒判断。终态后的主审阅引用实际trace；失败/未完成/观测不全均保留原ID与调用下界，unknown不是0。

## 单列限制

init-r2暴露既有 `backend/observability/sdk.py:144` 的 `MemoryManager.diagnostics_snapshot` coroutine未await警告。保留原日志，不扩改异步diagnostics、不因此重跑绿。它不使本次初始化控制失败；也不据此宣称SDK审计完整。真实评分若Provider/audit公共证据缺失，仍按现runner的不完整观测规则阻止完整计分。当前3个唯一无网络控制不是实际模型或240质量证明。
