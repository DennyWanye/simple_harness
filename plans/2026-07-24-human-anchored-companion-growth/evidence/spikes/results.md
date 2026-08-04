# 人类附属伴生助手架构 Spike 结果

> 日期：2026-07-24  
> 性质：一次性架构验证，不是业务功能实现  
> 最终校准快照：`2f5436efe7ce92638b84332c99c7a9714904dd9a`  
> 最终结论：SP-01～SP-11 全部通过；SP-02 已在第 5 轮按真实
> `ReactFinal / ToolBatch(1/N)` 组合根重跑，发现并验证了 execution writer 优化门

## 1. 执行边界

- 使用项目解释器：
  `F:\projects\deskpet\backend\.venv\Scripts\python.exe`
- 逻辑命令：

  ```powershell
  $env:PYTHONPATH='F:\projects\deskpet\backend'
  F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q -s `
    F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\spikes\work\test_spike_suite.py `
    --basetemp=F:\projects\deskpet\.sp

  # 第 4 轮的四事务模型重跑（第 5 轮已判定模型不准确，仅保留历史）
  F:\projects\deskpet\backend\.venv\Scripts\python.exe `
    F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\spikes\work\sp02_recheck.py `
    F:\projects\deskpet\.sp2

  # 第 5 轮：真实 SqliteExecutionUnitOfWork + ReActDriver 分支重跑
  F:\projects\deskpet\backend\.venv\Scripts\python.exe `
    F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\spikes\_sp02_round5.py

  # 第 5 轮复审：真实 fake HTTP transport、MCP stdio、local worker 的 split ACK
  F:\projects\deskpet\backend\.venv\Scripts\python.exe `
    F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\spikes\_sp10_dispatch_start.py

  # Windows Job：suspended-create/assign/resume、late child、helper close/crash
  F:\projects\deskpet\backend\.venv\Scripts\python.exe `
    F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\spikes\_sp11_windows_job.py
  ```

- 完整套件结果：`9 passed in 4.33s`，退出码 `0`；第 5 轮 SP-02 退出码 `0`。
- 这些测试混合使用真实 DeskPet 组件与一次性最小原型。原型只用于证明契约可行，
  不能当作生产功能已经完成。

## 2. 每个假设的结论

| Spike | 结论 | 对执行计划的约束 |
|---|---|---|
| SP-01 | 通过。Run start 可冻结原 Goal association 与附加 delivery；同一 ReAct Run 的 3 次物理 provider 调用各有独立 invocation；completed outcome 可无网络重放；unknown 不重发；半截 stream 只在内存；无 provider 的 Workflow 不伪造 invocation。 | 保留通用 `RunStartSnapshot + per-invocation ledger + provisional stream`，Kernel 不加入 Companion 分支。 |
| SP-02 | 通过（第 5 轮真实路径重跑）。50ms 固定 provider、每侧 20 个样本：`ReactFinal` 当前 Driver 为 0 笔写，计划增加 claim/outcome 后为 2 笔；`ToolBatch(1)` 当前为 boundary、goal、turn-fence、batch/attempt、1×prepared 共 5 笔，增强后 7 笔；`ToolBatch(3)` 为 `7→9` 笔。若继续每笔新开 SQLite connection，p95 分别回退 `43.257% / 21.793% / 10.887%`，不通过。保持 `WAL + synchronous=FULL` 和相同事务数，仅改成 UoW 内单进程串行的长寿命 writer connection 后，p95 相对当前绿色基线分别为 `+6.734% / -32.379% / -42.648%`，通过 10% 门。Kernel start/terminal 是比较两侧共有提交，本 spike 明确不冒充其完整组合根。 | 禁止再写“统一四事务”。Task 3 必须先实现、故障验证并启用 execution 单 writer connection；事务边界不合并、durability 不降级。随后新增每个物理 provider invocation 的 claim/outcome；Final 与 ToolBatch(1/N) 分别列账，Task 14 再测完整 ProductVenue/Kernel 组合根，任一路径 >10% 都阻断。 |
| SP-03 | 通过并发现缺口。真实 `CapabilityStore/Manager/Hub` 能用复合 `scope_key` 隔离 A/B owner 的同名 pack，且 Run lease 冻结 instruction；但 `capability_bindings` 尚无显式 `owner_key`，descriptor 也没有 managed instruction ref。 | Task 7 必须补显式 owner-generation、instruction/workflow refs 与 managed-root reconcile；不能把复合字符串隔离误写成最终契约。 |
| SP-04 | 通过。固定 `workflow.personal_v1` 的模型可见参数不含 owner/version/graph；同一 selection 只能创建一个 child；effect settle 后、checkpoint 前崩溃，按 `hash(child,selection,graph,node)` 恢复时物理 effect 仍恰好一次。 | 使用固定解释器和声明式图；不生成或执行任意 Python，不建立第二套 effect/确认协议。 |
| SP-05 | 通过。基于调研快照的真实 SessionDB v19 建库后，一次性 v20 callback 原型能保留旧 row/id/sequence，建立 owner-generation-event 唯一约束，完成 route CAS relocation；Companion 行不进入 FTS，上下文可见普通消息仍可检索。 | 这里只证明 callback 路线；实施时由 Task 0 动态锁定实际 `S→S+1` 和 migration ordinal。SessionDB 必须用 Python callback 事务重建，不能用普通 `ALTER`；profile inbox 与 history/live reducer 路线保留。 |
| SP-06 | 通过并发现缺口。真实 Manager 在 `candidate_ready` 崩溃后没有 active binding；可从 Store 的 verified/candidate phase evidence 重建精确 handle；当前通用 `recover()` 会返回 `candidate_recovery_requires_restart/unknown`。 | 新增 typed staged lifecycle façade 和 publish-intent reconcile；不能直接复用当前 `install()` 假装完整恢复。SP-06 当时的单目标原型名为 `activate_prepared()`，正式计划已扩展为 operation-scoped `activate_prepared_set()`。 |
| SP-07 | 通过。持久前台 Run 状态可在重启后重建 busy；identity 未就绪、quiet hours、错误 owner 或前台繁忙时均不能领取后台 job。 | ForegroundActivityGate 读取 durable execution 状态，不依赖进程内计数。 |
| SP-08 | 通过。reflection/evaluation 无聊天、TTS、codifier、二次成长和外部工具；delegated job 只能使用 durable grant 内的工具。 | 后台复用 Kernel，但使用独立 adapter/collector；`capture_growth=false`。 |
| SP-09 | 通过。事件同 payload 幂等、异 payload 冲突；同 owner-generation-name 只保留一个 genesis；growth permit 只能消费一次；action effect 进入 unknown 后不重发；forget 与 activation 竞态中 quarantine 胜出；authority 只有一个 durable writer。 | 保留双授权域、共享撤销 barrier、single-authority cutover 与 fail-closed unknown。 |
| SP-10 | 通过。真实 `httpx.AsyncClient + fake AsyncBaseTransport`、MCP SDK `1.28.1` stdio server 和本地 worker 均验证：transport/pipe handoff 前取消时 effect/物理计数为 0；正常路径 ACK 分别约 `10.762/14.727/34.284 ms`，长 completion 分别约 `260.215/302.451/439.160 ms`；ACK 后 crash/timeout 均为 `unknown`。MCP 通过构造 `ClientSession` 前包装 SDK 公开 write stream 实现，没有读取 `_write_stream`。 | Task 3/6 必须落正式 `PreparedDispatchAdapter`；Provider/MCP/local-runtime 各有具体 adapter/文件 owner，ACK 只表示 frame/pipe 已交给冻结 transport，不表示 response；可能 handoff 而无 ACK 时只能 unknown。 |
| SP-11 | 通过。Windows 真调用 `CREATE_SUSPENDED → AssignProcessToJobObject(KILL_ON_JOB_CLOSE) → ResumeThread`；resume 前 marker 不存在。root 退出后 late child 仍存活，但 helper 正常关闭 Job 或 helper 被强杀时，launcher 与真实解释器子进程全部消失。 | Task 6/7 复用同一 Windows Job helper；Task 15 lifecycle helper 必须长期持有 Job handle。进程不得在成功 assign 前运行，Stop/helper crash 均靠 Job membership 收敛，不靠镜像名广杀。 |

## 3. 失败尝试带来的真实修正

1. 第一次收集阶段失败：
   `orchestration_controls` 导入已不存在的 `ResourceSelector`。这是共享脏工作区的上游
   contract drift，本计划没有修改业务代码；一次性测试改用等价 schema 原型继续验证。
   这条证据使 Task 0 必须等待共享底层恢复绿色。
2. 第二次运行得到 `6 passed, 3 failed`：
   - 把“无持久化”直接与 3 次 `FULL` 提交比较，p95 回退 `14.964%`，超过门限；
   - SP-03/SP-06 因计划目录过长触发 Windows `WinError 206`。
3. 修正：
   - 第一轮先按 3 事务模型得到 `3.700%`，第 4 轮又改为统一四事务并得到 `7.146%`；
     第 5 轮逐行核对生产 `ReActDriver` 后确认两者都不是实际组合根：`ReactFinal` 没有
     turn fence，`ToolBatch` 则还有 boundary、goal、batch/attempt 与逐 call prepared。
     因此这两个百分比都只保留为被推翻的历史，不再支持性能结论；
   - 第 5 轮使用真实 `SqliteExecutionUnitOfWork + ReActDriver` 分别跑 Final、
     ToolBatch(1) 与 ToolBatch(3)。直接叠加两笔提交不通过；保持 FULL durability、改为串行
     长寿命 writer connection 后三条路径全部通过。这一优化因此成为 Task 3 的前置门；
   - pytest 临时根改为短路径 `F:\projects\deskpet\.sp`，真实 Store/Manager 路径通过。

## 4. 上游基线观察

- Spike/挑战期间 master 从 `7d44cb8a` 前进到 `2f5436ef`；第 5 轮 SP-02 使用的
  ReActDriver/UoW/test helper 在 `72ca2b16..2f5436ef` 间无后续差异，因此结果仍对应最终
  校准快照。
- 最终已提交 `WORKFLOW_SCHEMA_VERSION=12`、Capability 子 schema=`1`、
  SessionDB=`19`。
- 主工作树的 Platform/Harness/agent/`main.py` 等仍有未提交接线。builder 分支是
  patch-equivalent；UI/Godot 的前两个提交已有等价内容进入 master，但
  `git cherry master codex/capability-ui-godot` 仍把 HEAD `9d85a9a8` 标为 `+`，只能由
  Task 0 逐文件与测试对账后判断其内容是否已被 master 的后续 Godot wire 改动取代。
  OS runtime 分支仍有未合并提交。所以以上数字只是本轮快照，执行 Task 0 仍须在上游合并、
  工作树清理并跑绿后重新记录 `N/C/S`。

## 5. 进程与临时文件清理记录

| 运行 | 精确作用域 | 结果 |
|---|---|---|
| 首次收集失败 | root PID `27720`；命令含本 spike 测试绝对路径 | PID 已消失；作用域内 survivor=`0`；没有 pytest 临时目录。 |
| 第二次失败矩阵 | root PID `8932`；同一测试绝对路径 | PID 已消失；作用域内 survivor=`0`；长路径临时目录已删除。 |
| 校准运行 | root PID `31452`，child Python `15260`，conhost `9852` | 三个 PID 全部消失；当时先调整为 3 事务模型，Round 4 发现仍少算 pre-emit outcome 提交后已由下方四事务重跑取代。 |
| 最终运行 | root PID `4428`，child Python `32064`，conhost `29876` | 进程树峰值 private bytes=`866185216`（约 `826.06 MiB`）；退出后三个 PID 全部消失，作用域内 survivor=`0`，相应内存已由系统回收。 |
| 第 4 轮 SP-02 重跑 | root PID `30232`，child Python `8428`，conhost `31164` | 退出码 `0`；四事务模型回退 `7.146%`；进程树峰值 private bytes=`15233024`（约 `14.53 MiB`）；三个 PID 全部消失，作用域内 survivor=`0`。 |
| 第 5 轮 SP-02 首次真实路径运行 | launcher Python PID `3988`，child Python `5656`；命令含 `_sp02_round5.py` 绝对路径 | 外层采样命令超时后立即按绝对命令行复核；子树已自然退出，随后作用域 survivor=`0`。观测到 child private bytes 峰值约 `860864512`；没有按进程名广杀。 |
| 第 5 轮 SP-02 writer 优化重跑 | root PowerShell PID `29960`，launcher Python `32632`，child Python `25976`，conhost `15352` | 退出码 `0`；进程树峰值 private bytes=`868069376`（约 `827.86 MiB`）；三个后代 PID 全部消失，作用域 survivor=`0`，相应内存已由系统回收；`.sp3` 不存在。 |
| SP-10 dispatch-start 适配器 | root PID `5640`；MCP launcher/real Python `22904/4196`；local worker launcher/real Python `31560/21152`、`28396/29156`；conhost `29056`；全部命令行均含 `_sp10_dispatch_start.py` 绝对路径 | 退出码 `0`；进程树峰值 private bytes=`107483136`（约 `102.50 MiB`）；动态记录的全部 PID 消失，脚本内与外部 CIM 复核 survivor=`0`；临时根 `deskpet-sp10-p4on45ji` 已删除，内存由系统回收。 |
| SP-11 Windows Job 生命周期 | root PID `9524`；helper launcher/real Python `13976/8756`、`3620/30796`；suspended root launcher/real Python `23636/31464`、`18832/23620`；late child launcher/real Python `17472/21104`、`30448/23296` | 退出码 `0`；进程树峰值 private bytes=`74723328`（约 `71.26 MiB`）；正常 close 与 helper crash 两种 Job 路径全部 PID 消失，脚本内与外部 CIM 复核 survivor=`0`；临时根 `deskpet-sp11-ffvdc5qo` 已删除，内存由系统回收。 |

最终清理只匹配本测试绝对路径、短临时根和上述 PID，没有按 `python.exe` 或进程名广杀。
第 5 轮复核曾发现 `.sp\stale-links\...\G-broken` 断 junction；按已验证的仓库内绝对路径
精确移除后，`Test-Path .sp/.sp2/.sp3=False`，`git status` 不再有 broken-junction warning。
`F:\projects\deskpet\.sp`、`.sp2`、`.sp3`、SP-01～SP-11 测试源码、运行日志和 `__pycache__`
均在记录完成后删除；
计划目录只保留本结果文件。
