# FactExtractor 内容哈希幂等去重(Layer 1) — windows-mcp 真机测试结果

> 对应 testcase：[`testcase/2026-06-27-factextractor-dedup/manual-test.md`](../../testcase/2026-06-27-factextractor-dedup/manual-test.md)
> 环境：`npx tauri dev` 源码 backend（`[backend_launch] Dev python=...backend` 确认）+ dev userdata + relay-cloud；真坐标点击 + 剪贴板中文输入。
> 观测面：facts 表 `backend/userdata/data/state.db` count + `tauri-dev.log`（UTF-16）grep `facts_extract_skip_dup`(info)。
> relay 健康（全程 0 次 `FactExtractor.extract LLM failed`），故 count 判定非假绿。

## §6 判定汇总

| Case | 类别 | 预期 | 实测 | 判定 |
|---|---|---|---|---|
| **TC-0 ★** | 接线/零崩溃 | boot `content_dedup=True`+无崩溃 | `p4_fact_extractor_ready ... content_dedup=True` + `[backend_launch] Dev python=...backend` + 无 ConfigError + 无 Bundled exe + Uvicorn | ✅ **PASS** |
| **TC-1 ★ / IDEM-A ★** | 核心去重(三证) | C1>C0 ∧ C2==C1 ∧ skip 日志 ∧ 无第二次抽取 | C0=363 → 发"我的工号是 B9981，在数据平台组。" → **C1=366(+3 真抽过)** → 重发完全相同 → **C2=366(==C1)** + **`facts_extract_skip_dup seen_ago=65s`** + 0 失败 | ✅ **PASS** |
| **TC-2** | 归一化幂等 | 空白/大小写变体仍短路 | 变体"  我的工号是 b9981，在数据平台组。  "(前后空格+小写b) → count 仍 366 + **skip_dup→2** | ✅ **PASS**（注：首次测试句误加内部空格导致归一化不同，已用正确变体复测通过） |
| **TC-3 ★ / IDEM-E** | 不同内容不误短路 | 新内容正常抽取、无 skip | 发"我的办公座位在 12 楼 A 区 18 号。" → **count 366→367(+1)** + skip 仍 2（无新 skip） | ✅ **PASS** |
| **TC-4 / IDEM-D ★** | flag OFF 字节 BC | OFF 下重发不短路 | 改 `extract_content_dedup=false` 重启 → boot `content_dedup=False` → 重发相同 → **`facts_extract_skip_dup=0`**（无短路=旧行为）+ 0 失败 → 测后还原 | ✅ **PASS** |
| **TC-5 / IDEM-C** | TTL 边界 | 超 TTL 重发可重抽 | 设 `content_dedup_ttl_s=5` 重启 → 发 TTL 事实 → 等 15s(>5) 重发相同 → **`facts_extract_skip_dup=0`**（占位过期→不短路→重抽）→ 测后还原 | ✅ **PASS**（窗口边界生效；对比 TC-1 默认 3600s 窗口内 skip） |
| **TC-1b** | 并发近似 | 快连发不双抽 | UI 连发难严格并发（每次发送多 tool 调用，第一条常已抽完）→ 退化为 TC-1 | ⚠️ degraded(sequential)，单测兜底 `test_content_dedup_concurrent_same_content_calls_extract_once`(==1) + `test_content_dedup_placeholder_registered_before_llm_call` |
| **TC-6** | summarizer 不短路 | 系统 summary 不短路 | summarizer 由系统生成，用户难稳定触发 | ⚠️ env-limited(system-generated)，单测兜底 `test_content_dedup_does_not_skip_summarizer_source` |
| **TC-7** | relay 502 防假绿 | 502 时标 env-limited | 全程 relay 健康(0 失败)，无 502 可触发；C1>C0 真抽过证据已用于排除假绿 | ✅ 不适用(relay 健康)，防假绿机制以 TC-1 的 C1>C0 体现 |

## 最终判定

**DECISION: SHIP** ✅

- **全部 ★ 一票否决项 PASS**（TC-0 / TC-1 / TC-3 / IDEM-A / IDEM-D）。
- 核心 bug 真机端到端修复确认：重发完全相同的话 → facts 表 count **不增**（C2==C1=366）+ `facts_extract_skip_dup` 日志（修复前真测同场景 +3，现 +0）。
- 归一化 / 不同内容不误短路 / flag OFF BC / TTL 边界 全 PASS。
- 并发/summarizer 退化为单测兜底（真机难稳定触发，已诚实标注）。
- **未发现任何代码 bug**；唯一"失败"是 TC-2 首次测试句构造失误（误加内部空格），修正测试句后通过——非功能缺陷。

## 配套
- 实现：`config.py`(extract_content_dedup/content_dedup_ttl_s/cache_max) + `facts.py`(_content_hash/_dedup_lock/抢占登记/撤销/刷新/clear_content_cache) + `main.py`(接线+boot-log)。
- 单测：`backend/tests/test_factextractor_content_dedup.py` 13 个全绿。
- 完成度：codex gpt-5.5 3 轮评估至 100%。
- 证据日志：`factextractor-test.log`(TC-0~3) / `factextractor-tc4-off.log`(TC-4) / `factextractor-tc5-ttl.log`(TC-5)。
- config 测后已从 `config.toml.factextractor-backup` 还原（临时 OFF/ttl 改动清除）。
