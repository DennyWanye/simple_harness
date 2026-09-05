# S6 P1 / P2 admission 实施交接

2026-09-05，隔离分支 `feat/human-memory-s6-primary-preparation`，base `4eb1eb7c`。用户已授权本树源码/测试/ARCH提交；main、SDK、pins、UI、v47均未修改。

## 结论

- **P1 PASS（自动化范围）**：复用现 `/ws/control` default transport和已有signed `companion_profile_bind`，没有新URL、Rust/TS scope、账户或每读重签。生产dispatcher由内存TestClient驱动，不启动lifespan、App或Provider。
- **P2 admission PASS**：现service/API允许省略scope_ref；真实SQLite保持一条durable turn，None scope/零TaskScope，无假任务。显式scope权限不放宽，非法输入无evidence/queue写入。相同delivery_key重试/DB重开幂等，改文本冲突且原evidence ID/hash不变。
- **P2 runtime BLOCKED（交叉owner待协调）**：没有降低route/binding/terminal校验、没有假history。生产context port探针实际停在 `foreground_task_scope_authority_missing`；这不是SDK standalone成功证据。UI继续未接，不把admission ACK称为对话完成。

## P1 验证与最小性

现 `CompanionControlIngress.execute` 已核验真实窗口signature、connection/control epoch/challenge/request序列和Host trusted auth snapshot，然后事务绑定profile。现 `LocalAuthSnapshotProvider`本来只支持local身份；固定 `deskpet-local-owner-v1` 继续复用，换profile namespace反而会破坏既有primary/evidence幂等。

仅增加连接对象 `HumanMemoryControlBinding`：成功bind后冻结身份；每条HUMAN请求前检查同一connection、当前owner/binding epoch，以及既有durable active lease的process/control epoch/challenge/window/scope。全局gate ready不足以证明“这个socket已bind”；同owner新连接也会撤销旧lease，因此增加已有lease只读检查。unbind/rechallenge/另一连接仅收到ready都不能继承authority。

请求之间无新签名/no额外用户确认；bind自身防重放用原primitive，消息重放用原durable delivery key。已有legacy primary-ID fence、request authority字段拒绝、project effect exact auth保留。已admit的Run仍沿原冻结authority/lease执行，不把窗口断连等同取消Run。

## 需要主协调的具体交叉点

读到Dirac worktree `simple_harness-s5c-preparation` 的计划：

| S6闭环必需修改 | Dirac已声明工作 | 建议owner边界 |
|---|---|---|
| `execution/foreground_queue.py::record_sdk_terminal`：无scope不能再要求TaskScope ingest/gate/semantic receipt；仍须确证SDK terminal、generation、单事务turn settled+Memory outbox | S5c README T4：同一terminal事务追加occurrence settle；T5：immediate与普通ingestion不得双建job | **同一owner改terminal事务**。请主指定S6或Dirac负责合并实现，另一线通过窄port调用；不能两个分支各复制一套终态结算 |
| `foreground_runtime.py::SqliteSdkTerminalObserver.observe` 与 `evidence_ingress.py`：独立普通Run的durable终态证明，Run中route成task仍保留exact lineage | Dirac occurrence/ack/terminal与生命周期analysis依赖同Run事实 | 固定terminal evidence/receipt字段及原子边界后再接standalone observer；不凭SDK query状态直接放过Host gate |
| `foreground_runtime_ports.py`无scope Context/Tool分支、`foreground_runtime.py` initial_route=None、`main.py` composition | Dirac context snapshot presented/ack、catalog与composition也会接线 | S6可负责无scope分支；共享context source/terminal契约确定后实施，route后effect权限仍由原Host receipt负责 |
| 必需durable primary history reader/派生快照 | Dirac占用v47 prospective/action表 | 不占v47、不重写v45/v46；先核对现public SDK reader能否恢复最近完整因果turn groups，再决定是否需要独立追加migration；不能用空history或TaskScope ResumePackage冒充 |

交叉依据：Dirac `plans/2026-09-05-human-memory-s5c-preparation/README.md` T4/T5，以及INTERFACES §4/5。检查时其未提交代码为独立s5c_schema/s5c_store/migrations/s5c与测试；本树没有编辑这些文件。ARCH/PROJECT_STATUS两线均会新增状态，合并时保留两份条目。

这不是请求重新批准S6目标，而是按用户要求交还共享文件owner协调。当前没有擅自引入第二terminal ledger、伪scope或跳过closure使测试绿。协调后继续原P2 runtime及真实SQLite+deterministic Provider闭环，再交UI；仍不启动真实App/Provider。

## 命令与结果

cwd均为本worktree；解释器 `/Users/denny/projects/simple_harness/backend/.venv/bin/python`，`PYTHONPATH=backend`保证Host源码来自本树，DB用pytest tmp_path。没有修改该venv。

```sh
PYTHONPATH=backend /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/memory/test_primary_control_binding.py backend/tests/companion/test_window_control_credentials.py backend/tests/memory/test_primary_turn_admission.py backend/tests/memory/test_human_memory_api_and_fence.py backend/tests/memory/test_human_memory_service.py -q -p no:cacheprovider
```

结果 **29 passed / exit 0 / 3.80s**。随后增加“同delivery key换transport request_id”断言，仅窄重测admission文件 **5 passed / exit 0**，未重跑全量。

admission修改前有效基线：**1 failed / 4 passed / exit 1**，无scope API返回 `human_memory_request_rejected`；修改后5 passed。最初测试脚手架曾有pytest reserved parameter、CompanionStore无close、公开challenge不含hash等错误，已修正；这些不计产品红基线。独立context探针首次因macOS /var symlink被现storage gate拒绝，resolve tmp路径后才得到下述有效结果。

```sh
PYTHONPATH=backend /Users/denny/projects/simple_harness/backend/.venv/bin/python .local-test-evidence/2026-09-05/s6-primary/p2/standalone_context_probe.py
```

有效探针：**exit 1**，实际SQLite candidate scope=None/binding_revision=0；`TaskScopeForegroundContextPort.draft_lineage`拒绝 `foreground_task_scope_authority_missing`。尚未进入SDK/Provider，没有完整runtime PASS、没有timeout/hang。原四历史红不在本次测试范围。

## 原始证据索引

以下文件均ignored，不提交原日志/DB；这里只记录索引与hash：

- `.local-test-evidence/2026-09-05/s6-primary/focused-final.log` — SHA-256 `ce07f212f8997ab2b26b7f832148adc19ddcd22453b3360142b20ffd2cb62221`
- `.local-test-evidence/2026-09-05/s6-primary/p1/pytest.log` — SHA-256 `039cd3baed9ff555f9e78ac7a1ac2e57eae4390781d6ec35efa358cfb98e2fad`
- `.local-test-evidence/2026-09-05/s6-primary/p1/ws-fixed.log` — SHA-256 `224a89eacef8d510a0aa5cf9200b0b582e23b54815eb8c7f083da51589eacc1d`
- `.local-test-evidence/2026-09-05/s6-primary/p2/admission-baseline.log` — SHA-256 `59e41bc9f0b0a60cbc911833df79976adeef2d80294c963348a2cf49e9b61bd5`
- `.local-test-evidence/2026-09-05/s6-primary/p2/admission-after.log` — SHA-256 `4630317ae9c9c20fcca0bbfcf9bd623c572a322bdb9593b4187b18579a76321f`
- `.local-test-evidence/2026-09-05/s6-primary/p2/admission-final.log` — SHA-256 `50f58ac0ec6d19174107e8aee7cf056cbb76ae70a0f92357e59525f21eee28a4`
- `.local-test-evidence/2026-09-05/s6-primary/p2/standalone-context-resolved.log` — SHA-256 `9d0cb30a5679d894fed644f85a2eed1c4ac43c6770a78dbdaeff2e629b77e3cd`
- `.local-test-evidence/2026-09-05/s6-primary/p2/standalone_context_probe.py` — SHA-256 `8cc4368c2d9b86074955c4cd82c998a3a36d7f8d80a64d63dacdba86c31b8f78`
