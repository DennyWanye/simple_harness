# Flash A96 R 信封小复验（新身份）

最后更新：2026-09-15 21:33 CST。模型仅 `deepseek-flash`。身份 `a96-flash256k-r-envelope-v2`，**不是** `a96-flash256k-v1`，不与 Qwen 16/96 混算，不开 512K，不重跑 96。

## 修了什么

v1 的 S/R 全灭不是「Flash 不会写 JSON」。第一轮 billed input=980，计量预估没把 tool schema 算进去，`usage.input_tokens > input_reserve` 关闭 meter，候选和选择轮全部 `ExperimentBudgetExhausted`，最后被记成 `self-selection has no output`。

SDK `f7432dc`：

- `estimate_provider_request` 计入 messages + tools
- R 在候选 `public_output` 全空时跳过选择轮，不再用空选择掩盖预算关闭

定向测试：`tests/orchestrator/gap_phase1/test_appworld_arms.py` 25 PASS。

## 小复验

2 题 × 仅 R × 1 次。窗口 262144，80 calls / 4M tokens / 1800s。官方 `api.deepseek.com`。

| task | 选择信封 | selected | 三轮状态 | 官方 utility | valid_success | 调用 / tokens | 墙钟秒 |
|---|---|---:|---|---|---|---|---:|
| 530b157_1 | 有 `appworld-r-self-selection-v1` | 2 | committed×3 | true | true | 34 / 510975 | 63.43 |
| 0d8a4ee_1 | 有 `appworld-r-self-selection-v1` | 1 | committed×3 | false | false | 25 / 311779 | 43.74 |

合计 59 调用、822754 tokens、未知用量 0、admission denial 0。`0d8a4ee_1` 第一轮预留 1549 / 实计 980（v1 同题同 980 直接关 meter）。

## 结论

R 规定信封在 Flash 256K **可以执行**，前提是计量上界包含 tools。2/2 选择闭环成立；官方 1/2 只说明第二题答案不对，不是协议未执行。

**仍不能：** 把 v1 的 19/96 改写成过关；宣称 S 已修（未复测 S）；宣称黑板/知识复用；开满矩阵或 512K。

原始收据 ignored `.local-test-evidence/2026-09-15/a96/matrix-flash256k-r-envelope-v2/`。
