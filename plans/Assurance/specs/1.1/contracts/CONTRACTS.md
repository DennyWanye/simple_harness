# 活动协议1.1

本目录替代1.0活动Schema。`common.schema.json`唯一内部Ref/pin/read_item词表；不改变SDK公开TypedRef。变更layout的review/check/record/use bindings用v2，新policy/invocation/disclosure/restore和HostDTO用独立v1。

`structural-fixtures.json`是结构正反例，不是实际回执；`six-purpose-fixtures.json`同时锁定purpose/subject组合与父round/子invocation结构。真实issuer/tenant/input/费用/validity必须由SDK原来源核验。

运行codec先字节/UTF8/重复键/深度限制，再本地Schema和semantic guards。`schema_support.py`实现本包使用的JSON Schema子集，不伪称完整Draft通用实现；本包还可用jsonschema作独立meta/conformance检查，但SDK运行不依赖它。

所有字段source映射为JSON pointer与展开子树hash，修改嵌套enum或字段必须重生成、独立审核；不能靠删除checker使其通过。

Schema绝对ID统一使用`https://schemas.simpleharness.invalid/assurance/1.1/<file>`作为离线注册命名空间，不是需要联网访问的网站。消费者应预加载本包schema registry，禁止运行时从网络补未知引用。
