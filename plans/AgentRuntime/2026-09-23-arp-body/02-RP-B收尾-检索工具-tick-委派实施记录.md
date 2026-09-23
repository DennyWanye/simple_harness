# ARP-EXEC-1.1.1 主体施工 RP-B 收尾：模型侧检索工具、tick 卸载、委派走创建服务

日期：2026-09-23。分支 `arp-1.1.1`，上一片 RP-B 提交 `35a4b67a`；本片提交 `325d2606`。本片对应 RP-B 记录 §5 的前三项。

## 1. 交付物

| 模块 | 内容 | 规格条款 |
|---|---|---|
| `arp/retriever.py`（新） | `SessionRetriever.search / read`：模型与 Host 读同一 Session 历史的入口。search 无 cursor 时冻结当前 Session 的索引快照（highwater、完整 expected 组集，由协议组捕获重算）、按 query 身份调一次 embedding（`embedding_call`，持久回执）、`start_frozen` 建冻结查询；有 cursor 时 `resume_frozen`，且 query/limit/max_bytes 必须与原查询相同，否则 `CURSOR_REQUEST_MISMATCH`。read 走 `read_history`（精确 Journal、UTF-8 边界切片）。purpose 只允许 MODEL_SEARCH/MANAGEMENT_SEARCH 与 MODEL_READ/MANAGEMENT_READ；内部 CONTEXT_RECALL 不向模型暴露；请求不带 Session id | INTERFACES §1、HOST-DTOS §4、CONTEXT-SEARCH C3/C6 |
| `arp/tools.py`（新） | `ArpSessionHistoryTools`：与 legacy 同名的 `session_history_search / session_history_read` 工具，参数即 SearchRequest / HistoryReadRequest（query、cursor、limit≤32、max_bytes≤65536；seq_from、seq_to、cursor、max_bytes）；从 Run 解析 Agent，caller 为该 Agent 主体；ArpError → 具名工具失败 `session_history_<code>` | model-tools.json |
| `arp/search.py` 改 | 查询体新增 `page_items / max_bytes`（进 options_hash）；RESULTS 页按 limit 取项、按精确 canonical 字节数整项收缩，一项都放不下报 `ITEM_TOO_LARGE`；页 schema 按 purpose 选 ContextSearchPage / SearchPage / ManagementSearchPage；`resume_frozen(expect=)` 核对 cursor 所属查询 | C3 尾段 |
| `agents/runtime.py` 改 | `build_agent_runtime(session_tools_factory=)` 第三个工厂钩子；`_index_pump` 每轮调用 `arp.tick()`（INDEX 作业与未终态召回由后台泵推进，embedding 在任何锁外） | J7 |
| `arp/runtime.py` 改 | 装配 `ArpSessionHistoryTools` + `SessionRetriever`（`state.retriever`） | |
| `arp/creation.py` 改 | `create(agent_id=, kernel_start=)`：委派可保留原子 Agent 身份与原 kernel 子启动作为延期 kernel 步骤 | BW01 |
| `tools/delegate.py` 改 | ARP 模式下子 Agent 经 `NativeCreationService.create`（creation_key `delegation:<parent>:<id>`，caller 主体为父 Agent，命令回执绑定委派 intent hash），原委派 ticket + `kernel.children.launch` 作为 `kernel_start` 回调；legacy 路径不变 | BW01、§3 |

新增测试：`test_arp_history_tools.py`（3 项：词法/混合两种模式下模型 search 从 PROGRESS 页翻到 RESULTS 页命中早期暗号、rank 连续、字节上限、cursor 换 limit 拒绝、同 cursor 重放同页、不产生内部召回行；read 分页覆盖全部 seq、越界拒绝）、`test_arp_delegate.py`（1 项：委派子 Agent 有 creation intent、ACTIVE Session、父子 Run 链接保留、子 Agent 请求有冻结 manifest、委派结算）。

## 2. 实现决定

1. **模型工具名不变**：Agent 配置里的 `session_history_search / session_history_read` 继续有效，legacy 装配（无 ARP）仍用旧实现。
2. **tick 与 prepare 并存**：后台泵每轮 `arp.tick()`；prepare 内仍对本 Session 的到期 INDEX 作业做一次有界追赶（`process_due(session_id=)`），保证 coverage 诚实而不是等下一轮。召回 Progress 由 tick 推进只在崩溃恢复路径生效；正常路径仍在 prepare 内联到终态（RP-B 记录 §2.2 的决定不变）。
3. **搜索身份含发起时刻**：无 cursor 的同一 query 再发是新快照（C3 第 10 条），query_id 由 purpose、owner scope、query hash、发起毫秒与请求体派生。
4. **委派子 Agent 的 caller**：主体 `principal:agent:<parent>`，owner_contract 为激活 profile 的 owner-mode pin，命令回执绑定委派 intent hash；模型不构造 TrustedCaller，由 delegate 工具在服务端派生。

## 3. 测试与回归

- ARP 定向：`tests/agents/arp` → **261 passed**（核验后补 1 项）。
- legacy `tests/agents`（排除 arp）16 failed / 172 passed / 6 skipped；`tests/execution` 17 failed / 144 passed；均与基线相同。

## 4. 独立核验

一轮，opus 5.5 只读审阅，只报阻断级。结论：1 条阻断，已复现并修复；委派创建与 tick 挂泵未发现阻断。

| 级别 | 审阅发现（大白话） | 处置 |
|---|---|---|
| 阻断 | cursor 续用时比对"用途"那一项，请求侧填的是 cursor 自己存的用途，等于自己比自己，永远通过；管理通道的 cursor 能拿到模型通道续用 | `resume_frozen` 改为用本次访问的 purpose 比对；新增测试：管理↔模型双向互用、同用途换 caller，均 `CURSOR_SCOPE_MISMATCH` |
| 非阻断（提及） | 后台泵对 `tick()` 异常直接吞掉不留痕 | 保留（泵必须活着；召回行本身已由 `resume` 记 BLOCKED 与审计回执），后续接日志端口时补 |

处置后 ARP 定向 261 passed。

## 5. 未做

- Host 管理 verbs `agent_session_history_search / read` 的 DTO 解码与固定 caller 委托（Host 侧 handler，RP-D）；SDK 侧 `SessionRetriever` 已支持 MANAGEMENT_* purpose。
- 召回 Progress 完全交 tick、prepare 不等待（需改 prepare 的同步模型）。
- 其余见 RP-B 记录 §5。
