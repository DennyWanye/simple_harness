# S3 公共接缝对接需求（不在本分支修改 SDK）

2026-09-05。本轮继续执行原401/14攻击/阈值，以下为主执行者对接的最小建议签名；SDK 最终命名可由其公共 API 纪律确定，但语义不可缺失。

```python
async def build_human_memory_v7(db_path, *, clock: Callable[[], float] | None = None, ...): ...
# Public builder forwards clock to existing SQLiteHumanMemoryBackend(now=clock).
```

主执行者核实 backend 已有可信 now、相关路径使用 self._now；本轮只需 public builder 透传，不要求新大 port。默认真实时钟，已有 now 参数兼容规则保持。deadline monotonic seam 尚未声称解决；不改SDK源码。

```python
@dataclass(frozen=True)
class TypedRecallRejectionV1:
    schema_version: int
    invocation_id: str
    request_hash: str | None
    context_hash: str | None
    plan_hash: str | None
    stage: str
    reason: str
    candidate_query_started: bool
    candidate_query_count: int
# Existing public exception: exc.rejection_receipt: TypedRecallRejectionV1 | None
```

stage 至少区分 protocol/ownership/narrowing/idempotency/eligibility/deadline，稳定一对一 reason 保留。
记录真实本次调用的候选查询计数（定义保持原Task5；不是按预期合成的0，也不能以写状态不变推导未读）。解析前不能 canonical 的 hash 取null；不得猜造 request。不可含 memory payload、证据正文或未授权身份内容。若用 audit ref 返回，须提供身份受限的公开查询入口取得同等字段。

protocol_version 原攻击仍需公开 versioned request admission（harness_protocol / memory_protocol）。这不是为0.7.2要求新版本；原14攻击中的request版本目前只能由后台常量生成，不能改用另一个 schema/parser 冒充。保留BLOCKED并推进其余路径。

本轮更正 §3：`E(d,x)=H(C({domain:d,payload:x}))`。先前 NUL 写法是提案事实性错误，用户已明确指示自主纠正；全部SDK domain/401/14攻击/数值保持，§4验证侧state域NUL不变。不改生产hash迎合提案、不新增批准流程。

## Narrow implementation handoff (2026-09-05)

Clock-only 0.6.4 need not wait for the other ports. `execute_typed_recall` currently checks
ownership, then `plan.validate_narrowing`, then `_admit_typed_recall_request`, all before
candidate collection. The exact existing rejections required by the 13 executable attacks:

| stage | exception | exact existing message |
|---|---|---|
| ownership | MemoryOwnershipConflict | typed_recall_subject_not_owned |
| narrowing | ValueError | RecallPlan context_hash differs |
| idempotency | MemoryIdempotencyConflict | IDEMPOTENCY_CONFLICT |

Minimal suggestion: immutable `exc.rejection_receipt` carrying the V1 fields above, attached
by the actual per-invocation execution scope, without changing exception class/message.
The narrower `stage/reason/candidate_query_started/candidate_query_count` fields alone need
an invocation/request binding to prevent cross-call witness substitution; retain the V1 binding
fields. No global last-error slot, inferred count from exception type, or payload-bearing audit.

For the request-version attack, optional public keyword `harness_protocol: int = 4` on
Manager and backend is sufficient if strict integer admission rejects unsupported values before
all durable writes and candidate reads. Existing calls and public exports remain compatible.
Accepted requests keep the exact existing v4 request hash (no new hash algorithm/domain).
Freeze protocol rejection stage/reason before that new candidate executes. This is a proposed
Memory admission seam; do not change Harness DTO exports or substitute Context schema_version.
