# 交付回执：统一会话 + 委派 + 语音桥（SDK opt.221，给 AIPhone 与 AgentOS）

日期：2026-10-28（同日 Service SDK 0.3.20 正式发布后更新）。对应 AIPhone 2026-10-24 交接文（本目录《交接原文-AIPhone-2026-10-24.md》）与 AgentOS 2026-10-21 迁移交接。
接入说明统一在 `sdk/simple-harness-sdk/docs/orchestration-host.md`（下文用 §号引用）。
本文只写已经核实的事实。每项标"通过 / 阻断 / 部分 / 未执行"。

## 0. 一句话结论

| 接入方 | 现在能不能用 | 条件 |
|---|---|---|
| AgentOS（文字任务、快速聊天、Luna/Sol） | **能用**。opt.221 是 opt.213 的原地升级，安装方式不变 | 换轮子即可。opt.214～221 的修复都在里面 |
| AIPhone 文字主会话（`Session`：聊天、委派、结果 inbox、重启恢复） | **能用** | 只装一个轮子。接 §2d |
| AIPhone 语音（`[realtime]` 语音桥） | **能接入** | 要另装 Service SDK 0.3.20（已正式发布）。A09～A11 三个场景等中继修好 |

不能用一句"全部完成"概括。语音侧有三个场景阻断在 chinzy 中继，见第 6 节。

## 1. 制品身份

| 制品 | 值 |
|---|---|
| 编排 SDK 版本 | `0.13.0.dev20260925+opt.221` |
| 编排 SDK 源码提交 | `fb7980c7ecd60e954d733379620b4842e719079b`（Host 仓库 `sdk/simple-harness-sdk/`） |
| 编排 SDK 轮子 | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.221-py3-none-any.whl` |
| 编排 SDK 轮子 SHA-256 | `bed6ed01950767e3a4e1af99ce0b26e04e94a0671d0e7f0adb51dfa01064ff23` |
| 候选清单 | 同目录 `...opt.221.candidate-manifest.json`，SHA-256 `42f7fb6e0e8309376dc515f2b61ff0006e476851f933f15338871a5c6240c7ff` |
| Host 钉版提交 | `23a9731bf3a9e86f9bcabcd49a7fc6aca6349aa0`；origin main 现为 `15024682d89dc93b56c2d1a0eb081213ccef797b` |
| Service SDK 版本 | `0.3.20`，**2026-10-28 已正式发布**：https://github.com/DennyWanye/simple-harness-service-sdk/releases/tag/v0.3.20 |
| Service SDK 源码提交 | `f34caa3c82f1da73df0ea7bdf91f055a7c179f35`（tag `v0.3.20`；仓库 `DennyWanye/simple-harness-service-sdk`） |
| Service SDK 轮子 SHA-256 | `95a5ec79e6b12d7958f282e11c5e833f904f51505220a9d704698c14b2547bbb`（`simple_harness_service_sdk-0.3.20-py3-none-any.whl`，release 附件；10-27 前的私有候选 `f1e091bf…` 已作废） |
| Memory SDK（可选） | `0.5.2`，SHA-256 `deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e` |
| Python | `>=3.11`。编排 SDK 实测 3.12、3.13。语音桥新环境实测 3.13（macOS arm64） |
| 协议版本 | 会话事件 `schema_version=1`；语音事件同一 `schema_version=1`，额外带 `binding_id/generation` |
| 执行池身份 | opt.219～221 没有改任何工具说明、提示词、并发默认值 |

Service SDK 的全部制品在上面的 GitHub release 里：Service 轮子与源码包、编排 SDK opt.221 轮子、Memory 0.5.2 轮子、`compatibility-bom.json`、`SHA256SUMS`、三份平台锁文件（`realtime-lock-aiphone-py313-linux-aarch64.txt` 等）。
下载后先核对 `SHA256SUMS`。BOM 的 candidate 栏已改为 opt.221（轮子哈希 `bed6ed01…ff23`）。0.3.20 对编排 SDK 的要求是 `>=0.13.0.dev20260925`。

## 2. 安装

文字路径（AgentOS、AIPhone 文字）只装一个轮子：

```bash
pip install "backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.221-py3-none-any.whl"
```

语音路径加 `[realtime]`。两个轮子都从 GitHub release 下载后用 `--find-links` 解析（SDK 都不在 PyPI）。**不用 `--no-deps`**，满足 AIPhone 交接文第 7 节第 3 条：

```bash
uv pip install --no-index \
  --find-links <release附件下载目录> \
  'simple-harness-sdk[realtime]==0.13.0.dev20260925+opt.221'
uv pip check
```

公共依赖（httpx、websockets、prompt-toolkit、packaging、pytest、deepseek-recipe）先从公开索引装好，版本范围见 §2e 的安装块。
音频设备实现由接入方提供，桥不强制 `sounddevice`。

开发期规则：不保证旧数据目录兼容。升级后用新的数据目录。

## 3. 公开接口（指针）

| 能力 | 入口 | 说明位置 |
|---|---|---|
| 独立程序打开会话 | `await Session.open(root, principal, model, ...)` | §2d |
| 已运行 Host 上开会话（桌面、AgentOS 现有 Host） | `await host.open_session(session_id)` | §2d。复用同一数据根、任务库、预算账，不再建第二个 Host |
| 发文字 | `await session.send(text, key=...)` | 同键同文原样回放 |
| 事件流 | `session.events(after=seq)` | `text_delta / input_committed / turn_committed / delegated / task_progress / needs_response / result_ready / error` |
| 回答人工请求 | `session.respond(request_id, decision, version, ...)` | 选项对象用 `key` 字段（opt.218 之后 Host 侧已改为 `key`，不是 `id`） |
| 任务 | `session.tasks()` / `task(id)` / `cancel_task(id)` | 额度耗尽仍可读 |
| 结果 | `session.inbox()` / `session.ack(event_id)` | 只有人能 ack，模型没有 ack 命令 |
| 语音绑定 | `await session.attach_voice(audio_io, VoiceProfile(client_factory=...))` | §2e。同会话只允许一个绑定 |
| 主动播报 | `await session.announce(text)` → `handle.wait()` | 回执分开记 生成 / 传输 / 实际播放毫秒 |
| 打断 | `await binding.barge_in()` | 只清本地播放和在生成的回复，不取消任务 |
| 挂断 | `await binding.detach()` | 保留会话、任务、历史 |
| 音频协议 | `read_pcm() / write_pcm() / played_audio_ms()` | 输入 16 kHz s16le 单声道，输出 24 kHz |
| 快速聊天、逐字输出、看计划、接管、连接器 | `host.chat()` 等 | §2b、§2c、§4b～§4e（opt.213 已交付，不变） |

错误码表见 §2d 末（`idempotency_conflict / busy / turn_pending / permission_denied / stale_request / session_config_frozen / session_closed / budget_exhausted` 等）。

## 4. 从 opt.213 到 opt.221 改了什么（给 AgentOS）

| 版本 | 内容 | 对 AgentOS 的影响 |
|---|---|---|
| opt.214～216 | AgentOS 10-22 反馈四项 + 复验暴露三处：人答带选项标签、等执行池冷却算合法等待、信封漏收尾标签照收、动作结果按整个任务给 | 已在 10-22 回复里说明 |
| opt.217 | 新增 `Session` 文字主会话、委派、inbox | 新接口。`host.chat()`、`create_mission()` 等旧接口不变 |
| opt.218 | `host.open_session()`、语音桥 `[realtime]` | 不装 `[realtime]` 就没有语音依赖 |
| opt.219 | 被拒的计划提交留下半截修订行，之后同号提交永远撞唯一约束，任务白白耗尽规划次数；草案目标写错时给规划器完整期望值 | 修的是任务层。你们的任务也会少一类"规划次数耗尽"失败 |
| opt.220 | 执行池冷却期间被误判"没有可派发的工作"而任务失败；工具参数上限 64 KB → 1 MiB，越界回给模型改正，不再打死整轮 | 大参数工具调用不再整轮失败 |
| opt.221 | 模型流式调用挂死时打不断、回合不结束、收敛作业永远等。现在首字节等待 900 秒（思考池）、首字节后 180 秒无数据即超时；取消能中断阻塞读；收敛等待超时发可见事件 `TaskGraphConvergenceStalled` | 线路卡住时任务会在有限时间内失败，不再永远 ACTIVE |

opt.219～221 的真实模型验证全部用 DeepSeek（chunfengapi `deepseek-v4.1-flash`）。Luna/Sol 没有重跑。Sol 按字节上界计数的逻辑没有改动。

## 5. 验收现状（A01～A15）

| 编号 | 项目 | 状态 | 依据 |
|---|---|---|---|
| A01 | 普通文本/只读查询 | 通过 | 阶段 1（opt.217 仓库外）+ 真机点击（opt.221） |
| A02 | 文本复杂任务 | 通过 | 真机鼠标委派 PRD 到 COMPLETED，34 分钟，产物 56 KB，结果回主对话（opt.221，2026-10-27） |
| A03 | 幂等与竞态 | 通过 | 阶段 1 幂等矩阵与取消状态测试 |
| A04 | 创建/回传故障注入 | 通过 | 阶段 1 四个持久点退出恢复；真机 Cmd+Q 重启后结果仍在（opt.221） |
| A05 | 预算/权限/审批 | 通过 | 阶段 1；真机人答 1 次为真实按钮点击 |
| A06 | 真线路语音工具闭环 | 通过 | 正式轮子示例：`device_status`、`delegate_task` 两次真实工具调用，任务 COMPLETED（opt.218 + 0.3.20） |
| A07 | 三轮录音对话 | 通过 | 同上示例 3 段录音 3 次转写 |
| A08 | 语音委派→异步播报 | 通过 | live8：任务 COMPLETED 后结果实播 8320 ms（0.3.20） |
| A09 | 跨通道一致性 | **阻断** | 中继重连后 `tokenseller.error upstream_protocol_error`（fatal） |
| A10 | 打断分层 | **阻断** | 同上。局部片段（打断不取消任务）通过，整条未到达 |
| A11 | 断线/挂断/重启 | **阻断** | 同上。受控断线重绑片段通过，后继转写与新进程恢复未到达 |
| A12 | 超时/不支持/坏消息 | 部分 | 离线合同通过（工具目录不符拒绝就绪、不可重试终止不自动重连、流超时 opt.221）。没有单独的真线路专项 |
| A13 | 实际声音投递语义 | 通过 | 0.3.14 真实测量（生成结束时只播到 5254 ms，等到 25920 ms 才挂断）；0.3.20 示例分账一致。未专项重验 |
| A14 | 仓库外与兼容 | 部分 | 文字：opt.217 新环境通过。语音：opt.218 新环境通过。**opt.221 没有在仓库外重跑**，只在桌面端正式安装上跑过 |
| A15 | 质量与发布 | 部分 | 每版发布门通过（opt.221：451 + Host 180，12 变异全杀，独立评审 PASS）。**迁移与回退演练没做** |

四个桌面真机场景（问答、委派到完成、成功结果重启恢复、取消）全部通过（opt.221）。

## 6. 已知限制

- **A09～A11 阻断在中继。** 现象：重连或同号重绑后，中继返回 `tokenseller.error`，`code=upstream_protocol_error`，`fatal=true`，`retryable=false`。桥按合同不再自动重连，释放绑定，保留会话和任务。调用方可以明确重新 `attach_voice`。交接稿：《交接-给chinzy-重连后控制错误-2026-10-26.md》。**这份稿还没有发给 chinzy。**
- Service SDK 0.3.20 不提供请求与 response 的可靠关联。打断时若有未分配来源的请求，桥发 `voice_error(code=response_origin_ambiguous)`，要求挂断重绑，不猜来源。
- 通话期间文字 `send()` 排队到挂断后处理。进程退出后用原幂等键重试。
- 根任务终审：两位 DeepSeek 审阅员对 56 KB 文档连续 6 次回复不可用，系统转人答。原因未查。
- 中继不接受重复 `session.update`，也不接受只含 instructions 的部分更新。桥不用这两种更新。
- Linux aarch64 / Python 3.13（AIPhone 目标）只做了依赖锁解析，没有在手机上跑过。
- Luna 账号 2026-10-21 起 404，未验。NixOS 无实机。

## 7. 给 AIPhone 的接入步骤

1. 按第 2 节装 `simple-harness-sdk[realtime]==...opt.221` 和 Service SDK 0.3.20，`uv pip check` 通过。
2. 用 `Session.open()` 建文字主会话，接 UI。先跑 B01、B02（文字）。
3. 实现 `AudioIO` 三个方法。`played_audio_ms()` 必须是实际播放位置，不是已排队字节。
4. 实现 `client_factory(dispatcher)`：组装 Service SDK 客户端，凭据只从进程环境读。
5. `attach_voice()`，跑 B03～B05。B03、B04 要真人。
6. B06 重启：用同一 `session_id` 重开，`inbox()` 补读，`events(after=游标)` 续接。不自动开麦。
7. A09～A11 对应的手机场景（跨通道查询、说话打断后任务继续、断线重连）等 chinzy 修好中继后再验。

三项手机只读工具按 §4a 注册为 `DomainTool`。桥自动把它们列进语音工具目录。

## 8. 示例与证据位置

| 项目 | 位置 |
|---|---|
| 文字示例（委派→交付→重启补读） | 本机 `~/sh-ext-accept6/`（opt.217，README 在内；不进仓库，可交副本） |
| 语音示例（三轮录音→委派→播报→打断→挂断） | 本机 `.local-test-evidence/2026-10-25/unified-session/phase3/external/example_formal218.py` 与 `README-formal218.md`（Git 忽略） |
| 桌面真机验收记录 | 本目录《阶段3b-记录.md》"真机鼠标点击验收"各轮；证据 `.local-test-evidence/2026-10-26/session-real-click/`（本机） |
| 语音真线路记录 | 本目录《阶段3a-记录.md》第二十五、二十七节 |
| 独立评审 | 本目录《阶段1-独立评审.md》《阶段3a-独立评审.md》《阶段3b-独立评审.md》 |
| opt.219～221 缺陷与修复记录 | 本目录《阶段3b-记录.md》"SDK 规划层三处缺陷修复"起各节 |

证据目录都在本机，不上传。需要时按索引打包交付，先做凭据扫描。

## 9. 没有做的事

- 已完成：Host 内嵌编排 SDK 的 `uv.lock` 已换为 Service SDK 0.3.20 正式轮子，提交内容的 `source.url` 为 `https://github.com/DennyWanye/simple-harness-service-sdk/releases/download/v0.3.20/simple_harness_service_sdk-0.3.20-py3-none-any.whl`，轮子锁定 `sha256:95a5ec79e6b12d7958f282e11c5e833f904f51505220a9d704698c14b2547bbb`。安装及测试结果见[换源记录](记录-uvlock换指向release轮子-2026-10-28.md)。
- 把 chinzy 交接稿发出去。
- opt.221 轮子在仓库外新环境重跑两个示例。
- A01～A15 统一证据包（把本机忽略目录里的证据按索引打包）。
- 迁移/回退演练（A15 的一部分）。
- 根任务终审"无可用回复"原因调查。
- CHANGELOG 只写到 opt.218。opt.219～221 的条目以本文第 4 节为准。
