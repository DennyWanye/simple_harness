# 事件 P 决策记录：本轮输入（`:input-v1:`）可见性守卫与 CLAIMED→RUNNING 竞态

工作树 `worktree-input-guard-race`（分支 `worktree-input-guard-race`，基线 383a1c84）。未并入 main。

## 1. 现象与证据

C12 语料复核（`.local-test-evidence/2026-09-08/corpus-c12/run-02.review-report.md`）中 C12-19 判
`EXECUTION_FAILED` 且没有任何助手回合，同批 15 个兄弟用例正常。账本时序：

- C12-19：`provider_attempt.handed_off` 于 …151.637，`foreground_run_transitions` 的
  `CLAIMED→RUNNING` 于 …151.645——**交接早于状态落库 7.5 ms**。
- 其余 15 例：RUNNING 比交接早 43–77 ms。

即 ~1/16 的回合会踩中这个窗口。失败原因被 `primary_dependencies.py` 的 `private_cause`
抹掉，外部只看到一个不可诊断的执行失败。

## 2. 根因（同一模块内部自相矛盾）

`backend/deskpet/execution/primary_dependencies.py:437-439`（原行号）在 disclosure 的
`authority_ref` 含 `:input-v1:` 时调用 `claim_stamp`；而
`backend/deskpet/memory/current_input_visibility.py`：

- `:23` 的披露查询把 `'CLAIMED'` 算作**在世认领**；
- `:69-75` 的 `claim_stamp` 状态白名单**不含 `CLAIMED`** → 抛
  `host_current_input_physical_claim_unavailable`。

同一模块两套状态词汇。物理交接恰恰发生在 `CLAIMED`：

- `foreground_queue.py:86-87` `_EFFECT_BOUNDARY_ALLOWED_STATES[SDK_START] == {"CLAIMED"}`，
  即**唯一一次 SDK 启动效果只在 CLAIMED 被准入**；
- `foreground_runtime.py:1211-1218` `authorize_effect(SDK_START)` → `:1226` `ingress.start`
  → `:1322-1330` start observation → `:1332-1339` `record_sdk_started`（CLAIMED→RUNNING）；
- `foreground_queue.py:2490-2501`：写 RUNNING 前必须已有 `RETURNED`/`QUERY_FOUND` 的 start
  observation，否则 `foreground_execution_start_observation_missing`。

因此**被守卫的那一次物理请求，按契约就发生在 CLAIMED 窗口内**，白名单排除 CLAIMED 等于
拒绝它本该放行的那一次交接。

`:59` 的 `claim_changed_during_check`（以及 `primary_dependencies` 的
`primary_input_claim_changed_during_check`）把 `current_state` 纳入八元组全等比较，同一个
窗口里还有第二重竞态：G1 读到 CLAIMED、慢 Memory 检查期间 RUNNING 落库、G2 读到 RUNNING →
误判为「认领被换掉」。

## 3. 备选方案与裁决（用户授权自决，不再询问）

**方案 A（否决）：把 `record_sdk_started` 提到 `ingress.start` 之前。**
违反两条硬契约：RUNNING 必须先有 start observation（`foreground_queue.py:2490-2501`），且
`authorize_effect(SDK_START)` 只在 CLAIMED 准入——提前落 RUNNING 会让交接本身被拒。等于用
伪造的 RUNNING 换取放行，正是 `plans/2026-09-05-s6-primary-preparation/CREATE-NEW-BINDING.md:101-102`
明令禁止的「提前伪造 RUNNING / 绕过 SDK_START admission」。

**方案 B（否决）：照抄首 tool 竞态的 Event 屏障，在守卫里等 RUNNING 持久化。**
`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md:1100` 记录首 tool 竞态的处理是「等真实 Host RUNNING
持久化，不放宽 CLAIMED」。但那条守的是 **TOOL 边界**——TOOL 的准入状态本来就是 RUNNING，
CLAIMED 期间不得产生外部效果，所以只能等。本事件守的是 **SDK_START 边界本身**，它的准入
状态就是 CLAIMED；在这里等 RUNNING 是拿错了不变量去卡自己，还会给每个首请求加上一次有界
等待与新的悬挂风险（`ingress.start` 未返回时会真的死等到超时）。

**方案 C（采纳）：统一词汇 + 只容忍这一次启动线性化。**

1. `current_input_visibility.py` 抽出唯一的 `LIVE_CLAIM_STATES`（含 `CLAIMED`），披露 SQL 与
   `claim_stamp` 白名单同源，从结构上不可能再分叉。
2. 新增 `same_physical_claim(original, final)`：认领身份（run/subject/turn/primary/owner/
   generation/sdk_run_id）必须完全一致；状态**只允许 `CLAIMED→RUNNING` 一种推进**，因为那
   就是 Host 在记录*这一次*交接的开始。其余任何移动（PAUSE/STOP/CANCEL_REQUESTED、换代、
   重新认领、终态）仍然判定为认领改变。
3. `check_primary_input_visibility` 的收尾复核与 `check_runtime_dependencies` 的 G2 复核共用
   该判据；G1→G2 依然是**拒绝而非替换**，`CONTRACT.md:23` 的口径不变。

**同一缺陷的第三、四份拷贝（独立复核发现，同批修掉）。**
`backend/deskpet/memory/current_input_authority.py:37`（第三份状态字面量）与 `:72-75`（第四份
八元组全等比较）就**嵌在被放宽的这个窗口里面**：
`check_runtime_dependencies` → `policy.check_dependencies` → `_primary_history_visibility_checker`
→ `check_primary_input_visibility` → SDK `check_current_input_visibility` →
`port.resolve_current_input`。该函数两次 head 读之间是本守卫最长的一段真实 IO
（`read_current_input_source` + `resolve_history_source` + `resolve_current_disclosure`），
RUNNING 极易落在这里 → `return None` → `current_input_authority_unverifiable` → 本轮输入自身的
evidence 不可见 → `primary_dependencies_not_visible` → 同样的 `EXECUTION_FAILED`，只是原因码更粗。
更糟的是 `:47-48` 明确把 `run["sdk_run_id"] is None`（即 `bind_sdk_run` 之前，只可能在 CLAIMED）
当作合法，`:74` 却又把 NULL→已绑定判成认领改变——这个窗口里有**两处**未被容忍的合法 Host 写入。
已改为共用 `LIVE_CLAIM_STATES` 与 `same_physical_claim`，并为这个唯一容许未绑定 head 的读者加上
显式的 `may_bind_sdk_run_id=`（默认关闭：其他调用者的 `sdk_run_id` 任何移动仍然判改变）。

**两处收紧（同批）。** ①`claim_stamp` 现在显式拒绝 `sdk_run_id IS NULL`——CLAIMED 进入白名单后
那正是 `bind_sdk_run` 之前的窗口，不能依赖 `None != None` 为假来兜底。②`same_physical_claim`
比较的是**端点而非路径**：同代内 `CLAIMED → PAUSE_REQUESTED → RUNNING`（pause 被提起又被拒绝）
端点同样落在 `(CLAIMED, RUNNING)`，注释与 docstring 已按实际行为写明，不再声称比代码更强的不变量。

**为什么这不是放宽守卫。** `claim_stamp` 仍要求 `head.sdk_run_id == sdk_run_id`，而 head 的
`sdk_run_id` 只可能由 `bind_sdk_run` 写入，后者没有匹配的
`foreground_execution_start_intents` 行就拒绝。所以「CLAIMED 且已绑定本次 SDK Run」本身就是
Host 已准入本次交接的持久凭据，不存在「预认领读取拿到许可」的口子——那条路径由
`check_primary_input_visibility` 的 `matched` 为空分支照旧走普通 SDK 门。披露语义（谁能看到
什么）一个字未改。

## 4. 可诊断性

`primary_dependencies.py` 新增 `rejection_reason(exc)`，在被抹掉的路径上给出**稳定、无载荷**
的原因码，作为 `PrimaryHistoryDisclosureRejected(public_message=…)`（沿用
`sdk_adapters/prospective_request_guard.py:58` 的既有先例）：

- `CurrentInputSourceError` → 其 `code`（永远是代码里的字面量 `host_current_input_*`）；
- 本模块自己抛的 `ValueError` 字面量 → 白名单内原样透出；
- 其余一律 `primary_dependencies_rejected`。

路径、标识符、哈希、请求正文、密钥都不可能进入这个集合（有针对性负例）。`error_code`
（`primary_history_disclosure_rejected`）与 `to_dict()["code"]` 的公开契约不变。

## 5. 测试

新增 `backend/tests/execution/test_current_input_claim_race.py`（8 项，真实 Host FIFO/队列
存储、真实签名控制、真实 SDK runtime/ingress、生产可见性检查器，只有 Provider 是确定性的）：

1. `test_declared_input_guard_passes_while_the_head_is_still_claimed`——**确定性复现**：扣住
   `record_sdk_started` 直到守卫跑完，首个物理请求必然落在 CLAIMED 窗口（不靠抢时序）。
2. `test_claimed_to_running_landing_inside_the_check_is_not_a_claim_change`——外层 G1/G2 窗口：
   G1 读 CLAIMED、慢检查期间真实落 RUNNING、G2 读 RUNNING，仍判同一次认领。
3. `test_running_cannot_be_recorded_before_the_sdk_start_is_observed`——顺序不变量，断言的是
   **真实契约而非夹具自己造出来的顺序**：`_EFFECT_BOUNDARY_ALLOWED_STATES[SDK_START] == {CLAIMED}`
   且 CLAIMED 不在 TOOL 的准入集合；账本里确有 `RETURNED`/`QUERY_FOUND` 观测；对真实存储发起一次
   越界 `record_sdk_started` 必被拒。
4. `test_running_landing_inside_the_current_input_authority_is_not_a_claim_change`——**内层窗口**：
   在 `resolve_current_input` 的两次 head 读之间（钩 `read_current_input_source`）真实落 RUNNING。
5. `test_guard_rejects_a_foreign_run_in_the_claimed_window_and_after_settlement`——负控：
   CLAIMED 窗口内换 sdk_run_id / host_run_id 一律拒；Run 终态后重放也拒。
6. `test_claim_stamp_accepts_exactly_the_live_claim_states`——白名单本身：六个在世态放行、
   四个终态、未绑定 SDK Run、`sdk_run_id IS NULL` 一律拒（真实存储有触发器禁止伪造迁移，
   故用最小合成表覆盖白名单）。
7. `test_same_physical_claim_admits_only_the_start_advance`——只容忍 CLAIMED→RUNNING，
   并覆盖 `may_bind_sdk_run_id` 的开/关两面。
8. `test_redacted_rejection_reason_is_stable_and_payload_free`——含路径/密钥的异常、以及
   携带载荷的 `ValueError` 与其子类，一律降为通用码。

**修前验证（三处都承重）**：只把 `claim_stamp` 白名单改回旧值 → 1/2/5/6 四项失败，报
`host_current_input_physical_claim_unavailable`；只把 `same_physical_claim` 改成严格全等 →
2/7 两项失败；只把 `current_input_authority.py` 还原 → 第 4 项失败，且新原因码直接把它显示成
`primary_dependencies_not_visible`（可诊断性当场生效）。

**回归**（逐条与还原基线对比，同一命令）：

| 套件 | 基线 | 修后 | 差异 |
| --- | --- | --- | --- |
| `tests/execution`（全目录） | 49 failed | 49 failed / 230 passed | 集合完全相同，0 回归 |
| `tests/memory` 五个 current-input 套件 | — | 25 passed | 全绿 |
| `tests/memory` 九个 importer 套件 | 10 failed | 10 failed | 集合完全相同 |
| `tests/quality` c12/c05 + `test_typed_context_use_primary.py` | 11 failed | 11 failed | 集合完全相同 |

基线红项与既知清单一致（short-index embedder、s5b/effect_gate/s5a、scope_disclosure、
typed_context_use_primary、`/Users/denny` 路径、timing flakes、
`test_late_history_denial…[sent_unknown]`、Memory SDK candidate origin mismatch 等）。

## 6. 独立复核

改动交由独立只读复核 Agent 逐条挑战（是否放宽披露、`aiosqlite.Row`/dict 双形态取键、
`public_message` 是否可能带出载荷或破坏既有消费者、测试是否重言）。复核确认：
`LIVE_CLAIM_STATES` 恰为 `RunState` 减去四个终态、被替换的 SQL 逐字等价；迁移 033:319-356 的
`foreground_run_heads_guard` 触发器保证 `sdk_run_id` 一经写入不可改、`generation` 单调、状态不可
退回 `CLAIMED`，故七项身份全等 + 端点 `(CLAIMED, RUNNING)` 基本钉死一次 `record_sdk_started`；
`rejection_reason` 的每条非通用返回不是字面量集合命中就是经 `_REASON_CODE` 复验的
`host_current_input_*`，无泄漏路径；`public_message` 没有既有消费者依赖（各处断言的是
`error_code`），Host 侧审计日志由固定英文句改为稳定原因码，严格更好。复核提出的四条实质问题
（内层第三/四份拷贝、NULL `sdk_run_id`、端点非路径的措辞过强、顺序测试重言与内层窗口未覆盖）
已全部在本次提交内修掉，并各自补了修前必红的用例。

## 7. 事故旁记：`git stash` 在本仓库是跨工作树共享的

取基线时用了 `git stash push -- <文件>` / `git stash pop`。stash 栈是**整个仓库共享**的，不
分工作树：pop 时栈顶已经变成另一个 Agent（`worktree-extra-type-rate`）刚推入的条目，结果把
别人的改动落进本工作树并丢弃了他们的 stash。已用 `git stash store <原 commit>` 把该条目原样
放回栈中，清掉本工作树里的外来文件，并删除自己那条多余的 stash（当时另有
`worktree-termination-persona` 的条目，未触碰）。

**后续 Agent 请勿在本仓库使用 `git stash`**；要取基线请改用文件副本或
`git show HEAD:<path> > <tmp>` 再拷回。
