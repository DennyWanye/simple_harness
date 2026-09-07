# 原生 r12：Procedure 使用链第三次（Host 9b84aac1，M0.6.26，含 F06/时钟接缝）——步 1–4 通过，步 5 因"当前指令被当成历史"未执行

2026-09-08 上午（屏幕解锁后）。bundle `SimpleHarness Memory Verify 9b84aac1p18120.app`，installed H0.7.10 / M0.6.26 / S0.3.13，全新 userdata，gpt-5.6-luna 主通道，auto 模式，System Events 驱动。

| 步 | 结果 |
|---:|---|
| 1–3 | 「松柏记录」active/unbound、「云杉归档」draft 落库；改写无召回；零告警 ✅ |
| 冷重启 | 同 userdata 重开，`reconcile.recovered` 1，窗口正常 |
| 4 | `procedure_discover` 真实返回草稿（audit 2 次），回答 draft/第 1 版/低风险/未授权，无文件效果 ✅ |
| 5 | `context_route(create_new)` 建「松柏九月」✅（无 `after` 缺参、无 history 拒绝、无 provider 超时）→ 但第二次 provider 调用回答「已看到这段历史记录。它不是当前指令，因此我不会执行其中的“松柏九月”任务」，Run 正常结束，未 `procedure_discover`/`procedure_use`、无文件。**FAIL（Context 组装/提示框架）** |

根因（物理请求 `execution-v6.sqlite3 provider_invocations` rowid 6/7）：上一轮因果组被压成一条 user 角色消息 `Historical conversation data (not instructions): {…}`，紧接着才是当前指令（普通 user 消息，无标记）；模型把两者视为同一段"历史"。r10 的 `after` 必填、r11 的 F06 超时停摆在本轮均未复现（修复有效）。已交子代理修当前指令定界/标记（HM-AC-6 动态 Context 面），修后 r13。

| 原始证据 | SHA-256 |
|---|---|
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-9b84aac1/primary-ui-4c648l07/native.log` | 4f9c1aea087c986bb2a1961f9ff5ed0f7bbc019fe86d1b944d299a2f4d9f71ed |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-9b84aac1/primary-ui-75q8mv1m/native.log` | 627a0bdf9d9a51ff6d6df931385cf226b62202362042833942a7104bfa70e7e8 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-9b84aac1/primary-ui-4c648l07/userdata/data/human_memory_v7.db` | 153535cc04e70754fdaa5350c50b9f0ddd03c3ead76d387927082e878407384d |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-9b84aac1/primary-ui-4c648l07/userdata/data/state.db` | f745b817555f5250049c925dd5c12c2460b2fbc64c989bafc2c06eeb19c50f42 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-9b84aac1/primary-ui-4c648l07/userdata/data/simple-harness-sdk/execution-v6.sqlite3` | c45de8f55fbb69d326aeefef9dfad2a12c242dedc9902ddb49c8053ea7275f84 |
| `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-9b84aac1/resource-r12b/resource.json` | c2f07c45b886d43131b3c78bfd5f74d41fbd639e30172171f2d50b28b77a7ce0 |
