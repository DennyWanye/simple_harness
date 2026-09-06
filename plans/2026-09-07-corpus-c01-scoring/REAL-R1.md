# C01-10 首次真实评分：FAIL

2026-09-07。实际运行 Host `30b07393`，H0.7.9 / M0.6.19 / S0.3.13，沿既有 config 调用 gpt-5.5。没有重复同例、Probe 或模型评审请求。

## 业务结果和原 gold

原 USER：“照我已存的待办排序约定，排一下：提交周五到期、复查无日期、取件周三到期。”
原 gold：取得 A，取件→提交→复查；只排序，不创建提醒。

公开 setup 已应用 A（semantic / active / revision1），memory_id `cognitive-memory-0305e0890582086950eedf6c159868bf7cd90a7d88600261a06432a6fd9621a4`，content_hash `e8f070a08990ff6c520d117d21b235e69fa1b6fb152194251a2c283ed4ba73fb`。
实际唯一物理请求只有 system + 原 USER，12 个可用工具含 context_route；没有 A 的内容、ID、revision。模型未调用工具，最后回答“按常见待办排序”，列取件、提交、复查，并请用户补充约定。

| 原要求 | 本次结果 |
|---|---|
| 取得并使用 A | FAIL，零召回、零工具调用 |
| 取件→提交→复查 | 正确 |
| 不创建提醒 | 满足，route/effect 均空 |
| 原 gold 合取 | **FAIL** |

主审与 Dirac 对实际公开 trace 独立阅卷一致。实际 Provider handoff=1、tool_calls=0、提出 memory_types=[]、required semantic 命中0/1、extra types=0；本例完整观测，不把零额外类型当整体通过。usage input2840/output518/total3358（其中 reasoning435），这是模型调用量，不是本代理用量。

Host Run `877a6f33-15f8-5403-aec0-b0a6cfa2b007`；SDK Run `product-sdk-7c7caaae53d8dae2c713dcb42fa62731e2b461c980b613ea40857c2c080f878d`。公开终态 COMPLETED、trace COMPLETE、provider_observation_complete=true、observation_errors={}；业务终态不等于质量通过。

## 退场失败和资源

worker 保存 execution.json 后，解释器等待尚未退出的 Python 线程；父进程尚未生成 review-packet/batch。外部 runner 于180.249s触发 deadline，exit125/parent-15；PG67059已清空、cleanup_error=null、峰RSS1100272KiB、最低磁盘2458MiB。没有触发RSS/磁盘门。execution.cleanup_errors=[]只表示已调用closer未报错，不证明进程自然退出。

本 Markdown 是主审在进程退出后的原始证据阅卷，不伪造缺失的 runner packet，不重新请求模型。保存此失败并修复资源所有者退场；不能仅用 os._exit 掩盖。240条真实评分已尝试1条，语义通过0条；其余239条仍未实际评分。此单例不推导全语料质量门。

此前仅移除旧任务的 Rust deps/build/incremental 可再生缓存，空闲字节631730176→3664871424；该清理记录的 preserved_binary_hashes 为空，不声称本次做了二进制hash覆盖。未删原始证据、用户数据、模型或应用。caffeinate PID56392持续保持显示器/系统空闲不睡眠，整个测试结束后再解除。

## 后续改动（尚未验证）

真实 system 提示只介绍任务路由，缺少依赖个人历史时先读取来源的指导。仅生产 Primary PERSONA 补通用来源规则、由模型自行选择所需类型、区分TaskScope与Procedure；不带本case/gold、答案、固定类型，不修改旧输入或评分carrier。后续用新的未执行case验证；不重跑本例刷绿。

## 本机原始证据

| 相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1/C01-10/execution.json` | `dbd280ec2e8a011239e5a5fafeea4e68a673e2ba429c5c27a69f932dee673a01` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1/C01-10/observation-trace.json` | `d08e53e2ec637a2ab98510ac18dbd251208ba652251e13d0d27b3dbbf91b40b4` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1/C01-10/observation-route_audit.json` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1/C01-10/observation-route_effects.json` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1/C01-10/setup.json` | `c9fd532369aa8422b4fd2aa401cfa16765dabb3954e968e609b29ac8b138858b` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r1/C01-10/oracle.json` | `639a96f6a00a31e32fa2dacc2b80cb5868bf53e9887f1bce06c353a5dac00186` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/resource-r1/resource.json` | `06485e656a50d099cd4868f592e248cff65254957f7aafa69ef2f9ecc7f18f24` |
| `.local-test-evidence/2026-09-07/native-decision-build/old-plan-cache-cleanup.json` | `d5d02b903ec321ca5fa22b56213c8ef0f5b97fdeb493acbc891763bb5d6a6999` |
