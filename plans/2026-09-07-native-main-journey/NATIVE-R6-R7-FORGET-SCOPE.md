# 原生 r6/r7：遗忘只针对记忆、不隐藏会话记录（用户 09-07 决定）

2026-09-07，taiwan Mac，bundle d94e93dc，后端源码 main，gpt-5.6-luna。

| 轮 | Memory | 步骤 | 结果 |
|---|---|---|---|
| r6 | 0.6.21（仅去掉反向 MEMORY 目标） | 全新 userdata；「请记住：我喝咖啡只喝美式，不加糖。」→ 真实模型提取 semantic「美式」→ UI「忘记这条记忆」（suppression 1 条，target memory）→ 刷新/关闭 | 记忆已忘；**主对话仍显示为空**——定位到 `duplicate_source_matches` 仍按 MEMORY 指令的重复来源别名拒绝 evidence（在库副本上复现：READ denied，directive 同一条） |
| r7 | 0.6.22（duplicate-source 只用于记忆候选） | 同一 userdata 重开 | 主对话恢复显示「请记住：我喝咖啡只喝美式…」与助手回复；记忆面板「当前页没有可展示的认知记忆」 |

结论：新决定在原生 UI 上成立——忘记记忆后，记忆消失、会话记录保留。Memory 0.6.22 源 `67b176d0`，wheel `dafdc447…`，Host 已 pin（`b75188bb`）。

| 原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-s2ufq9bf/launch.json` | 24ace592fc728caed0d1f228e332e936471e0b2c915cfe6368f920c7f801e095 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-s2ufq9bf/native.log` | 9115bea4c547917b79cae0e71f8dd7f2ffea3b4647442a6c680f3c423fde13f1 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r6/resource.json` | 243eea732b09cb7e2cbc8d84dfc4de6494c3355e792f7c8880dac63d081c8d33 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-zei6quv2/launch.json` | 6174d534dde66475983cf8178870b9e170d05bbd501a2dca284ccc87d5994f9a |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-zei6quv2/native.log` | f7844c65c1bd917f6d439c9f93d24ad4a12554325edffd674476ed18211927df |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r7/resource.json` | 4bef8673e29547bb1e797a3055b27a1162d77b194c3762b71781385b6390bf02 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-s2ufq9bf/userdata/data/human_memory_v7.db` | f5f1742fb900624daf8fed7722e8a4d61ef7b0742db8cfbba081f7aeecc452f4 |
