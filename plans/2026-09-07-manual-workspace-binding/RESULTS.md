# Manual workspace UI 分批结果

2026-09-07。固定源 `ef0ed7bf4ad1b22dc82bb892ae8eb774c94fdcb8`；H079/M619/S0313 来自主组合既有 `memory619-artifact/installed`。限定源/真实Host运行fixture/模拟HTTP与UI transport；不是原生或模型质量通过。

当前汇总：**7 backend＋4 UI，11个唯一控制分批通过**。首批6＋4保留，仅原rebind红在48169ae8修正后单项复验1PASS；以下r1失败记录仍是原事实。Dirac已核固定产品ef0ed7bf、夹具修48169ae8及r1/r2，给出限定终审 **ACCEPT**；未合主/未新native。

- Backend：**6 PASS、1 FAIL，33.73s**。exact（含重新hash错base拒绝）、mixed identity、allow后append故障、expired、独立进程cold exact读取、34 resolved历史＋33 pending分页通过。
- UI：**4 PASS，1.35s**。包含真实PrimaryChatView/controller条件卸载、不同owner/primary隔离、原namespace仅exact status恢复及零自动重复decide。
- 原root7、Auto2及其它旧绿未执行。首批实际独立通过数10，完整本叶尚未通过；没有把冷进程给定ref回读当App冷启动自动发现。

## 唯一红例

`test_actual_route_manual_public_ui_source[rebind]` 收到 `human_memory_request_rejected`，原oracle要求 `human_memory_connection_stale`。provider夹具callback约5095ms后断言失败；该原失败留存，不改成PASS。

静态复原：Manual `pending/respond` 外层 `human_memory_request_boundary` 持有shared lease；夹具 `slow_read` 在同task内await原 `_bind`，其 `ProfileBindingCoordinator.bind` 必须等待同一 `RevocationBarrier.exclusive`、条件为readers==0、默认timeout=5s。夹具未返回就不能释放reader，形成确定的非法重入时序；r1日志未直接保存内部exception traceback，分类依据源码路径和已保存外层事实，不能说已实测SQLite锁故障。

修复仅test：模拟生产生命周期在独占等待前可暴露的真实 `IdentityReadyGate.unbind`，旧请求应保持stale拒绝/零decision；待共享边界退出后再执行真实签名 `_bind`，新连接重新读取并走原合法决定路径。不改产品、不延长timeout、不删stale断言。**修后该1项 NOT_RUN**，10绿直接保留。

## 命令与原始索引

本树 `.local-test-evidence/2026-09-07/manual-workspace-binding/run_batch.py` 在一次默认 `scripts/run_resource_bounded.py` 锁内串行运行 `run_backend.py` 与 Vitest；backend只选新 `tests/execution/test_primary_workspace_binding_ui.py`，UI只选 `src/primary/PrimaryWorkspaceBindings.test.tsx` 与 `src/views/PrimaryBindingRecovery.test.tsx`，单worker。载体保留当时exact source guard，不能当当前源命令重跑整批；后续仅该rebind红项。

原始文件均在本树 ignored `.local-test-evidence/2026-09-07/manual-workspace-binding/r1/`：

| 文件 | SHA-256 |
|---|---|
| command.log | 757ee13a9a7ee92e46d8edcce9a02bdc26e907fbd114575e91a1d30f0318153b |
| resource.json | 9896c8e1b3d8773e20fd78a36b9ef2fa3e5be4b5f623259c0d82d2712494c61b |
| loaded-origins.json | a5a45312ca52dfeec663eb536612ad91435ac354256f6f724e789a4afb79f6a1 |

PG **68756**，exit1、36.042s、peak442896KiB、minDisk5470MiB，`remaining=[]/cleanup=null/stop_reason=null`；资源已立即交Carver。caffeinate56392未触碰。原无journal orphan、SQL全历史扫描成本、App进程退出后的UI自动恢复与当前候选原生仍明确待验。


## r2：只复验原 rebind 红

固定 `48169ae8f3be289f5f4e91375038eea010b936cc`，产品仍ef0ed7bf；同H079/M619/S0313 target。唯一选择：`tests/execution/test_primary_workspace_binding_ui.py::test_actual_route_manual_public_ui_source[rebind]`，**1 PASS /3.44s**。慢read后真实gate失效使pending/decide以原stale拒绝，零decision；共享边界释放后实际签名重绑、原Manual授权/文件路径继续通过。不宣称完整rebind在shared lease内提交。

沿用默认共享runner/2GiB/180s，ignored载体 `run_rebind_r2.py`，PG **69778** exit0、4.059s、peak186560KiB、minDisk4427MiB，`remaining=[]/cleanup=null/stop_reason=null`。已立即向主释放槽，10绿未重跑，无模型/native/构建/新env。

同ignored父目录 `r2/` 索引：

| 文件 | SHA-256 |
|---|---|
| command.log | 17b503cda9b730e097a8a42687173c7eb6c151e7464fe0c4ff842c2def27901b |
| resource.json | d48327b6e3a07dda5d04b204d07d397c62539e63d632c71c2a095b780fff70fe |
| loaded-origins.json | 52d6642b6c6c01bbec2fd5319e3ad0f813edf27920e20c6399fc67aa64f57178 |

修后NOT_RUN是481提交时状态，现在由r2限定结果取代。原无journal orphan、历史SQL扫描成本、View进程退出后原已bound引用自动发现、真实主组合/native仍是边界；S3/program不标完成。


Dirac终审为只读源与已有结果复核，没有另行执行测试。结论仅覆盖本叶真实Host/公共权限链和UI模拟transport：未见新P0/P1。后续由主一次组合构建/native验证，不为本叶单独重复build或11项绿。
