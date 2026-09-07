# 本机（taiwan Mac）C01-10 首次完整真实模型执行与召回缺陷定位

2026-09-07。接手自 [HANDOFF-2026-09-07](../../../simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/HANDOFF-2026-09-07.md)。Host 候选树 `feat/human-memory-primary-candidate` @ `4c5ed727`，Harness 0.7.10 / Memory 0.6.19 / Service 0.3.13。本文件只记录本机事实，不宣称 240 质量通过。

## 1. 环境重建（本机）

- Host worktree：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-primary-candidate`（分支 `primary-candidate`，跟踪 origin 同名候选分支）。
- `backend/.venv`：`uv sync --python 3.12`，按 `uv.lock` 从 `backend/vendor` 三只 wheel 安装。实测 3.12.14，H0.7.10 / M0.6.19 / S0.3.13，torch 2.7.1。
- installed target：`.local-test-evidence/2026-09-07/installed-h0710-m0619-s0313`，`uv pip install --offline --no-deps --target` 自 vendor wheel；成员逐字节与 wheel 一致：Harness 174 / Memory 92 / Service 116（与那台 Mac 的 INSTALLED-0710619 一致）。
- 那台 Mac 的原始证据包（3.0 GB，22442 文件，09-05～09-07）已解压到主树 `simple_harness/.local-test-evidence/from-other-mac/`，未入库。
- 两个 SDK 源码提交均在 GitHub：Memory 0.6.19 = `e27003c6`（`feat/human-memory-procedure-current-input-successor`），Harness 0.7.10 = `031fdc68`（`feat/nullable-tool-schema`）。

## 2. 用户决定与配置变更

- 用户 09-07 指令：模型用 `gpt-5.6-luna`，评分跑道与 App 两边都切。
- App `~/Library/Application Support/com.dennywanye.simpleharness/llm_runtime.json`：由 chinzy/kimi-k2.6 切为 svtun `/v1` + gpt-5.6-luna（旧文件已备份为 `.bak-20260907T071434`）。
- 候选树 `config.toml` `[llm] model`：gpt-5.5 → gpt-5.6-luna（评分 worker 用 `config.llm.local.model`）。
- 主树 `simple_harness/.env` 的 `BASEURL` 补上 `/v1`（备份 `.env.bak-20260907`）。原因：Host Provider 以 `{base_url}/chat/completions` 拼 URL，缺 `/v1` 会打到 HTML 页面。
- `backend/deskpet/quality/corpus_scoring_session.py`：凭据文件路径由写死的 `/Users/denny/projects/simple_harness/.env` 改为 `host_root.parent/simple_harness/.env`，可用环境变量 `CORPUS_CREDENTIAL_ENV_FILE` 覆盖。仍只读 APIKEY/BASEURL 两个字段。
- 直接裸调用（urllib）gpt-5.6-luna 在 svtun 返回 502「Upstream access forbidden」，在 chinzy 返回 503 无渠道；但 Host 发出的请求形态（httpx、系统提示+工具）稳定 200。裸探测的失败原因未查清，与本轮无关。

## 3. 三次真实执行（均为 C01-10，单例、独立 root、无重试）

| 轮次 | installed target | 预算 | 结果 |
|---|---|---|---|
| r1 | 正式 0.6.19 | 2GiB/180s | 主 Run 完成（8 次 Provider、7 次 context_route），180s 截止时被终止，卡在 Run 后 WeMM 首次加载；无 packet。resource returncode 125，remaining=[]。 |
| r2 | 正式 0.6.19 | 6GiB/900s | **首次完整闭环**：worker exit 0，56.9s，峰 1.15GiB。4 次 Provider handoff，3 次 memory_standalone 召回全部 `recall_refs=[]`；模型最终回答「读不到你已存的约定」。verdict PENDING_POST_TERMINAL_REVIEW；类型：semantic 命中 + 多提 procedure（extra=1）。 |
| r3-spike | 0.6.19 副本 + 词法门补丁（见 §4） | 6GiB/900s | 39.3s，2 次 handoff；第 1 次召回即返回 `recall-item:ddf0e7e3…:1`（semantic，A）。模型回答「取件→提交→复查」，与 gold 一致，未创建提醒。类型：semantic 命中 + 多提 prospective（extra=1）。 |

r2 的 setup 回执证明记忆 A 已写入（`cognitive_memory_heads=1`，`semantic_claims` 含 `todo_sort_order` / 「按截止时间升序，没日期的放最后」/ qualifiers 「待办清单」），但 `typed_recall_decisions` 记录 `candidate_query_count=1, filtered_candidate_count=0, outcome=no_recall, reason=recall_no_eligible_memory`。

## 4. 根因（Memory SDK 0.6.19，`backends/sqlite_v5.py`）

`_collect_typed_recall_candidates`（wheel 内第 4697 行附近）与 `_collect_typed_recall_confirmation`（第 4267 行附近）用
`re.findall(r"[\w\u3400-\u9fff]+", plan.query.casefold())` 切查询词。Python 的 `\w` 本身匹配汉字，于是中文查询只按标点断成整句，例如「查找用户已存的待办排序约定」「提交周五到期」；随后要求这些整句在候选 payload 里逐字出现（`payload_text.count(term)`），否则 full_text 通道为 0。本例无 entity 约束、无 task_scope、无时间约束，`lane_values` 为空即被 `continue` 丢弃。离线复现：三条真实查询的 lexical_score 均为 0。

同时向量通道在该版本被硬编码为不可用（`cognitive_vector_unavailable`），短期通道 `NO_ACTIVE_GENERATION`。因此**中文用户的长期语义记忆在 typed recall 下几乎不可能被召回**，除非查询整句原文出现在记忆内容中。

SDK 已有 `features/lexical.py::lexical_units`（ASCII 词 + 中文二字组合），仅用于旧 FTS 路径。spike 只把上述两处 `query_terms` 改为 `lexical_units(plan.query)`，其余不动；r3 即召回成功。二字切分在本例命中「待办」「日期」。

## 5. 结论与下一步

- 本机评分跑道已可端到端运行到 Provider 并产出 review packet；那台 Mac 的 3 次失败（HTTP 400）本机未复现。
- r2 是第一条**完整执行但召回失败**的真实语料记录；r3 只是 spike，不进入正式计数。240 实际正式评分仍为 0 通过（r2 若按 gold 判定为 FAIL）。
- 建议修复路径：在 Memory SDK 后继分支把 typed recall 的两处词法门改用 `lexical_units`，补中文正/负控测试，出 0.6.20 候选 wheel → Host vendor/pin → 重跑 C01。是否同时打开向量通道另议。
- 现有 `cognitive_vector_unavailable` 硬编码、`NO_ACTIVE_GENERATION`（嵌入生成在首次使用后才激活）是另两个独立缺口，本轮未改。

## 6. 本机原始证据（ignored 目录，相对候选树）

| 路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c01-local/resource-r1/resource.json` | f2d72abb5b66945ef3b949fc5565fb6edb2ecf045e2591eb0fdb3a8391318d40 |
| `.local-test-evidence/2026-09-07/corpus-c01-local/scoring-r1/C01-10/worker.log` | 690b1bf319e3abd3caab94ba5a74d3a93864f062ea3de582f9e4f08b2f8fd450 |
| `.local-test-evidence/2026-09-07/corpus-c01-local/resource-r2/resource.json` | 082be8c5d954ad52649e0900aae3f37b995df94347b7d7d6f9240f82dc33d38f |
| `.local-test-evidence/2026-09-07/corpus-c01-local/scoring-r2/C01-10/review-packet.json` | a30b1e6ab883c1a8dfdb07ec7f2f5b2ebdc2e83eea78eb480b555c850f26b488 |
| `.local-test-evidence/2026-09-07/corpus-c01-local/scoring-r2/C01-10/execution.json` | 403be5c1317bae6e9dd0a535946b4ec278fb9cecb20b12c5d79030baf66c7b52 |
| `.local-test-evidence/2026-09-07/corpus-c01-local/scoring-r2/C01-10/observation-transcript.json` | e7b3bdd4206b793ae904ed95cc611057fd73a94df85f9cf8c85d853d844d5fab |
| `.local-test-evidence/2026-09-07/corpus-c01-local/resource-r3-spike/resource.json` | 09cbb1a45d4cbd3835948cc2cdc3ad560b66f4f0d470a09d15627f9bd5661a89 |
| `.local-test-evidence/2026-09-07/corpus-c01-local/scoring-r3-spike/C01-10/review-packet.json` | d73254cc1a8d67526de422e93f53a526d6aeef4372c52cef703f6000b141d849 |
| `.local-test-evidence/2026-09-07/corpus-c01-local/scoring-r3-spike/C01-10/observation-transcript.json` | 5ad756ba07e32b66623eb7df4b213713135988b3a9703787977c8fe9bee56d66 |
| `.local-test-evidence/2026-09-07/installed-spike-cjk-lexical/simple_harness_memory/backends/sqlite_v5.py`（spike 补丁版） | 6fffbb37d1cd2471e7be42289c8c009722d0d1b5ff142770d5ae1ed5b3bebe22 |
