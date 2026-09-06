# HTTP 拒绝原因：保留最小安全诊断

2026-09-07。主eaa72b51/H0710/M619实际C01-20 r4 HTTP400：已读原observation-trace，唯一provider为failed/provider_request_rejected，response_json=null。SDK providers/openai_compatible.py在_post_once156先_raise_for_status，400只附status_code，不附private_cause；Host_parse_response钩子没执行，外层日志仅类别/状态。故现有r4无法恢复结构化body，不能据此断言nullable或relay根因。原r3/r4保留，不自动重发。

packet prediction_observation_complete=false时extra0是缺观测下的值，不能解释成观测到零额外类型。本叶只记录这个限制，不扩scoring状态或重测。

Host _ProductOpenAICompatibleProvider只为现有借用client.post包一层返回后诊断，不改变SDK取消、状态异常、parser或重试合同。原HTTP>=400响应在SDK抛错前，仅记录request opaque ref/status、body字节数/SHA256。body≤64KiB且JSON.error是对象时，白名单选择code/type/param/message；用原SDK SecretRedactor先脱敏再截断（128/128/256/1024字符），JSON转义控制字符。非JSON/过大响应仅hash与长度，不落完整body、headers、key或任意额外字段；不赋error body新的公共权限或语义。日志失败不替换原Provider错误。

SDK/nullable/参数/模型预算保持不变，无SDK新包。唯一新增fakeHTTP组合控制覆盖结构化、非JSON、过大400；核活跃key脱敏、边界裁剪、extra/header不入日志、原异常与单请求不变、借用client仍开启。当前源码候选NOT_RUN；未来真实错误只可通过主明确安排的单次诊断捕获，不自动发LLM。
