# DeepResearch v6 答案契约稳定性手工测试

> 执行日期：2026-07-18；GLM-5.2 1M 增量复验：2026-07-19
> 环境：Windows 11，source Tauri，隔离 userdata  
> 证据：`plans/2026-07-17-deepresearch-answer-contract-stability/evidence/t13-release-20260718/`  
> 结论：5/5 PASS；当前基础/预分析模型为 `sf-glm-5.2`，1M 临时画像与真实 SC-STATS-2 增量复验 PASS，GLM 请求全部 HTTP 200。

## 前置与判定纪律

- 只启动 Tauri，由它启动唯一 backend 和 Vite；日志必须出现 Dev python/backend dir。
- 所有 UI 动作先记录坐标、动作和期望，再用 Computer Use 真点击/真输入。
- UI 结果必须与 workflow DB、delivery、control journal 和 Tauri/backend 日志交叉核对；禁止用协议直注替代 UI。

## TC-01 基础模型切换

1. 启动隔离 userdata；期望有效模型为 Relay 别名 `sf-glm-5.2`。
2. 打开 Context usage；期望有效上限 950,000、compaction 750K、recall sweet spot 384K，启动日志解析名义窗口 1,000,000。
3. 在真实聊天框与 SC-STATS-2 中触发模型；期望所有 `chat_stream_with_tools` 选择 `sf-glm-5.2`，Relay completion 全 HTTP 200、无 DeepSeek/402。
4. 结果：PASS。2026-07-19 源码 Tauri 重启和 run `8109026887b64b309723c004e35c85af` 满足上述条件；隔离模型/上下文回归 `58 passed`。

## TC-02 completed：2024 国家统计局 exact 三次

1. 真输入“深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。”
2. 期望每次只显示本地化事实 `年末总人口：140828 万人`、`全年出生人口：954 万人`，带国家统计局 canonical 引用，不出现 raw JSON、`unit=`、`scope=` 或 `definition=`。
3. 结果：PASS。最终 release identity run `e58b02815a514577a2a9df2789c6978f`；更早三次健康网络样本为 `492bdf52cf714616878957a57be55241`、`7eb29c9f22e74aecb4a614acf746795c`、`a895f7e8f87e45d0b9997d2eff5a1751`；全部 delivery once。

## TC-03 partial：2019 双指标缺一

1. 真输入 2019 年总人口与出生人口问题。
2. 期望系统只发布已准入的 `全年出生人口：1465 万人`，状态为 `partial`，动作是 `continue_research`，不得编造总人口。
3. 结果：PASS。最终 release identity run `2ed15e0095ee41fa95e7bb04b297d652`，workflow final 和 UI/DB 一致，delivery once。

## TC-04 insufficient + generate-now 双击

1. 在 generic research 运行中，对“立即用现有证据生成”按钮连续真点击两次（执行坐标 `(174,267)`）。
2. 期望只有一个 durable command，依次出现 accepted、observed、settled、consumed；最终只有一个 `insufficient_evidence` 回答和一张完成卡。
3. 结果：PASS。重启后最终 release identity run `eeab90bab2d64cf7ab076a0d6514f765`；command `9582bb5c288aec6bbaf452fb88405c6e1527629b6ebde7713ef7292fdd8d046c` 唯一并完成 accepted → observed → settled → consumed；控制后无第二次 DeepResearch semantic LLM 调用，全部 delivery once。

## TC-05 重启与历史恢复

1. 完全关闭并用同一 userdata 重启应用。
2. 依次点击 2019 partial、generic generate-now、2024 completed 三个历史 session。
3. 期望 partial 答案/动作、单个 insufficient 卡和完整中文 exact 事实均恢复；无重复卡、重复答案或仍可点击的旧 control；当前有效模型显示 `sf-glm-5.2`，Context usage 有效上限 950K。
4. 结果：PASS。`tauri-final14.stderr.log` 记录 `recovered_deliveries=0`；最终 identity partial 历史截图 `ui-partial-2019-release-after-restart.jpg` 与 DB 一致，无重复终态或仍可点击的旧 control。

## 汇总

| 用例 | 结果 | 核心证据 |
|---|---|---|
| TC-01 模型切换 | PASS | 启动/聊天日志选择 `sf-glm-5.2`；名义 1M、UI 有效 950K、compact 750K；HTTP 200 |
| TC-02 completed exact ×3 | PASS | 三个 run、NBS 引用、本地化答案、delivery once |
| TC-03 partial | PASS | `answer_status=partial`，只发布 1465 万人 |
| TC-04 generate-now 双击 | PASS | 单 command 完整 journal、单终态 |
| TC-05 restart/history | PASS | final14 同 userdata 恢复，无重复 |
