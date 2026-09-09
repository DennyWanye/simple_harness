# 裁决 AJ：第 12 次原生跑 T20/T21 全条被拒——同一个 predicate 下的两条记忆不是「歧义的更正」

事件：HM-TO-A6 第 12 次尝试（2026-09-09 19:35，turns 20–21），A6-7 / A6-8 判 INCONCLUSIVE。
证据目录：`.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`。
对照：第 11 次（`native-a6-run11/primary-ui-9izlp1ao`，协议 v9），同两轮全部通过。

结论先说：**这不是 AE（v10 / validator v6 的 prospective 接地）造成的，也不是 v10 提示词把模型带偏。**
两条 operation 的形状在 v9 与 v10 下逐样本相同；被改坏的是 Host 更正切片里两条**与协议版本无关**
的准入规则，它们在第 12 次因为上游槽位命名撞车第一次被踩中。

---

## 1 证据重建（逐行）

### 1.1 拒收码（`native.log`）

| 行 | batch | 逐条拒收 |
| --- | --- | --- |
| 2521 | `analysis-batch-95b2522e…`（T20） | `[('revise_py313', 'analysis_correction_candidate_ambiguous', {})]` |
| 2522 | 同上 | `analysis_all_operations_rejected`（本轮记忆未物化） |
| 2580 | `analysis-batch-d6d1f8f9…`（T21） | `[('contest-8a24131c', 'analysis_contest_slot_mismatch', {'reason': 'qualifiers'})]` |
| 2581 | 同上 | `analysis_all_operations_rejected` |

两个 batch 的 `analysis_batches.state='applied'`、
`application_receipt_json.reason_codes=["analysis_validator_accepted"]`、
`validator_version="host-analysis-validator/v6"`——**这是正常的**：验证器接受的是「模型给的这份提案
经过 Host 逐条准入之后的结果」，而结果恰好是 0 条 operation，`structured_result` 因此是
`{"outcome":"no_mutation","closure_reason":"analysis_all_operations_rejected","operations":[]}`
（`result_hash` 分别为 `5d2a0a08…` / `e0b87c20…`）。收据说「验证通过」，日志说「全被拒」，两者不矛盾。

### 1.2 下发给模型的候选（`state.db.human_memory_evidence`）

T20 的候选快照 `analysis-candidates-f18430f5…`，**两条候选、同一个槽位**：

| candidate_key | memory_id | subject·predicate | object_value | qualifiers | correction_intent |
| --- | --- | --- | --- | --- | --- |
| `semantic-candidate-6c9669c9…` | `cognitive-memory-4e4b8346…` r1 | `user:self · workflow_environment_preference` | `统一用 Python 3.12 跑脚本` | `["doing 资料校对"]` | **有**：`grammar=cue-anchor/v1`、`cue=更正一下`、`anchor=Python`、`old_value=统一用 Python 3.12 跑脚本`、`new_value=null`、`intent_hash=f230db9a…` |
| `semantic-candidate-3834ca5f…` | `cognitive-memory-1a1644b6…` r1 | `user:self · workflow_environment_preference` | `校对结果一律存到「外接硬盘 / 校对归档」这个目录` | `["doing 资料校对"]` | **`null`** |

T21 的候选快照 `analysis-candidates-d7d66f64…`：只有一条
`semantic-candidate-44b92742…` → `cognitive-memory-4e4b8346…` **revision 1**（因为 T20 的 revise 被拒，
它没有拿到 revision 2），payload 同上，`correction_intent: null`（含糊句不该有更正意图，正确）。

对照第 11 次同两轮（`analysis-batch-c6b1563e…` / `analysis-batch-07bb3e0b…`）：四条候选的 predicate
**各不相同**（`uses_python_version_for_proofreading` / `proofreading_archive_location` /
`primary_document_authority` / `project_file_path`），qualifiers 是 `["task=材料校对"]`。

### 1.3 落库结果

`cognitive_memory_revisions`：

* run11 `cognitive-memory-cce67e6d…`：r1 `Python 3.12` → r2 `Python 3.13`（T20 REVISE）→ r3 `contested` `3.12`（T21 CONTEST），
  `cognitive_conflict_groups` 1 行（`incumbent` r2 / `challenger` r3）；A6-7 / A6-8 PASS。
* run12 `cognitive-memory-4e4b8346…`：**只有 r1**，`cognitive_conflict_groups` 0 行。

### 1.4 提案形状（真实模型复算，不是推断）

把第 12 次持久化的 T20 attempt-input（`analysis-attempt-input-d4c08b22…`）逐字重放，
system 提示与工具 schema 按臂替换，其余（证据体、`now_iso`、候选、`max_output_tokens`）一字不改：

**v9 与 v10 各 6 个样本，6/6 都提 `revise_semantic`、6/6 都引用了正确的 `candidate_key`、
6/6 的 `qualifiers` 与候选逐字相同、6/6 被 Host 拒收为 `analysis_correction_candidate_ambiguous`。**

也就是说模型没错，v10 的提示词也没有把它带偏——**拒收发生在模型之后**。

---

## 2 根因

### 2.1 T20：按 predicate 计数的「同槽歧义」是一个过期的代理判据

`analysis_proposal.py` 的 `revise_semantic` 分支（旧 435–437 行，`d0cce501` 引入）在确认了
「本句唯一一条带 correction_intent 的候选」之后，**又**数了一遍同 `(subject_entity, predicate)` 的候选，
不等于 1 就判 `analysis_correction_candidate_ambiguous`；
`semantic_correction.authorize_plan`（旧 757–758 行）有一条完全一样的 `analysis_action_target_ambiguous`。

这条规则写于只有模板语法（`Correct my <predicate> from <old> to <new>.`）的年代，那时句子只点名
predicate，槽位唯一性确实是唯一的判别依据。后来加入的 cue-anchor 语法把判别依据换成了**旧值**：
`_discriminating_anchor` 只接受「本句引到、且**其他候选的值里都不包含**」的 token，
`explicit_correction_intent` 因此天然只会落在一条候选上，紧邻的
`sum(correction_intent 同 evidence_id 且同 exact_quote) != 1` 已经把唯一性钉死了。
模板语法同样把 `old` 逐字嵌进正则，也自带旧值绑定。

而 predicate 是**模型在写下这条记忆时自由取的字符串**，Host 没有任何注册表。
第 12 次 T1「统一用 Python 3.12 跑脚本」和 T2「校对结果一律存到…这个目录」两件毫不相干的事
被写进了同一个 `workflow_environment_preference`。于是这条代理判据把一次 Host **已经唯一定位**的
更正判成了歧义。第 11 次没踩中，只是因为那次模型给了四个不同的 predicate——**运气，不是保护**。

### 2.2 T21：CONTEST 要求模型逐字重打一份 Host 自己会覆盖的 qualifiers

`analysis_proposal_v7._compile_contest`（旧 103–104 行）要求模型给的 `qualifiers` 与在位那条
逐字相等，否则整条 `analysis_contest_slot_mismatch/qualifiers`；可是紧接着第 115 行组装 payload 时用的是
`tuple(old.get("qualifiers", ()))`——**模型给什么都不会被写进去**，SDK 那侧还会再把 CONTEST 的槽位钉死一遍。
第 12 次的 qualifier 文本是 `doing 资料校对`，模型没有原样重打，一条本来完全合法的含糊冲突就整条丢了，
A6-8 连 conflict group 都没有。

### 2.3 与 AE 无关

`analysis_proposal_v10.compile_proposal` 只在
`_compile_non_relation` 里对 `memory_type == "prospective"` 调 `prospective_grounding`
（`analysis_proposal_v8.py` 第 360–361 行），semantic 的 create/revise/contest 一条都不经过它；
`v8._compile_validated_proposal` 的 `prospective_grounding=None` 默认值对 v8/v9 也保持原样。
用例 `test_v10_grounding_never_runs_for_a_revise_or_contest_operation` 把这条钉住（
打桩 `grounding.check_time_trigger`，revise 编译后调用次数必须为 0）。

---

## 3 修复（契约口径）

一句话：**槽位归 Host，替换值归模型**——这本来就是 cue-anchor 语法自己的注释写的话，
只是两处准入规则还停在旧口径上。

1. `analysis_proposal.py`：删掉按 predicate 计数的同槽检查。目标唯一性由紧邻的
   correction-intent 计数负责（它是真正的判据，代码与拒收码都保留）。
2. `semantic_correction.authorize_plan`：删掉同一条 predicate 计数
   （`analysis_action_target_ambiguous` 仍由该函数里基于 intent 计数的那条抛出，语义不变）。
3. `analysis_proposal.py` 的 revise 分支：模型**没给** qualifiers 时继承在位那条的；
   给了且**非空又不同**时仍然 `analysis_correction_qualifiers_mismatch`。
4. `analysis_proposal_v7._compile_contest`：同样口径——空即「未主张」，非空且不同仍然
   `analysis_contest_slot_mismatch/qualifiers`。

`semantic_correction.authorize_plan` 与 `_verify_contest` 里**逐字相等**的 qualifiers 校验一个字没改：
编译器现在保证交上去的 operation 携带在位那条的 qualifiers，权威车道的纵深防御原样生效。

### 3.1 为什么就地改，而不是发一个 v11 编译器分支

`created_endpoint_must_survive` / `prospective_grounding` 那种「按协议版本冻结」的做法，管的是
**线格式与提示词**（提示体进 `bind_attempt` 哈希，改词会让已持久化请求重放报
`analysis_attempt_input_conflict`）。本次两条都不碰线、不碰 schema、不碰提示词。而且：

* `SemanticCorrectionAuthority` 根本没有协议版本维度（`ISSUER = host:semantic-correction/v2` 一个实现，
  对 v3..v10 同一套 `authorize_plan`）。只给编译器加版本分支，权威车道照样会拒——必须一起改。
* 更正切片历来就是就地演进的：`d0cce501` 给 v3..v8 一起加了 `revise_semantic`，
  cue-anchor/v1 又就地替掉了别名表。
* 两条都是**严格放宽**，且不可能写出不同的 payload：CONTEST 的 qualifiers 一直取在位那条；
  REVISE 在模型不主张时继承在位那条；目标定位一个字没动。

### 3.2 为什么这次不发 v11 提示词

任务书把 v11 的条件写成「v10 的措辞把模型从 revise/contest 带偏，或弄坏了 candidate_key 的用法」。
§1.4 与 §4 的复算说：**没有**。v9 与 v10 逐样本同形。发 v11 只会让 AE 的 §5 复算全部作废，收益为零。

---

## 4 真实模型复算（`deepseek-v4-flash`，`scripts/native/a6_replay_t20.py`）

做法与 `a6_replay_t14.py` / `a6_replay_t15.py` 一致：请求体逐字重放，臂之间只换 system 文本与工具 schema；
候选按请求体重建，`correction_intent` 由 Host 自己的 `explicit_correction_intent` 现算
（与 `SemanticCorrectionAuthority.prepare` 的第二趟同一份代码，模型不参与）。
新增 `--recompile`：**同一批模型样本**分别喂给修复前（main `1e825018` 的 backend）与修复后的编译器，
所以下表的「修复前/后」不含采样噪声。凭据只在内存里做 Authorization 头，不打印、不入库。

### T20（A6-7，`更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。`）

| 臂 | 提 `revise_semantic` | 引对 candidate_key | qualifiers 逐字一致 | 修复前接受 | 修复后接受 |
| --- | --- | --- | --- | --- | --- |
| v9 | 6/6 | 6/6 | 6/6 | **0/6**（全 `analysis_correction_candidate_ambiguous`） | **6/6** |
| v10 | 6/6 | 6/6 | 6/6 | **0/6**（同上） | **6/6** |

（另有一批 6+6 的先行基线，v9/v10 同样 0/6、全为 `analysis_correction_candidate_ambiguous`。）

### T21（A6-8，`不过我印象里上周好像还是按 3.12 在跑的，你说呢？`）

| 臂 | 样本 | 提 `contest_semantic` | 修复前接受 | 修复后接受 |
| --- | --- | --- | --- | --- |
| v9 | 18 | 3/18 | 3/18 | 3/18 |
| v10 | 18 | 4/18 | 4/18 | 4/18 |

（成对的 12+12 用 `--recompile` 逐样本对照修复前/后，判决完全相同；另 6+6 为先行基线。
v9 另有 2 个样本因提案体本身形状不合法被 `analysis_operation_payload_invalid` 拒，与本次两条规则无关。）

**口径要说清楚**：隔离重放里模型只要提了 contest，就一次不落地把 qualifiers 逐字重打对了，
所以这一批**测不出**修复前后的差别——和事件 AE 一样，`qualifiers` 失配是低频采样事件；
它真实发生过（`native.log` 2580 行，逐条码 `analysis_contest_slot_mismatch{reason: qualifiers}`），
由用例 `test_contest_compiles_when_the_model_omits_the_qualifiers` /
`test_contest_still_refuses_a_stated_but_different_qualifier_set` 钉住两个方向。
另一件能被测量到的事：**flash 在这句上只有 ~20% 会提 contest**（v9 3/18、v10 4/18，两臂相当，
不是 v10 引入的），第 11/12 次原生跑各自 1/1 都提了。这是 A6-8 的独立风险，记为
**F-AJ-1**（含糊冲突的提案率），本轮不动提示词。

---

## 5 控制（named files，单进程）

| 文件 | 结果 |
| --- | --- |
| `tests/memory/test_semantic_correction_slot_collision.py`（新增，10 项） | 修复前 **7 failed / 3 passed**，修复后 **10 passed** |
| `test_analysis_proposal_v10.py` / `v9` / `v8` / `test_semantic_correction.py` / `test_semantic_correction_cue_and_contest.py` / `test_analysis_v9_named_workflow_relation.py` | 150 passed |
| `test_analysis_proposal_v5/v6/v7.py`、`test_analysis_relation_{applied,procedure_applied,prospective_applied}.py`、`test_analysis_v6_public_relation.py`、`test_analysis_v8_existing_relation.py`、`test_contested_recall_disclosure.py`、`test_analysis_request_guard.py`、`test_prospective_trigger_local.py` | 69 passed |

新增用例覆盖：撞槽下的 revise 编译 / 端到端 revision 2 且撞槽的那条不动；omitted qualifiers 的
revise 继承与 contest 成立；两个方向的「非空且不同仍然拒」；anchor 真的分不清时仍然
`analysis_explicit_correction_intent_missing`（放宽的只是 predicate 计数）；AE 接地不碰 revise/contest。

## 6 遗留

* **F-AJ-1**：`deepseek-v4-flash` 对含糊冲突句的 contest 提案率 ~20%（隔离重放），A6-8 仍可能因
  「模型没提」而 INCONCLUSIVE。要动就得发 v11 并重跑 AE §5，本轮不做。
* **F-AJ-2**：predicate 是模型自由取的字符串，两件无关的事撞进同一个槽位（本次 T1/T2）本身就是
  记忆质量问题——它不再阻断更正，但撞槽的记忆仍然并存。是否需要「同槽写入时提示模型换更具体的
  predicate」属于 v11 及以后的策略题，与本次准入回归分开。
