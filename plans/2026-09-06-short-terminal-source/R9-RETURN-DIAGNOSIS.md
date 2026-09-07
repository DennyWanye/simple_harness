# r9 短期检索有候选、工具无片段：只读定位

2026-09-06。Host 事实基线 fa7580b0，H077/M617；主 r9 记录后继2387f9af仅文档。本文仅只读诊断和最小源码修改建议，未改业务源码、原库、预算或运行测试/重检索/embedding。Hegel 占测试槽，本叶没有尝试占锁。

## 确定根因

**本次空片段发生在 SDK typed 候选筛选/预算阶段，尚未进入 Host selected-source proof 或出站过滤。** 内部 short audit 的 used/三个候选不能直接称为最终 typed 命中。

精确 Run：`product-sdk-be8d36dab26c765828fbc19ae27c4cbfccd4f6a7f2544852e538f004d276f331`。

| 持久事实 | 观察 |
|---|---|
| 第二次 short audit，created_at=1788686437.763017 | generation_state=used；eligible3、fts1、vector3；degradation=None。内部耗时337.007ms，剩余deadline926ms；privacy/suppression/classification/time filtered均0 |
| 同次 typed request `typed-recall-request-1ff83af4aca39d79fa85b1abcff711b9f86d5acd82b2607756fec1dd7ce16ba2` | context.allowed_retrieval_modes 和 plan.retrieval_modes 均只有 full_text；memory_types=[]、include_short_horizon=true；预算16384 bytes/2048 tokens/8 items/1000ms |
| typed result `recall-result:5119049f9a3ef9c742089b288bea2266` | items=[]；reason_codes=[recall_budget_exhausted]；truncated=true；对应 decision selected_items=[]、outcome=no_recall |
| 成功 effect `effect-c0d58ee8196150617b044e918d60e90b0c5fb26d8a2e6d946e91743e44b310a2` | 原 result_json 的 value 确为 fragments=[]、degradation_codes=[]、truncated=true；Host route ledger accepted，typed carrier.fragments=[] |
| 同 Run 的 provider-turn:3 | 已 handed_off_at=1788686438.27338，state=succeeded；持久 request_json 工具上下文携带同样的空 fragments/truncated=true，不是仅看 UI 的间接推断 |

更晚的 `recall-result:df2293bf28096ae61435047779d63a94` 属 analysis-recall、不同 Run，不能误拿它与前述 foreground 工具对应。

## 为什么有一个很短的相关片段仍返回空

| 原组 | 完整文本尺寸 | 原算法单项预算成本 | 实际检索 lane |
|---|---:|---:|---|
| seq2：原始茶偏好 | 95字符/249 UTF-8 bytes | provider_budget_value 列表编码359 bytes；保守token成本205，可装入原预算 | vector第一名，score约0.536413；本次fts_lane不含它 |
| seq3：完整长工具消息组 | 8680字符/9806 UTF-8 bytes | 列表编码10422 bytes；保守token成本9296，超过2048 | 唯一FTS候选；vector第二名 |

成本是按既有 `core/recall.py::apply_budget` 的 canonical_json 与 max(字符数, ceil(bytes/3)) 算术计算，不是一次新检索，也不是 Provider 实测token用量。没有输出原消息正文。

`sqlite_v5.py::_collect_typed_recall_short_candidates`（:3505附近）只从 plan 请求过的检索 lane 建 candidate；未请求 vector，就丢弃内部 vector lane。因此 seq2 虽有真实 vector 命中且尺寸合适，仍不进入 typed candidates；唯一留下的 seq3 超预算，最终空。`apply_budget` 会 continue 超大项，未存在“首项超大阻断后来可装入项”的实现错误；此次后来根本没有合法请求 lane 的小候选。

Host `human_memory_v7.py:385` 构造 RecallContext 时固定 `(RecallRetrievalMode.FULL_TEXT,)`，后续 RecallPlan 使用该 allowed_retrieval_modes。这是此次实际接线矛盾。不是需要加大预算、跳过完整组、修改 suppression 或重新下载 WeMM。

## 已排除的本次分支与仍需验证的范围

`human_memory_v7.py` explicit_selection 分支从 result.items 生成 bindings；本次 items为空，直接得到 SelectedShortSources(())，不进入 resolver 异常吞为None的分支。`project_recall_fragments` 对空 items 无从产生片段；`context_route.py:406/425` 原样放入 extras；SDK effect 和下一真实 Provider request 均与之相符。本次不应先修改 selected-source异常处理、privacy/proof门或 typed carrier。

拟定最小下一源码叶：只在显式请求短期时，为 RecallContext/RecallPlan 一致加入现有 `VECTOR` 检索模式，保留 `FULL_TEXT`、统一预算和所有资格/proof检查；不改变未请求短期的长期模式。需要针对混合长短期确认现有 SDK 支持边界，不能用 undocumented fallback 伪造一个 lane。此处是建议，本文未写入业务代码。

后继必要验证应在静止三库的 SQLite backup 衍生副本或真实公共小fixture上：精确请求模式、FTS超大但vector小项可选、原2048预算保持、完整source proof/typed carrier/最终工具结果绑定。只要 proof 另有实际失败就继续明确定位，不能从本诊断推断下一层必通过；当前没有修改后运行结论，也未再发一次 native query。

## 只读证据位置

使用用户指定的 primary-m0615/venv/bin/python，SQLite `mode=ro`、query_only=ON、BEGIN，仅读以下原静止库；没有执行 public recall（它会追加审计）或修改数据库：

- `/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/native075616/primary-ui-lm2syaq2/userdata/data/human_memory_v7.db`：typed_recall_requests/results/decisions、short_horizon_audit/chunks。
- 同目录 `state.db`：context_route_tool_invocations/decisions。
- 同目录 `simple-harness-sdk/execution-v6.sqlite3`：execution_effects/provider_invocations。此为专家只读诊断，不将 SDK 私有 SQL 引入产品 consumer。
- UI/日志：`/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/native077617/primary-ui-nqkra6y1/` 中 native.log、short-result-missing.ax.txt；没有新增截图/模型调用。
