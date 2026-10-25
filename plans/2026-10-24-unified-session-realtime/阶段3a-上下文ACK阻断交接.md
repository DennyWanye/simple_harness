# 阶段 3a：真实消息 ACK 发布阻断交接

最后更新：2026-10-25。执行：Codex。本文不是独立评审或放行回执。

接续状态：已收到并采用 Service SDK 0.3.15，原六项确认反例 **6 通过 / 0 失败**，
原两个红灯已转绿。新增受跟踪确认回归与既有回环合计 14 通过，依赖及干净环境检查通过。
真实联合主链仍有单独阻断，详见阶段 3a 记录第十五节；等待第五次复核后发版 opt.218。
下面保留 0.3.14 的交接历史，不作为 0.3.15 当前状态。

## 当前边界

第四次独立复核结论为不可发，回执 FAIL，报告提交 `ae3b4fef5`，受评源码 `f8099ba53`。
R4/R5 修复通过该轮检查，但历史回灌、恢复及结果播报仍被真实消息确认合同阻断。
因此没有执行 release_sdk_opt.sh，没有更换 Host opt.217，没有正式轮子或发版成功时间。

当前 Host 仓库内只有 Service SDK 0.3.13 和 0.3.14 两份候选输入。0.3.14 已安装源码与
校验过的轮子一致；问题落在其 Qwen 适配器与 ManagedRealtimeSession 的确认合同，
不是工具目录未确认。用户只授权在本 Host 仓库内工作，不能直接修改外部 Service SDK
仓库；也不能靠修改 site-packages、重包同版本 wheel 或桥内兼容适配器来假装修复。

## 已确认的出入站事实

原始索引位于 `.local-test-evidence/2026-10-25/unified-session/phase3/live2/`。
A10-run1 的两次 message 请求和回显分别是 `event_947399… → item_OwPM…`、
`event_f3d9fe… → item_HAhi…`。回显仍为 completed/user/input_text，但 ID 不同。
Service 0.3.14 的 QwenOmniAdapter 仅按原 item.id 查找待确认消息；没有匹配到时不返回
context_ack_id。ManagedRealtimeSession 等待结束后报：

`context acknowledgement timed out`（timeout；诊断 operation_kind=context.ack_timeout）。

不能据此判断 ID 改写发生在中继还是上游。不能把收到任意 conversation.item.created
当成原消息已经确认，也不能提前 response.create 或跳过恢复资料。

## 本仓库内可重现反例

脚本及 XML 位于忽略目录 `phase3/post-review4/`，没有真实联网或音频，不是新真线路通过证据。
测试使用真正的 Service SDK 0.3.14 QwenOmniAdapter、ManagedRealtimeSession 和 Host 桥；
业务 Host 与音频使用离线端口，媒体 SDK 仅替换网络连接，回显方式取自上述真线路事实。

```sh
PYTHONPATH=sdk/simple-harness-sdk/src:sdk/simple-harness-sdk/tests/orchestrator/host \
sdk/simple-harness-sdk/.venv/bin/python -m pytest \
  .local-test-evidence/2026-10-25/unified-session/phase3/post-review4/test_context_ack_contract.py \
  -q \
  --basetemp=.local-test-evidence/2026-10-25/unified-session/phase3/post-review4/red-tmp \
  --junitxml=.local-test-evidence/2026-10-25/unified-session/phase3/post-review4/red.xml
```

已执行：**4 通过 / 2 失败**，两项失败均为上述 timeout，无收集错误。

| 用例 | 当前结果 | 修复后要求 |
|---|---|---|
| 回显使用提供方 item ID 的历史回灌 | 失败 | 确认正确的历史资料后绑定就绪，不触发自动回答 |
| 回显使用提供方 item ID 的播报 | 失败 | 真实确认后才发一次 response.create |
| 原 item ID 不变的历史及播报对照 | 通过 | 保持通过，证明不是夹具所有确认一律失效 |
| 错误正文的回显 | 通过 | 必须拒绝，不能触发 response.create |
| 错误角色的回显 | 通过 | 必须拒绝，不能触发 response.create |
| 未完成状态的回显 | 通过 | 必须拒绝，不能触发 response.create |

现有 Service 联合回环基线另为 **8 通过 / 0 失败**。这说明“旧回环全部通过”不能覆盖
真实服务端分配 item ID 的情形。两个红灯未关闭，不能宣称本轮已经修复消息 ACK。

## Service SDK 修复候选需要满足的合同

1. 在原始确认仍可验证的前提下支持提供方分配的消息 ID，保留原调用的确认关联。
2. 正文、角色、类型、完成状态不一致时仍拒绝；不能只凭文本或 FIFO 猜测 ACK。
3. 迟到、重复、同内容消息及跨 generation 事件不得误确认下一次注入；补相应正反例。
4. trigger_response=False 的历史和未播事实注入不自动回答；True 只在真实 ACK 后续答一次。
5. 保留工具结果回写确认，不引入重复 session.update，不重放音频或任务命令。
6. 提供新候选版本、轮子路径和 SHA256SUMS；不要修改旧 0.3.14 的身份或将 Host 安装补丁
   作为 Service 正式修复。Service 保持提供方中立，不反向依赖 Host 桥。

也可以由线路在真实回显中保留原请求 item ID 来修通合同；这需要新的实际出入站证据，
不能只宣布配置已改。若此方式修复，明确记录上述 ID 改写反例的适用边界，不要求猜测 ACK。

收到修复候选后，先跑上述六项及现有八项回环，再用原录音重验 A08～A11、A13 和干净环境
完整示例。A08/demo 的任务仍 PLANNING、A10-run2 未命中委派是另外的未完成链路，不能
在 ACK 修复后自动算通过，也不改提示词凑结果。

本轮清单已重新生成，848 个源码哈希一致，仍 NOT_RUN。阻断未解除；需要修通最终组合并
取得第五次独立复核 PASS 后再进入 opt.218 发版与 Host 钉版，不自签、不 push。
