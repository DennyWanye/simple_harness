# Slice B black-box oracle — closed-ingress product adapters

> 冻结对象：产品在临时user-data中从vendored exact wheel组装的SDK stack；production ingress必须保持
> closed，不允许借旧Harness完成任何case。

| ID | 输入类 | Root runs | PASS oracle |
|---|---|---:|---|
| SDK-B1 | exact wheel/storage identity | 1 | installed origin/version/SHA一致；SDK execution/product state/Session三个DB互斥；schema reopen、rollback和owner fencing通过 |
| SDK-B2 | startup readiness/failure retry | 2 | 半初始化对象不可见；失败逆序close；fresh generation重试成功；三个入口观察同一ready slot |
| SDK-B3 | Provider/Tool/reconciliation | 2 | `deskpet.tools`正常import零provider/注册/外部副作用/旧Workflow；pre-cutover 79项manifest精确映射为77 SDK Tools+2 required Workflow profiles；77项schema全量compatibility清单与old/new hash齐全，开放object只变closed或typed bounded map；六类真实handler已invoke；metadata digest一致且零skip；坏provider阻断ready；malformed/unknown/crash不盲重放 |
| SDK-B4 | permission/personal/capability | 2 | 每个跨DB写点kill/reopen后saga收敛且receipt hash匹配；S5前Tool=0、S5后≤1；cancel/expire/revoke/permanent conflict不遗留active orphan grant；无matcher；伪造candidate失败；build成功后同Run active |
| SDK-B5 | DeepResearch v7-sdk1/PPT v2 host definitions | 2 | active仅v7-sdk1/v2；旧engine checkpoints明确不迁；真实handlers与exact typed Ports经同一SDK Runner；PPT pure interrupt拆分；DeepResearch bounded terminal ref可打开且hash一致；continuation/artifact/restart无重复 |
| SDK-B6 | product conformance | 1 | product Host从wheel运行四suite全PASS；required零skip；report artifact SHA等于vendor bytes |

B2首次失败后不得在失败Runtime上再次`start()`；B4必须覆盖product prepare、SDK decision bind、product
decision bind、SDK effect prepare、product effect bind、SDK handoff intent、product handoff commit、SDK
handoff commit、handler return、两侧settle前后的全部crash窗口；B5至少一个DeepResearch和一个PPT run跨进程重开。
