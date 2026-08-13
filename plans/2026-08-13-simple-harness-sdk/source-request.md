# Source request ledger — Simple Harness SDK

> 记录日期：2026-08-13  
> 用途：冻结本 plan 的用户行为语义来源；hash 按每条引号内 UTF-8 原文（不含 Markdown 引号和换行）计算。

| ID | 用户原文 | SHA-256 | 映射 |
|---|---|---|---|
| SR-1 | “不同意，我希望是完整的 durable RunKernel 也提取进首版，后续都直接使用这个sdk就好，另外请你检查现在的plan-test是不是git上面最新的版本？” | `9b305dab0e4b7d8d5831f292cd68f6e429337f246716e01721aa558de6632a83` | SDK-AC-5、SDK-AC-8 |
| SR-2 | “1.不同意，需要支持对应的workflow，我不想要在使用这个sdk的时候，重新开发一遍这些workflow” | `9fa3afd80e852c6765037c6888e13cdad3b345b19c95cf57f57afcb866670381` | SDK-AC-6、SDK-AC-7 |
| SR-3 | “2.为什么要处理历史Run？请解释，这个项目现在是开发阶段，没有实际使用用户，不需要对历史run进行兼容性处理” | `62e7664dcdbe81f05106ed6a6e9a9deb6ea4c483478dd8fed16bfbb9e0c95181` | SDK-AC-7、数据重置 |
| SR-4 | “需要这三个：workflow.durable_task，workflow.personal_v1，workflow.capability_build” | `42bb0471f602a6f6da18083d539168737a1055f294043030594efa590a88f0b9` | SDK-AC-7 |
| SR-5 | “我希望是让Agent自己做决定” | `facb88414cd8da2b063fc6cc50ca81f403b002744422b06eac8ffc23596a9265` | SDK-AC-6 |
| SR-6 | “同意，另外我希望不仅仅是AIPhone那边，当前的这个simpleHarness也使用这个抽离出来的sdk。” | `cc6ff260e21cd4ce8d9d88efb40a37c8ff242695d5d641ba2b6422dbc82290a8` | SDK-AC-8、消费者顺序 |
| SR-7 | “好的呢，同意这套发布与迁移顺序” | `5d960ae744da8fdff18dc979f6597d7ce56c2666f16def08fe5c48e2ae84de60` | exact-wheel cutover、发布、Handoff |
| SR-8 | “同意 BC-SDK-IMPORTS” | `112c0f14b202efa7280e1cfa8cb81f4a7dd9ffc4490ca265ad85391d673efaaa` | 批准 frozen oracle 的机械 import/fixture 迁移与三类 legacy 断言退休 |
| SR-9 | “请你说明这个cold_start具体是做什么，我感觉这个会有阻塞，如果你不能代替我输入的话，作为后续的进行处理，记录在followup中” | `a47e8f2b95987c0c5fbcaa6f79c899f643a637fdd6045c76b77d699e683606ea` | 产品级首次登录 cold-start 改为独立 follow-up；SDK clean install/init 与已有登录态 E2E 仍保留 |

## 批准事件

- 2026-08-13：用户确认 SDK 仓库名 `simple-harness-sdk`、Apache-2.0 / BUSL-1.1 边界、三个 public Profile、无旧 Run 兼容、Simple Harness 首个真实消费者、exact wheel 最终验收、删除旧同源实现、发布后交接 AIPhone。
- 2026-08-13：用户显式批准 `BC-SDK-IMPORTS`；旧测试可按批准范围机械迁移 import/fixture，并退休只验证 legacy router、ticketless child、Personal 前置 matcher 的断言，其他 durable oracle 不得放宽。
- 2026-08-13：用户要求避免产品级首次登录输入阻塞当前 SDK 提取；该 cold-start 场景登记为独立 follow-up。SDK 自身 clean install/import/init/reopen 和已有有效登录态下的产品真实 E2E 不受影响。
- 冻结 acceptance：[`acceptance.md`](acceptance.md)。
