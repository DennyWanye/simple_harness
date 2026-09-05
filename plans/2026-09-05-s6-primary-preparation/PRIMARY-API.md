# Primary product API slice

2026-09-05，`feat/human-memory-primary-api`，base `29902ea4`。按原 P-PROJECT/P-PRIV/P-QUEUE（HM-AC1/7/8）授权实施 API 切片；原 AC 不变，S5c e2702006 暂停。本分支不改 main/runtime/fence/SDK/pin，完整 UI 与组合接线由主协调。

## 前端契约

沿用 `human_memory_request` 的 request_id/operation/request 与现有 success/error wrapper。先 `primary.open` 获取 durable primary_ref，不创建假 Session。

| operation | request | result |
|---|---|---|
| primary.state | `{}` | `{primary_ref,revision,current_run,queued_count,queued_count_truncated}` |
| primary.messages.page | `{primary_ref,cursor?:string,limit?:int}` | `{primary_ref,revision,items,next_cursor:string\|null}` |
| primary.messages.detail | `{primary_ref,message_ref,offset?:int,limit?:int}` | `{message_ref,text,offset,next_offset:int\|null,total_chars}` |
| queue.control | `{expected_run_ref,expected_generation,control,reason?}` | 既有 `{run_ref,generation,outcome,state,receipt_ref,...}` |

current_run 为 null 或 `{run_ref,generation,state,sdk_run_ref,execution_session_ref}`。后两字段来自实际 Host SDK binding 和注入的公开 runtime binding 记录，仅供 UI 事件关联；尚未绑定 SDK 时均为 null。不能当作执行授权或 Session selector。

items 为 `{message_ref,turn_ref,run_ref,delivery_key,role,text,has_more,total_chars}`。role 白名单 user/assistant/tool/artifact；delivery_key 用于入队 ACK 对齐。queued user 文本来自真实 Host admission；assistant 必须有 Host terminal receipt 与实际公开 SDK transcript 同 run/event/hash/state，以及 exact user anchor。过滤 system、hidden/reasoning、provider metadata，只取公开 text/tool/name/call_id/artifact 字段并脱敏。message_ref 绑定 primary/turn/transcript index/content hash。

page 默认20/max50条消息，每次最多扫描10个 turns；先取最新一页，页内按时间正序，next_cursor 向更旧内容推进。preview 最多1024 Unicode codepoints，detail 默认/max4096 codepoints；offset 也按 codepoints（JS 用 Array.from，非 UTF-16 units）。所有公开长文本可经 detail 拼回，不静默截断。这里只承诺条数/codepoint 上界，**不承诺64KiB响应上界**；公开 SDK callback 当前仍按单 Run 读取 transcript，单 Run 内容成本不等于 detail 响应大小。

keyset 使用 `(enqueue_sequence,message_index)`，无 history OFFSET；cursor 绑定 primary/revision。抑制过滤可产生 items=[] 且 next_cursor 非 null，UI 继续翻页，不能以空页当 EOF。detail 按 turn 主键读取。state 与 page 共用同一 revision 采样函数：四张既有 append-only 表的 MAX(rowid) 表尾和 exact current head，不加载所有历史 heads；水位为全局保守失效，其他 owner 的写入也可令 cursor stale，但内容始终 exact owner/primary 过滤。revision 为 opaque hash，不是递增整数，更不是 SDK privacy epoch。

state 沿既有 pending 索引最多读取101条 queued，只对前100条逐项查当前 source policy；queued_count_truncated=true 表示数字为已见可见计数下界，UI 显示“至少 N”/N+。不新增计数 ledger，也不对全队列 await policy。

每次 page/detail/state 均重查当前 subject 和实际 Host source evidence suppression；SDK suppression 变化无需 Host revision 变化，旧 cursor/ref 也须过滤。缺 resolver、错误/非 typed resolution 均 fail closed。终态观察按 S6 已约定 `uuid5(NAMESPACE_URL, "primary-runtime:" + sdk_run_id)` 的 evidence 主键查询并核对 source_ref/run/owner/terminal，避免扫描全部 evidence。pre-S6 无该 observation 的记录仍受输入 evidence 过滤。

**隐私边界**：resolve_suppression 只检查所传 IDs，不展开 memory/evidence/entity lineage。本切片只证明 Host 主体、用户输入及终态观察 source suppression；不声称解决所有 memory-derived 历史，不重放旧 authorize_context_use 或旧 receipt 代替当前授权。来源谱系扩展留后续。UI 不持久缓存，privacy/change 事件到达即清页与详情，拒收已失效读取。

Exact control 任一 target 字段出现即要求两个都有效；generation 严格正整数，禁止 null/bool/字符串/半请求回退。直接把客户端 exact run/generation 交既有 request_control，不能先查 current 再覆盖。迟到的 A control 可返回 A already_terminal，绝不影响新 B；仅两字段都没有的内部旧请求保留兼容。新 UI 必须 exact。

## 组合 kwargs 与交叉归属

HumanMemoryHostServiceFactory 存储并向每次 bind 传入三个 kwargs，均支持 sync/async：

- `settled_run_reader(sdk_run_id, *, current_text) -> (SdkRunTerminalEvidence, tuple[public_message,...])`，接 `ProductSdkRuntimeStack.read_settled_primary_run`。Host 验 exact binding/terminal/source 后再验 reader event ID/hash/state 与 user anchor。runtime reader 若先丢 artifact blocks，API 无法恢复；须在 reader 保留公开 artifact_ref/name/mime_type/uri 白名单。
- `suppression_resolver(SuppressionCandidate, OrdinaryMemoryPurpose.READ) -> SuppressionResolution`，接当前 `Memory manager.backend.resolve_suppression`，不可 missing-policy allow。
- `run_binding_reader(sdk_run_id) -> SdkRunBindingV1 | binding_record`，记录须通过 SdkRunBindingV1.from_record 验 fingerprint/run_id。若用 `read_closure_run_facts`，提取 `binding_record`，不可直接返回外层 facts。

三个类型已直接同步 Carver，其回复已在隔离树 main 注入；本轮也只读核对了该 factory 调用及 binding_record 提取。组合树运行验证仍归主/Carver，本分支不声称生产接线验收完成。合并 service/API 必须保留 Carver request_fence propagation。Popper 已同步 *_ref/queued_count_truncated/页边界；state/page revision 跨请求不一致时刷新。permission pending 重建、typed runtime event emission、目录 pending replay/ACK 仍归主；现有 project_directory_response_applied 是日志而非 WS ACK，UI 仅显示提交待确认。

## 验证边界

真实 Host SQLite + 公共 API enqueue/control，真实已安装 Memory SQLite backend suppression；runtime transcript/binding 用明确注入的 typed fixtures，不计生产 SDK runtime/UI/provider 验证。重点：queued 文本、exact owner/primary、keyset/Unicode完整性、subject/source变化后旧ref拒读、白名单 canaries、terminal/anchor不匹配拒读、缺 reader/policy 拒读、旧 A 不控制新 B、bounded queue/window。原始失败和重测保存在 ignored `.local-test-evidence/2026-09-05/primary-api/`，不删除失败。

最终命令（本 worktree，main venv 解释器，PYTHONPATH 指向本树 backend）：

```sh
PYTHONPATH="$PWD/backend" /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/memory/test_primary_read_api.py backend/tests/memory/test_human_memory_service.py backend/tests/memory/test_human_memory_api_and_fence.py backend/tests/memory/test_primary_turn_admission.py -q
```

结果 **40 passed / 14.30s**（新增24、既有16）。Ruff 新模块/测试 E,F,I 与共享文件 E4,E7,E9,F,I 通过；git diff --check 通过。`final.log` SHA-256 `0e3fb6281cf377d2b8af9c110c4da2552986d790e02f0d4606bae0f3b7869aab`；`SHA256SUMS.json` SHA-256 `d3abee0e2b7d18aa2031a2b247d639f6446978bdfcaeaad8db4760feec419588`。本机模式扫描无 credential-shaped 命中，不等同于任意 secret 不存在证明。

保留的失败类别：首次 red 为缺注入接口；first.log 为过长输入 fixture 超出既有16KiB admission预算；expanded.log 为 fixture evidence_ref 与真实入队来源不一致；focused-final.log 为命令误指 execution/ 下的 admission测试（0 tests）；ruff-initial 为长SQL排版。对应重测均另存，不覆盖原失败。不将 fixtures/观察命令错误归为生产 provider 失败，也不借此宣称 UI/组合链完成。
