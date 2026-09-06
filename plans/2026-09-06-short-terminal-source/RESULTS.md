# r8 窗口外短期投影失败：真实来源补齐

2026-09-06：Dirac对02bf7dcc固定源、实际副本及3项分批控制最终限定ACCEPT，已合primary候选；generation接线另补，未把projection成功写成short recall通过。

最后更新：2026-09-06。Host base `2c8c57c6`，自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/short-projection-source-lineage`。Procedure 契约 `9deb3610` 保留在原分支，未继续实现。本叶没有改 SDK 或构建制品；复用实际 installed H077/M617，尚待主/Dirac 独审和主组合/native验证。

## 根因与生产修复

在主提供的最终 diagnostic-copy-v3 上再派生工作副本，调用实际 installed M617 公共 `build_human_memory_v7` / `rebuild_short_horizon_projection`，稳定得到：

`rebuild_short_horizon_projection → _resolve_suppression_unlocked → _resolve_suppression_snapshot_unlocked → duplicate_source_matches → _ancestors → MemoryCorruptionError("history_source_lineage_missing")`。

源码锚点：Memory `sqlite_v5.py:2106/1714`、`history_source_guard.py:300/95`。首个 eligible 组从最近十组排除窗口出来后，有真实遗忘记录时触发 duplicate-source 祖先校验。Host 已注册 assistant/tool envelope 引用真实 terminal runtime_event，但 `PrimaryShortIndexingService.register_group` 仅入场 USER 和各消息，漏了 terminal。副本里缺失目标在 Host state.db 中实际存在，首组 terminal ID 为 `3242152e-22b7-5cc0-9e93-84a50cb0d933`。不能删 ref、跳过 missing ancestor、放宽 suppression 或修改旧注册来绕开。

最初怀疑 SQL NULL 被转为字符串；实际 trace 否定该方向，10 条认知 revision 的 content_json 均非 NULL，未为猜测修改 SDK。

两处生产接线：

- `conversation_registration.py`：ConversationGroup 新增内部必填 `terminal_source`，携带 `_group_tx` 同读事务已核验的真实 terminal envelope/receipt。它不是额外 conversation item，不改变 manifest、registration identity/hash 或消息数量。
- `short_indexing.py`：重放实际 USER 原 analysis lineage 后，先通过公共 `admit_evidence_source` 入场 terminal，再登记原消息；终态仅 source admission，不创建分析 job。lost-ACK 后原公共幂等接口恢复。增加 `short.after_terminal_source` 故障点，未新增 DDL/flag/权限接口。

现有 worker 新实例从空确认缓存开始，正常扫描即可补旧库缺失来源；本叶未修改原 userdata。部署需使用新 worker/重启后的实际运行栈，不能声称同进程旧 `_confirmed` 缓存会因代码文件变化自动失效。没有尝试补全任意外部祖先图，当前生产 terminal 的真实来源由既有 Host reader 验证。

## 结果与范围

| 批次 | 实际结果 |
|---|---|
| r1 | 原 installed M617 公共 rebuild 原红，phase=public_rebuild，精确异常如上；脚本捕获异常所以 carrier exit0 不代表功能通过。PG81498/125600KiB/0.869s/remaining=[] |
| r2 | 修复验证脚本误把表名写成 analysis_jobs，实际为 jobs；公共修复调用前即失败，保留原 carrier。没有据此修改产品 |
| r3 | 自有 Host 两处修复 + 同一 installed M617，真实副本公共 reconcile/关闭/重开/reconcile 通过；13 组、source admissions 27→40、chunks 0→3，重开不重复增加。registration、suppression、认知 revision、jobs 的全行 hash 保持。PG81995/138560KiB/2.164s/remaining=[] |
| r4 | 自有稀疏树缺当前 H077 candidate manifest，三项均 fixture setup error；补签入树已有的确切 H077/M617 vendor 文件，无安装、无业务修改 |
| r5 | 新三项控 2 PASS/1 FAIL，4.51s；窗口退出+遗忘、lost-ACK+重开通过。篡改测试在更前置 append-only trigger 被拒绝，测试未声明该预期，因此失败；原测试副本保留。PG82131/181776KiB/5.093s/remaining=[] |
| r6 | 仅修测试：明确断言 append-only 拒绝，再用真实错误配对 receipt 调公共 admit 验证拒绝。定向 1 PASS/2 deselected，3.71s；未重复前两绿。PG82219/173424KiB/4.265s/remaining=[]，锁释放 |

新增三项回归位于 `backend/tests/memory/test_short_terminal_source.py`，使用实际 Host turn/outbox/SDK 与确定性 transport fixture，未调用真实 Provider/模型。首项验证 unrelated active forget 不再让首个窗口外组崩溃，随后直接遗忘该组来源仍使 projected_chunk_count 归零。最终副本的既有两次 semantic forget 原样保留；不把其“3 chunks”计数称为所有内容语义均已独审。

尚未构建 generation、加载 embedding、运行 shortquery 或 native；`NO_ACTIVE_GENERATION` 后续是否经主实际生成/查询闭合，由主组合继续。本叶不宣称真实 short recall 或原 program 全绿。

## 本地证据与复现入口

只使用主最终副本：`/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/native077617/primary-ui-j_8ulfwm/diagnostic-copy-v3`。r1/r3 均核该副本三库 hash 前后不变；没有再读取原 source DB/WAL 或前两失败副本。

本叶 raw 根：`/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/short-projection-forgotten/`，全部 ignored，未提交。根下 `repro.py` 原红、`verify_fix.py` 实际副本修复、`run_controls.py` 定向 pytest；各 rN 有 command.log/resource.json，r1 有 exception.txt/result.json，r3 有 result.json。

执行统一为：既有 `/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0614/venv/bin/python` 调 `/Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir <全新rN目录> --rss-mib 512 --seconds 90 -- <同Python> -I -B <上述carrier绝对路径> <同rN目录>`；r6 末尾追加 `-k tamper`。使用默认共享锁，无 lock-file 覆盖；全部组均已清空，未用额外环境或大型测试。

| 固定成员/证据（源码相对 Host，raw 相对上述根） | SHA-256 |
|---|---|
| backend/deskpet/memory/conversation_registration.py | baefeb64963512426f8436cdc4c9e62488a48d1f80c12d8a4b860d6a258d2a5c |
| backend/deskpet/memory/short_indexing.py | d63caf50ba886a071189db163e8b8267533a194f959d2d9526a263ae7fb089f9 |
| backend/tests/memory/test_short_terminal_source.py | 92dacef70c2b10503edea4fa0c57ed490f84af326112c20031a10787bae3d3e6 |
| r1/exception.txt | cce43001d59d51656c3256b1cb747a2d12f3c09a95f584d0bff3e18e20cabc18 |
| r1/result.json | a82f9dcdb2b0ec1f6c31074ee4007d1d993c909c000bb0c03a72e3aee2b938bf |
| r3/result.json | 4c9c8d63305d300d1d9c681909564063d3cf33a5964f3e364865fd30e6eb7319 |
| r5/command.log | 0b77ac6152ce065418b4846c23d753c4b9d56bb74d8a2cc2a9835704d32c9d96 |
| r6/command.log | a83ab0454599b074f9e2370f5371f17dcf41813f52a950f8d7e499f350d534ba |

请主转 Dirac 挑战：来源是否确实同事务回读、入场未签分析/effect、旧 registration/hash 不变、丢 ACK 后是否可恢复、直接及 alias suppression 是否仍强制。当前无 Dirac 直连入口，未声称独审接受。
