# C01-20 首次真实评分：FAIL

2026-09-07，Host2c02be03，H079/M619/S0313；含strict:false与明确omit错误反馈，原C01-10/13未重跑。

原gold要求同一semantic类型取得A/B并正确比较日报最多3条、周报最多5条。实际4次Provider请求，前三次均调用context_route却带无关reuse_workspace_of（“d”或空格）及全零expected_source_hash；三个实际路由均拒绝，无A/B进入模型。最后如实回答无法确认数值，未伪造约定；原gold **FAIL**。原提议合计semantic/episode/procedure，required类型提议命中1/1、extra2，不能把类型提议算实际召回成功。

业务COMPLETED，公开trace完整；PG71822自然exit0，51.880s、峰1089472KiB、minDisk2338MiB、remaining[]、cleanupnull，无资源停止。reported usage：{'input_tokens': 12942, 'output_tokens': 1266, 'total_tokens': 14208}。

本地通信组合控制仍是有效的限定证据，但实际模型结果说明显式strict:false+omit指导未解决当前环境；不声称线上relay如何转换已被证明。暂停继续扩跑同一已知故障路径，补SDK可选字段JSON null的schema/参数验证支持及Host明确无值语义；真实非空source/workspace绑定继续精确校验，不把字符串占位符当空值。

官方规则参考：[Function calling / Strict mode](https://developers.openai.com/api/docs/guides/function-calling)。Chat Completions与Responses缺省不同，strict格式的可选字段需可表达null；文档不是本次relay运行证据。既有SDK单一type验证不支持nullable联合，后继实现由同一源版本控制，新wheel只统一构建一次。

240条历史已首次尝试3个不同case，0通过。修复固定新候选后会显式复验失败case，分别记录候选与旧FAIL；不隐藏重试、不以新case替代失败case，也不将跨版本结果拼成一次绿色评测。其它237条尚未实际评分。

| 本机原始证据相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r3/C01-20/review-packet.json` | `ffce5562584269c387020aec68042f9ac7de6e4bcdb98d9c4a2d1311b3bd98c5` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r3/C01-20/observation-trace.json` | `457decc8db2486ac04fb8cacfc289276c15d0b7ad134f98e9df9bb79b48c3163` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r3/C01-20/observation-route_audit.json` | `ef0357567944d8d1c424c0b01fd6385c4ac5a374ed3cda579c5cf76b6e84ec7d` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r3/C01-20/observation-route_effects.json` | `dea7146bfae3b3f1848101f1014822d941a83897acd25be74bf9302ee615d9cf` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/resource-r3/resource.json` | `02440c41ff781f7291f4b13570e0feda3ef1405b211f1e8d65a065a0e616c2d5` |
