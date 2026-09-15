# Flash A96 只读拆解

最后更新：2026-09-15 17:05 CST。只读；0 新模型调用。身份 `a96-flash256k-v1`，96/96 收据。Qwen 16/96 不混算、不补。

## 结论

19/96 官方 utility **不能**解释成编排收益。

- **S 0/24：** 每例 **1 次模型调用、0 次 AppWorld 工具**，runtime `failed`，官方 `declared_task_status=pending`，答案 `<<not_given>>`。Flash 没有进入单 Agent 工具循环，不是「题做不出来」。
- **R 0/24：** 全部 `ValueError: self-selection has no output`（`appworld_arms.py` 在选择轮 `public_output is None`）。每例约 1 次调用、&lt;5 秒。Flash 没有吐出规定信封 `{"protocol_version":"appworld-r-self-selection-v1","selected_candidate":N}`。
- **D 11/24、F 8/24：** 过关例 Mission 多为 COMPLETED / verification_passed；失败例多为 `budget_exhausted`。`knowledge_reuse_events` **D/F 合计 48 例全是 0**。过关也不是黑板下游消费。
- 三题全臂 0/8：`50e1ac9_1`、`68ee2c9_1`、`4fab96f_1`。

机制判断：**S/R 是 Flash 与单 Agent 协议不匹配**（不调工具 / 不写选择信封）。**D/F 是部分任务能过官方分，但知识复用未发生。** 不值得为追 PASS 再跑 96。若要修，应冻**新**身份、只做 R 信封小复验（数例），不要重开满矩阵。

## 臂

| 臂 | n | utility | 调用/tokens | 中位墙钟 | 典型失败 |
|---|---:|---:|---|---:|---|
| S | 24 | 0 | 24 / 2.5 万 | 2.6 秒 | 1 turn、0 tools、pending |
| R | 24 | 0 | 24 / 2.6 万 | 2.8 秒 | 24/24 self-selection 无输出 |
| D | 24 | 11 | 1116 / 1536 万 | 233 秒 | 失败多为 budget_exhausted；reuse 全 0 |
| F | 24 | 8 | 956 / 1316 万 | 161 秒 | 同上；dynamic_graph true；reuse 全 0 |

D 过关 11 里 Mission COMPLETED 8、FAILED 2、ACTIVE 1（官方仍 true）。F 过关 8 全部 COMPLETED。

## 题

| task | utility | D | F |
|---|---:|---:|---:|
| 530b157_1 | 4/8 | 2 | 2 |
| 0d8a4ee_1 | 3/8 | 2 | 1 |
| 396c5a2_1 | 3/8 | 1 | 2 |
| 23cf851_1 / 37a8675_1 / 6171bbc_1 | 各 2/8 | 1 | 1 |
| 383cbac_1 / 6c2c621_1 / fac291d_1 | 各 1/8 | 1 | 0 |
| 50e1ac9_1 / 68ee2c9_1 / 4fab96f_1 | **0/8** | 0 | 0 |

S/R 对所有题都是 0。

## 知识

D/F 均绑 `appworld-*-host-public-knowledge-v3`。过关例常有 `host_observations_committed` 2–4，但 `knowledge_reuse_events` 全 0。与 N5「观察晋级、无下游消费」一致。不能用这 19 个 true 证明黑板有用。

## 不做什么

不重跑 96。不补 Qwen 96。不把 19/96 写成 A 轮成功或编排优于 S。B 轮 512K 另冻。

原始收据仍在 ignored `matrix-flash256k-v1/episodes/*/result.json`。
