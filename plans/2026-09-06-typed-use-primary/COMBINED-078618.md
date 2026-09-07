# H078/M618 当前组合结果

更新：2026-09-06。固定测试源码1491309f，包含已审A7展示/ACK与Procedure v4创建、旧v3响应恢复。历史分页后继f28d3fbe未包含在最初5项内，后续必要组合见下。

5项必要组合检查通过，耗时4.82秒：

1. 真实main factory采用SDK驱动选择，并检查A7 schema52/ACK装配。
2. 实际direct_standalone路由完成提醒ACK与终态消费。
3. Procedure `[3-True]`：旧v3响应已持久化后发生普通异常，切换v4配置恢复仍沿用旧语义，零额外Provider调用。
4. Memory候选精确wheel身份验证。
5. Memory依赖锁与候选身份一致。

使用原Python及新小型installed target，无新venv；H173/M84/S116个包成员逐字节匹配各vendor wheel，201个已加载SDK模块全部来自此target。测试采用确定性Provider，并非新的原生或真实模型质量证据。没有重复历史全套检查。

固定依赖：

- Harness 0.7.8，source f778cba9c5ee599e7ff5ac55796d0f331d215f62，wheel SHA256 `5aa1112803b5b617142b015f4989c679b455f34444f512a4ede957161b41138e`。
- Memory 0.6.18，source d8d80d5c00f489e7d85a7d1df613f73e489b4cc2，wheel SHA256 `010b4281c7b1149e32858939119262d97cedb8528ddd8bb62b87940dcefbe1cb`。
- Service 0.3.13不变，source74a622572602bf3b6973f5d8a55c09bf62ff08ba，wheel SHA256 `26205f89854e27bd7ed8cbd6f7ac1f6b621603f973a081823bc6b707ae0784a8`。

安装PG2152与测试PG2168均exit0、remaining=[]、cleanup_error=null。测试峰值418528KiB，最低可用磁盘4843MiB，默认共享锁已释放。

复现命令：现有 `primary-m0615/venv/bin/python` 调用 `scripts/run_resource_bounded.py --evidence-dir <本根>/r1 -- <同Python> -I -B <本根>/run_controls.py <本根>/r1`。原始文件仅保留于 `.local-test-evidence/2026-09-06/primary-078618/`。

| 文件 | SHA-256 |
|---|---|
| install.py | 4558b40efe5ee5cdb1f2e46702949b2fa18db151944aebc90444f6615ddc20fc |
| run_controls.py | 8038c2cb68b25350b476241b846e39c71b8f7d1a4f4c1aebc3e504057c648f30 |
| install-resource/resource.json | 88e9685729fbc36e8dfe5431f6472f8d9e15bad7f7a0f4d271c6226d31d700c6 |
| r1/command.log | 9a4db7c5ff73dad87a7597528874d6be6aa3b162f61c7c400cbf0bc9c57021e9 |
| r1/resource.json | 2343719b91c8e7168e969e2cab7be2923a9b8023017655422275c018c0505af5 |
| r1/identity.json | fdd29f2e3a9935494fc72438b40deac026dc0c0f69b4225d686b480dd21f21f2 |

Scope观察/适用性、真实事件来源、当前运行分页、两轮独立原生旅程、关系边和240条质量批次仍未闭合。未切换用户主checkout，未push/tag/release。

## 历史分页与A7合并后的必要交互

固定c9e1aebf，仅执行 `test_actual_history_page_and_physical_guard[allow]`，1PASS/4.71s。实际S1与公开SDK来源、首/续/尾页、错误hash拒绝、下一物理请求和重建stack依赖在H078/M618+A7组合通过；没有复跑旧history四项。PG3328 exit0、remaining=[]、cleanup_error=null，峰399792KiB，最低磁盘4780MiB。该结果仍是确定性HTTP，不是原生质量结论。原始根 `.local-test-evidence/2026-09-06/primary-078618/pages/`。

| 文件 | SHA-256 |
|---|---|
| run_history_composition.py | 1467bdcf962651c826f8ac242b12a78e999c90933a5753c2070d298cacccf370 |
| r1/command.log | 4fd07bcee9b5a8f0645ee8bc81e966b1fc7417a9a0ec85c8404455fe322cb09f |
| r1/resource.json | edec7a63c9eeb4b7c750d3a57c4627325eab41ea98e790fe00de385348b53c3b |
| r1/identity.json | 33e109a279b1796641bb433548a6ff03b5a7872ac150c16888753eff5ed899e5 |

## 当前运行分页与A7的装配交互

固定320a419e，执行真实main factory与 `test_actual_current_effect_page_and_physical_guard[allow]` 两项，2PASS/5.34s。合并冲突为构造器及调用处相邻关键字，保留 occurrence_coordinator 与 current_tool_projector。H078/M618当前组合的实际双大结果、父请求来源、分页后物理发送、闭合及重开依赖通过；不代表typed-consumed跨SDK全覆盖或原生。原未closed写Scope撤回来源时终态pending仍OPEN，已交原实现者修复。

PG3663 exit0、remaining=[]、cleanup_error=null，峰410960KiB、最低磁盘4760MiB，默认资源锁释放。仅针对新增合并接线与新SDK组合验证，不重跑旧page全套。

| 文件（pages根） | SHA-256 |
|---|---|
| run_current_composition.py | 48317a55dd626703c73ec7a384122d892e1d77ee9928778b559a4b275e1c3967 |
| current-r1/command.log | dbf8aa747a2544f8deb54f83995a9df7837645561d6640299550a36b50397109 |
| current-r1/resource.json | 3004bb2a3f76b20dd16cb79bcc645e36a73963cfb478b893fdaab86a13724786 |
| current-r1/identity.json | 33e109a279b1796641bb433548a6ff03b5a7872ac150c16888753eff5ed899e5 |

后续源码校准：current-r3精简fixture未注入main已有BoundClosureFallback。该红例证明该fixture的Host终态pending，不能外推默认产品也阻塞。现正在验证生产fallback的原未closed Scope撤回链及必要冷恢复；原记录保留，不用预关闭后的绿控替代。
