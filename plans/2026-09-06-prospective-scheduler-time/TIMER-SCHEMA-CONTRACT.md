# Prospective time scheduler：schema51与接线合同

2026-09-06。Host base b4f51024，分支feat/prospective-scheduler-time。下面SQL对应当前新SignalStore；由主统一落迁移/registry，scheduler owner不编辑schema或既有S5cStore。当前源码WIP，未测试。

## 精确DDL

目标 `backend/deskpet/memory/migrations/s5c/043_prospective_time_signals_v51.sql`。
版本推进由initializer管理，下面不写PRAGMA user_version。

```sql
CREATE TABLE prospective_timer_events (
    owner_key TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    phase TEXT NOT NULL CHECK(
        phase IN ('prepared','claimed','handed_off','applied','invalidated')
    ),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL CHECK(claim_epoch >= 0),
    lease_until REAL,
    body_json TEXT NOT NULL CHECK(json_valid(body_json)),
    body_hash TEXT NOT NULL CHECK(
        length(body_hash)=64 AND body_hash NOT GLOB '*[^0-9a-f]*'
    ),
    prior_hash TEXT NOT NULL CHECK(
        length(prior_hash)=64 AND prior_hash NOT GLOB '*[^0-9a-f]*'
    ),
    record_hash TEXT NOT NULL CHECK(
        length(record_hash)=64 AND record_hash NOT GLOB '*[^0-9a-f]*'
    ),
    PRIMARY KEY(owner_key, signal_id, sequence),
    CHECK(
        (phase='prepared' AND claim_owner IS NULL
         AND claim_epoch=0 AND lease_until IS NULL)
        OR
        (phase IN ('claimed','handed_off')
         AND claim_owner IS NOT NULL AND length(claim_owner)>0
         AND claim_epoch>0 AND lease_until IS NOT NULL AND lease_until>=0)
        OR
        (phase IN ('applied','invalidated')
         AND claim_owner IS NOT NULL AND length(claim_owner)>0
         AND claim_epoch>0 AND lease_until IS NULL)
    )
);

CREATE UNIQUE INDEX prospective_timer_prepared
ON prospective_timer_events(owner_key, signal_id)
WHERE phase='prepared';

CREATE UNIQUE INDEX prospective_timer_authority
ON prospective_timer_events(
    owner_key, json_extract(body_json,'$.authority.authority_id')
) WHERE phase='prepared';

CREATE UNIQUE INDEX prospective_timer_handoff_epoch
ON prospective_timer_events(owner_key, signal_id, claim_epoch)
WHERE phase='handed_off';

CREATE INDEX prospective_timer_recovery
ON prospective_timer_events(owner_key, phase, lease_until, signal_id);

CREATE TRIGGER prospective_timer_events_no_update
BEFORE UPDATE ON prospective_timer_events
BEGIN
    SELECT RAISE(ABORT, 'prospective_timer_events_append_only');
END;

CREATE TRIGGER prospective_timer_events_no_delete
BEFORE DELETE ON prospective_timer_events
BEGIN
    SELECT RAISE(ABORT, 'prospective_timer_events_append_only');
END;
```

## Python接口

定义位于 `backend/deskpet/memory/prospective_scheduler.py`：

```python
@dataclass(frozen=True)
class PreparedTimer:
    authority: ProspectiveSignalAuthority
    observation: dict

@dataclass(frozen=True)
class TimerClaim:
    signal_id: str
    owner: str
    epoch: int
    authority: ProspectiveSignalAuthority
    handed_off: bool
    # reference属性从authority生成ProspectiveSignalAuthorityRef

# 主实现生产source adapter，返回真实首grant；不得伪造旧revision注册。
async def prepare_due(*, now: float, limit: int) -> tuple[PreparedTimer, ...]: ...
async def registration_is_live(authority: ProspectiveSignalAuthority) -> bool: ...

# 新ProspectiveSignalStore
async def get_prepared(signal_id) -> PreparedTimer | None: ...
async def prepare(prepared: PreparedTimer) -> None: ...
async def claim(*, owner, now, lease_seconds) -> TimerClaim | None: ...
async def handoff(claim, *, now) -> TimerClaim: ...
async def assert_claim(claim, *, now): ...
async def invalidate(claim, *, now): ...
async def applied(claim, result, *, now): ...
async def resolve_prospective_signal_authority(reference): ...

# 新ProspectiveScheduler
async def tick(*, claim_owner: str, limit: int = 32) -> int: ...
```

构造SignalStore时调用 `s5c_timer_schema.validate_s5c_timer_state_db(path)`，事务内另核exact51；不只相信user_version。
主组合旧registration resolver与新timer resolver；未知authority才能按明确域分派，损坏/错ref不得吞错fallback。

## 首次time observation

用Host `deskpet.task_scope.protocol.canonical_hash`，不新增SDK hash算法。

```python
observation = {
    'schema_version': 1,
    'kind': 'host_time_observation',
    'owner_key': store.owner,
    'subject': intent.subject,
    'memory_id': intent.target_memory_id,
    'target_revision': intent.target_revision,
    'scheduler_registration_ref': intent.scheduler_registration_ref,
    'registration_revision': intent.registration_revision,
    'trigger_hash': intent.trigger_hash,
    'due_at': intent.trigger.trigger_at,
    'observed_at': intent.observed_at,
    'run_id': intent.run_id,
    'operation_id': intent.operation_id,
}
intent.signal_receipt_hash = canonical_hash(observation)
intent.signal_receipt_id = 'host:time-observation:' + canonical_hash(observation)
```

prepared body为 `authority/authority_hash/observation/observation_hash`；写入和读取均重新校验绑定。
owner_key = canonical_hash([principal.deployment_id, principal.household_id, principal.actor_id])，不含session。
signal_id = canonical_hash(['host:prospective-time/v1', owner_key, scheduler_registration_ref,
registration_revision, target_memory_id, target_revision, trigger_hash])，不含新now。
source先 `get_prepared(signal_id)` 复用实际首件，不更新observed_at/issued_at/expiry。
每行body_hash=canonical_hash(body)，首prior_hash为64个0；record_hash=canonical_hash([
owner_key,signal_id,sequence,phase,claim_owner,claim_epoch,lease_until,body,prior_hash])。

## 恢复及证明范围

- fresh：Host已ACK registration且无ACKed invalidation→TX内claim→再次registration_is_live→durable handed_off→public apply。
- handed_off可能已Memory提交：接管后先same-ref apply重放，即使head已TRIGGERED或authority过期；不被Host旧pending检查挡住。
- SDK apply原事务负责current revision/lifecycle/suppression/未消费authority expiry。Host registration_is_live不声称SDK全部可见性预检。
- handoff按epoch唯一；任一历史handoff使TimerClaim.handed_off=True，lease到期不等于未执行。
- claim收据在TX内部固定；stale epoch不能handoff/settle。异常/取消保留可能提交事实，不记not_sent。
- occurrence_key只用SDK实际inbox，不能Host重算；后续presentation/ack不由此timer冒充。

## 必要测试准备（尚未运行）

真实注册→time_due→public apply→inbox恰一；Memory提交后丢ACK→重开同ref replay（包括过期）；
fresh postclaim invalidation不调用apply；handoff后恢复不调用旧head预检；lease接管旧worker无法落回签；
get_prepared跨重开保留原observation/authority；篡改observation时间/target/hash拒绝。仅变动控制，不跑rich/401/旧绿。

## 固定源码交接边界

主schema模块为 `backend/deskpet/memory/s5c_timer_schema.py`，由主组合后提供；本分支不复制或实现该模块。
本次固定scheduler/store及三项控制测试，尚未执行；控制测试使用spy只验证顺序，不替代真实SQLite/SDK。
真实注册→到期→inbox、same-ref丢ACK重开、lease接管及schema验证需组合主schema/source后运行。
本片尚未完成完整scheduler产品验收，未启用main接线、未更新ARCH为完成。
