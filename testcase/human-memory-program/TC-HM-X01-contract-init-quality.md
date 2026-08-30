---
id: TC-HM-X01
purpose: Verify three-repository public contracts, fresh initialization, frozen recall quality, and performance budgets
status: active
surface: api
type: scripted
obligations: [HM-TO-A1, HM-TO-A2, HM-TO-A8, HM-TO-R1, HM-TO-R4, HM-TO-R5, HM-TO-R6]
tags: [human-memory, cross-repo, wheel, quality, benchmark, fresh-init]
entrypoint: clean wheel consumers and frozen evaluator corpus
revision: 1
---

# TC-HM-X01 — 跨仓契约、全新初始化与质量硬门

## 固定输入

- 三仓 exact candidate commit/wheel hash 与公共协议版本。
- 固定 seed 的 evaluator corpus：exact/semantic/entity/time/task/cross-scope/no-match/suppressed/superseded/contested/expired/recipient-private，包含明确 Episode、Semantic、Procedure、Prospective 和无长期价值反例。
- 1k/10k/100k active records；至少 200 个 query；1M 仅 exploratory。

Fixture lock：

- `model-eval-corpus-spec.json` SHA-256 `226e75124dee43efeead8f270da532e7d89ee9d7b97799ec2f6948825b903482`
- `generate_model_eval_corpus.py` SHA-256 `6b65efadef2282a4be0539b5aaf87ad4ac59d7e7840721298a2c5a6c37344e60`
- `metric-formulas.json` SHA-256 `27c99ab292f9d308d40ecd1e9d83ea836706d8c25e4096b200dde47efafb356a`
- `program-journey.json` SHA-256 `b462b84244153867962b08f4d2dde8a2d3293562a90b3aeee616f27acbeeee8f`
- `fault-matrix.json` SHA-256 `88fbc1ee38e557b3e6799ea505148ffb6b110019e890d467306bfa341d2735e4`
- 由上述固定 generator/spec 产生的 240-query corpus SHA-256
  `87482da2913963430a86c5b14d26bfa039a6fdbf09bc133f92ff1a7e5d110ae3`

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 在干净环境安装 Memory SDK 与 Harness SDK wheels，由 Host 只经公开 API 运行 consumer contract。 | exact 版本通过；错误版本在写状态或 effect 前 fail closed；wheel、协议与 commit hash 入证据。 |
| 2 | 从空数据目录并发初始化，在各事务 checkpoint crash/retry/restart。 | 只有一个可写主对话；schema、short-horizon index、worker/outbox 状态完整且幂等，无半初始化。 |
| 3 | 对冻结 corpus 用真实主模型独立跑两轮并保存逐项 prediction、模型/Provider identity 与每项分子分母。 | hard-trigger recall 和 privacy forbidden correctness 均 100%；required-memory-type recall >=90%；no-recall correctness >=90%；TaskScope search correctness >=90%；extra-type rate <=15%；每一轮和 micro aggregate 均过门。 |
| 4 | 运行本地 cold/warm benchmark，记录所有原始样本。 | warm p95 <=500ms、单 query max/hard deadline <=2s；cold first query <=2s 或进入冻结的稳定降级；报告 p50/p95/max/RSS/index/rebuild/write amplification。 |
| 5 | 对 4k/8k/32k Context fixture 核对真实 provider usage 和逐分区 item/byte/token 样本。 | item/byte/token 不超 effective budget；generation reserve 为 1024/2048/4096，安全余量 `max(256, 10%)`；低估即 FAIL。 |
| 6 | 在 retention、suppression、维护、测试清理、索引重建和容量不足路径前后核对 raw rows/hash。 | 原始证据行数和内容 SHA-256 不减不变；容量不足时拒绝新写入，不清理旧 evidence。 |
| 7 | 扫描 DB/object/log/docs/vector input/ContextSnapshot。 | credentials 与隐藏 reasoning 零命中；允许的结构化 LLM invocation/decision 审计字段完整。 |

## 两轮真实模型 authority

- 第一轮必须在同一个 `primary_conversation` 中至少 20 个 committed turns、两个 TaskScope、一次 exact resume，并包含 tool/Provider/记忆/纠正/遗忘/Prospective。
- 第二轮使用独立 root 和独立模型调用；最终指标聚合全部样本，不得只报平均值掩盖单轮失败。
