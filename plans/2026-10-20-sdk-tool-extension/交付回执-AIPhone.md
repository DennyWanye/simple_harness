# 交付回执：AIPhone 手机 Linux 本机任务编排（SDK 侧）

日期：2026-10-20。对应交接《SDK 侧交接：AIPhone 手机 Linux 本机任务编排》（基线 opt.195，`deb9e006c`）。
本回执只在私有渠道流转，不推送公开 GitHub。**没有手机真机：P 项全部未执行；A 项的 Linux 证据来自 Ubuntu 24.04 ARM64 虚拟机，只够进真机联调。**

## 1. 新候选身份

| 项目 | 值 |
|---|---|
| 版本 | `0.13.0.dev20260925+opt.200`（不覆盖 opt.195） |
| SDK 源码提交 | `25ce272e1062724233a94e03a56d7acbaf2d3e55` |
| wheel | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.200-py3-none-any.whl` |
| wheel SHA-256 | `f5e03d34df04060ddaedffb9a516549cfa1c2a4d85399ac55c8a37ecde28f2a5` |
| 候选清单 | 同目录 `...opt.200.candidate-manifest.json`，SHA-256 `795795f8a868247deaf82ffe5d697f961ad9976943031f519cb186f56873a37e` |
| Python | `>=3.11`（纯 Python wheel）；已测 3.12、3.13 |
| 必需依赖 | `deepseek-recipe==0.1.1`（非 Windows）、`httpx>=0.27,<1`、`pytest>=8.3,<9` |
| 验收门 | 发版门 167 条通过，wheel 内清单 `VALIDATED`；独立核验回执 `review-232415.json` |

## 2. 逐项状态

| 编号 | 状态 | 说明 |
|---|---|---|
| SDK-01 启动阻断 | 已修（opt.196 起） | 原因属实：触发器体内 `INSERT INTO temp.<表>`。改成不带库名，`ON main.<表>` 与查询里的限定名不动。用系统 SQLite 3.51.0 对真库结构装 142 个触发器通过；你们的 `repro_sqlite_trigger.py` 复现成立。原发放 wheel 已对齐：宿主钉版提交 `097bd443c` 里的 `backend/vendor/...opt.195...whl` SHA-256 是 `bbae818a77a977853bf77fd160c0379b52f2928e3da91dbb7950861c65c5c6b2`，与你们本地重建的同字节——你们复现的就是原制品，不是重建差异。opt.195 名下的 `VALIDATED` 只说明发版门在当时机器的 SQLite（3.53，放宽了该规则）上跑过，不覆盖 3.50/3.51。 |
| SDK-02 Linux 沙箱 | 已实现（opt.198），虚拟机证据 | 见第 3 节。手机真机 NOT_RUN。 |
| SDK-03 手机工具接入口 | 已实现：只读工具（opt.197）、操作连接器（opt.199，opt.200 修参数引用产物） | 见第 4 节。 |
| SDK-04 发布与宿主契约 | 本回执 + 接入说明 | 手机"预组装 site-packages 层"的发行方式没做，见第 6 节。 |
| SDK-05 大文件 / 二进制 | 没做 | `artifact_read` 正文上限 256 KB，二进制不给正文。首版只读文本报告可用。 |

## 3. Linux 隔离区（L-01～L-08）

环境：Lima 虚拟机，Ubuntu 24.04 ARM64，内核 6.8.0-147，glibc 2.39，bubblewrap 0.9.0。证据 `.local-test-evidence/2026-10-20/linux-bwrap/`。

| 编号 | 状态 |
|---|---|
| L-01 | 普通用户、`systemd-run --user -p NoNewPrivileges=yes` 下任务 COMPLETED；检查命令 `python -m agent_orchestrator.host.sandbox_doctor` 输出系统、内核、C 库、bwrap、用户命名空间设置、NoNewPrivs 与完整检查结果。Ubuntu 23.10+ 要装 bwrap 的 AppArmor 规则（`--apparmor-profile` 打印）。 |
| L-02 | 根目录空、只读挂系统位置与解释器白名单；可写只有本次副本与草稿目录；读机密、`/etc/shadow`、写出去、写 `/usr` 都失败（有测试）。 |
| L-03 | 新网络命名空间，TCP、路径与抽象 Unix 套接字、局域网地址都不通；不挂 `/run`、D-Bus、整个 home。宿主自己的模型请求照常出网。 |
| L-04 | CPU、输出、壁钟、进程数、内存有限制；内存与进程数是软限制（rlimit），没用 cgroup。 |
| L-05 | 正常结束、超时、取消、同组、setsid、两次 fork、完整守护化、不可转储五种逃逸写法都收干净；宿主被 SIGKILL 后隔离区随之结束。 |
| L-06 | 冷恢复按开机号 + 第一个进程启动时刻 + 命名空间认领；对不上（进程号复用、重启过）一个不碰；记录被改 → 不明、保留副本。 |
| L-07 | `run_tests`、`pytest:` 与核验代码走同一执行器，有执行回执。 |
| L-08 | `status()["sandbox"]` 有 `ok`、`kind`、没通过时的原因；没有可用隔离区时任务不能带 `pytest:` 要求（`local_tests_disabled`），其余要求照常。 |

无隔离区时能做什么：`file:` 要求的文件存在与内容核对、审阅员语义审阅照常；`pytest:` 要求与模型写的核验代码不能跑。

## 4. 手机工具接入口

公开导入：`agent_orchestrator.host`（`OrchestrationHost`、`HostSettings`、`DomainTool`、`ToolCallContext`、`SandboxOptions`、`ModelConfig`、`Principal`）与 `agent_orchestrator.runtime.connectors`（`OperationSpec`、`Receipt`、`ConnectorRejected`、`ConnectorTransportError`）。不需要私有导入，SDK 不依赖 AIPhone 的包，也不写死套接字路径。

**只读方法（`host.ping`、`device.get_status`、`app.list`）用 `DomainTool`：**

```python
from agent_orchestrator.host import DomainTool, ToolCallContext

async def device_status(arguments, context: ToolCallContext):
    receipt = await mobile_host.call("device.get_status", {})   # 你们的 Unix 套接字客户端
    verify(receipt)            # 保留现有的 producer version / source_commit / 方法绑定校验
    return {"status": receipt.result, "receipt_id": receipt.id}

tool = DomainTool(name="device_status",
                  schema={"type": "object", "description": "读手机当前状态",
                          "properties": {}, "additionalProperties": False},
                  invoke=device_status, timeout_seconds=10)
host = await OrchestrationHost.open(root, principal=..., model=..., tools=[tool])
```

- 同步函数在线程里跑；`async def` 在宿主事件循环上跑，里面不能阻塞（会卡住宿主和租约续期）。
- 每任务一把锁，同一任务内工具串行；超时与取消会取消协程，锁等物理调用真正结束才放。
- 结果必须是规范 JSON 对象，≤1 MB；事件里 32 KB 内原样。异常只记类型名。
- 可信上下文 `ToolCallContext`（`mission_id`、`attempt_id`、`call_id`、`principal_id`、`tenant_id`）由 SDK 给，模型参数不能伪造。
- 客户端与连接归应用所有，SDK 不关闭它们。

**之后的副作用操作（启动应用、拍照、发消息）用连接器**：建任务时写成 `action:<连接器>.<操作>:<目标>` 要求（可带参数）。SDK 负责：

- 审批：自动模式代批；标 `always_needs_approval` 的操作等人。
- 先记"已交接"再执行。
- 结果不明时按幂等键查询；连接器权威且封口时可证明"没生效"，换新键重做；否则等人裁定，不盲目重放。

不支持幂等或查询的连接器在注册时就被拒。完整说明：`docs/orchestration-host.md` 4a、4b 节，例子 `examples/orchestration-host/tools_readonly.py`、`tools_async.py`、`tools_effect.py`。

## 5. A 项验收

| 编号 | 状态 | 证据 |
|---|---|---|
| A-01 | PASS | 仓库外空项目只装 wheel：`demo.py --scripted`、三个工具例子退出码 0（macOS）；opt.198 wheel 在虚拟机 Python 3.12 / 3.13 任务 COMPLETED；opt.199 wheel 在虚拟机跑 `tools_effect.py` 两个任务 COMPLETED。 |
| A-02 | PASS | 真实 DeepSeek，生产验收门开启（没有改标签、没有绕过验收门），带连接器与回执丢失的一局 COMPLETED，见第 5a 节。 |
| A-03 | 虚拟机 PASS，真机 NOT_RUN | Linux 测试 20 项、两轮独立核验；macOS Seatbelt 没有回退。 |
| A-04 | PASS（测试适配器） | 只读工具注册、调用、事件、结果进证据。真实 Mobile Host 调用归 AIPhone。 |
| A-05 | SDK 既有测试覆盖，本候选未单独列跑 | 依赖任务、同幂等键重发、等人回答、版本冲突、预算耗尽、不明结果在 SDK 产品同形测试里；发版门 167 条通过。 |
| A-06 | PASS（脚本模型） | 回执丢失 → 封口证明 → 换键只生效一次；不封口 → 保持不明、不重交；取消时未结清动作列出。 |
| A-07 | PASS | wheel、清单、文档、例子一致；读产物前重算哈希，不符拒绝。 |
| P-01～P-03 | NOT_RUN | 没有手机。 |

## 5a. 真实模型一局

**opt.200 结果：PASS（2026-10-20 深夜～10-21）**。仓库外项目只装 opt.200 wheel，经本机转发（上游 chunfengapi.com，DeepSeek v4.1 flash），生产验收门开启，自动模式。任务：写三条省水办法到 NOTES.md，再经假"留言板"连接器发出（参数 `{"title": "省水三招", "body": {"$artifact": "NOTES.md"}}`，连接器的 `execute` 与 `lookup` 都是 async，声明权威且封口；第一次执行故意丢回执、请求不落地）。

- 任务 COMPLETED，停止原因 `verification_passed`；结清用量 490 462 token，尝试 1 次。
- 连接器日志：第 1 次执行回执丢失 → 查询该键为空（封口）→ 第 2 版动作换新键执行 → 发帖 1 条。留言板上只有一条，正文就是模型写的 NOTES.md（连接器按 `storage_uri` 读字节并核对了内容哈希）。
- 动作：`…:v1` FAILED（`proven_not_applied:rehandoff_refused:lookup_fenced_key`，交接 1 次）；`…:v2` SUCCEEDED（交接 1 次）。
- 事件（按出现顺序）：`OperationCompletionSpecApproved` 1、`OperationIntentSubmitted` 2、`ActionProposed` 2、`ApprovalRequested` 2、`OperationMaterialized` 2、`ApprovalGranted` 2（`approval_source=HOST_AUTO_PERMISSION`，执行者 `system` / `host-auto:demo-user`）、`ActionHandedOff` 2、`ActionOutcomeUnknown` 1、`ActionScopedReconciled` 1、`ActionNonapplicationFinalized` 1、`ActionSucceeded` 1、`OperationOutcomeReviewPrepared` 1、`OperationOutcomeAccepted` 1。
- 过程中规划器有几次没按格式写完（转发站线路当晚不稳），SDK 按格式重写或服务故障宽限处理，没有扣错次数。

这一局在 opt.199 上先发现了一个缺陷：参数引用产物的操作在建动作时永远被拒（建动作按文件发布的办法绑定参数）。已在 opt.200 修复，修复独立核验 PASS。

更早一次尝试（opt.199，10-20 晚）因线路故障没跑完：转发站 520、单次请求 90～220 秒；SDK 按服务故障宽限处理。

## 6. 已知限制与仍需 AIPhone 提供的事实

- **手机内核前提**：用户命名空间是否开、`kernel.unprivileged_userns_clone`、`max_user_namespaces`、LSM 是否挡 bwrap、bwrap 版本。若内核关了用户命名空间，bubblewrap 用不了，需要你们决定 setuid bwrap 或别的办法。SDK 不改系统设置。
- **cgroup**：没用；内存与进程数是软限制。
- **手机发行方式**：没做"构建机组装 ARM64 site-packages 层、手机只校验展开"的工具。wheel 是纯 Python；`deepseek-recipe 0.1.1` 有 Linux ARM64 wheel，虚拟机里装上能导入，真实计数在 Linux 上 NOT_RUN。
- **分词文件**：宿主首次启动自动下载并核对钉死的哈希，也可传 `tokenizer_path` 用预置文件（同样核对）。
- **与旧 Service / Memory SDK**：新宿主不共享旧执行库与记忆库，作为独立进程运行；同环境升级的兼容矩阵没做。
- **宿主生命周期**：单目录单所有者（实例锁）；`run_forever()` 与 `run_until_settled()` 不要同时用；`on_write`、`on_round` 是同步回调；重建后用事件游标续读；`notify` 至少一次，按事件 `seq` 去重。
- **模型**：只支持 DeepSeek（官方端点或声明过的兼容转发）。
- **大产物**：SDK-05 没做。
