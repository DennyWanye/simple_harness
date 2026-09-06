# 本轮输入与Procedure共同源码组合

最后更新：2026-09-07。固定Host80764c13、Memorya15c7be（业务a28a857）已获Dirac限定ACCEPT。组合使用真实签名SELF配置、实际当前USER claim、独立procedure-source S1与公开DRAFT创建，同Manager检查两项前[True,True]，实际publicforget后[True,False]且当前输入仍allowed；epoch递增，requesthash保持，snapshothash变化，Host CurrentInputJournal两条实际返回均captured_bound。不是把invocation_input_allowed当整批授权。

r1到双项可见后，测试误用不存在all_visible属性失败；r2删除两条冗余不存在字段断言，保留逐项精确比较，1PASS0.82s。无产品重改、无整套复跑。r1 PG49343exit1/1.513s，r2 PG49417exit0/1.514s/峰162816KiB，均remaining[]cleanupnull。

H079/S0313既有安装174/116成员精确，Memory为共同候选src overlay，184加载模块按各自源或target匹配。不是Memory installed/native/全Procedure通过。主合并保留signal/current-input/procedure同principal、contextpage、v3 draft各层聚合，独审已核。0.6.19新制品及当前安装组合另验。

## 本机ignored索引

- `.local-test-evidence/2026-09-07/current-input-draft-combination/run.py`：`7213ee69f9dc684e7ca4100a34e49cae7d57626565d48f4b8fd645962dd15847`
- `.local-test-evidence/2026-09-07/current-input-draft-combination/r1/command.log`：`efe8fb7368af7f5bd1450047c859d601788b6b095d2633bc91fe36f2b294ea29`
- `.local-test-evidence/2026-09-07/current-input-draft-combination/r1/resource.json`：`d6f1429c0137bf44357c08c16f957c34066e32dabd5f4fcc2970b056b6a986dd`
- `.local-test-evidence/2026-09-07/current-input-draft-combination/r2/command.log`：`f110ea65f6d33cdc6322b155f056abd01a7abd63e18b0f61e05569402024ad9d`
- `.local-test-evidence/2026-09-07/current-input-draft-combination/r2/resource.json`：`de3ed8c9ed4a529e52e61f388732a53f60c634775603819163b3e9bcc5973a04`
- `.local-test-evidence/2026-09-07/current-input-draft-combination/r2/identity.json`：`5529cc40e1983d587837d6032d0bebd052aef179639d12a9ad8be34a82e94f5c`
