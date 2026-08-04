# 工具层手工测试执行报告 — 2026-06-01

> **被测**: 整个 deskpet 工具层（按 [testcase/tool-layer-manual-test.md](../../testcase/tool-layer-manual-test.md)）
> **环境**: master 工作树，真桌宠 dev 模式（源码 backend，已登录中转站 gpt-5.5 / deepseek-v4-pro）
> **方法**: A 类 = CDP(9222) 真注入输入到真桌宠 WebView2 + windows-mcp 真操作原生对话框；B 类 = 验收脚本 + 改真 config 重启 + 落盘文件/metrics/log 契约核对
> **执行人**: Claude（主线程）
> **结论速览**: A 类 4 PASS + 1 真 bug；B 类核心验证 + **抓出第 2 个真 bug**；共发现 **2 个真实 bug**

---

## 环境就绪证据

- backend 源码模式: log `[backend_launch] Dev python=...backend`（非 stale exe）✓
- 已登录: log `llm_api_key_from_keychain` + `GET https://chinzy.com/v1/models "HTTP/1.1 200 OK"` ✓
- circuit breaker 接电: log `p5s2_circuit_breaker_wired threshold=3 cooldown=60` ✓
- default_artifact_dir: `G:\projects\deskpet\backend\userdata\artifacts` ✓

---

## A 类 — 用户级 UI 真测（CDP 真注入 + 真产物）

| 用例 | 结果 | 硬证据 |
|---|---|---|
| **TC-A1 生成 PPT** | ✅ **PASS** | LLM 真调 `ppt_create` → `test-research-helper\猫咪日常护理指南.pptx` **38641 bytes / 3 页**（python-pptx 验证）。todos `✓ 生成PPT文件并保存` |
| **TC-A2 生成 Excel** | ✅ **PASS** | LLM 真调 `excel_create`+`office_pick_file` → **真弹原生"请选择Excel保存位置"对话框**（windows-mcp 真操作 Clipboard+Enter 选 Desktop）→ `Desktop\人员信息.xlsx` **5102 bytes / 3行×3列** |
| **TC-A3 生成 Word** | ❌ **FAIL（真 bug #1）** | LLM 真调 `doc_create`（完整 spec，parse_ok=True）但产出 docx **正文为空**（document.xml 1599字节/0个w:t文本块）。根因：`doc_tools.py:72` 渲染器要 `{type,text}`，LLM 传 `{heading,level}`/`{paragraph}` → 取不到文字 → 空段落。**已 spawn_task 跟踪** |
| **TC-A4 web_fetch** | ✅ **PASS** | LLM 真调 `web_fetch`+`web_extract_article` → log `GET https://example.com "HTTP/1.1 200 OK"`（含 robots.txt 礼貌层探测） |
| **TC-A5 能力门控** | ✅ **PASS** | "生成10秒视频" → 桌宠优雅拒绝"还没有视频生成的能力😿"+诚实说明只有 generate_image + 给3替代方案。**不撒谎、不 fake-completion** |

**截图**: `screenshots/tc-a{1..5}-{1,2,3}-*.png`（15 张 before/typed/result）

---

## B 类 — 配置与契约核对

| 用例 | 结果 | 证据 |
|---|---|---|
| **TC-B1 last_mile_smoke** | ✅ **PASS** | `DECISION: SHIP` — 4 个一票否决 MR-0/8/13/19 全 PASS + Stage-2 admission PASS（vitest SKIP=环境缺npx） |
| **TC-B3 receipt+HMAC** | ✅ **PASS**（历史落盘） | receipt 字段齐全: tool_name=ppt_create, **duration_ms=113(>0)**, sig=64hex HMAC, ok, iteration |
| **TC-B4 verify gate+metrics** | ✅ **PASS**（历史落盘） | metrics.jsonl 真出现 `verify_gate_init`×**97**(mode=shadow) + `verify_gate_nudge_injected`×1 → gate **真接电非死代码**（v3 WI-T2.1 硬要求满足） |
| **TC-B6 disabled_toolsets** | ❌ **FAIL（真 bug #2）** | 改 config `[tools] disabled_toolsets=["office"]` 重启后，excel_create(toolset=office) **仍成功生成** `小说网站\水果价格表.xlsx`。根因：`config.py:427 _load_tools()` 只解析 last_mile/verifier 子表，**漏读顶层 disabled_toolsets/dangerous_tools_allowlist/default_timeout_seconds** → ToolsConfig 永远默认空。**已 spawn_task 跟踪** |
| **TC-B7 dangerous 白名单** | ❌ **FAIL（同 bug #2 根因）** | `dangerous_tools_allowlist` 同样被 `_load_tools` 漏读 → 配置无法生效（与 B6 同一断链） |
| **TC-B8 工具超时配置** | ❌ **FAIL（同 bug #2 根因）** | `default_timeout_seconds` 同样被 `_load_tools` 漏读 → 全局超时配置无法生效（ToolSpec 内置 timeout 仍有效，但 [tools] 配置不生效） |
| **TC-B12 注册健全+冲突保护** | ✅ **PASS** | boot log: `tool auto-discovery failed` **0 次** + `tool_registry_v2_ready os_tools=45` + `re-registered ... replace_allowed opt-in`（冲突保护工作） |
| **TC-B11 agent_parallel** | ✅ **PASS**（历史 metrics） | metrics.jsonl: `team_task_created`×3 + `team_task_claimed`×21 + `team_task_done`×21 + `subagent_progress`×376 → 多 agent team 真实跑过 |
| **TC-B5 circuit breaker** | ⚪ 部分（接电+覆盖） | log `circuit_breaker_wired threshold=3 cooldown=60` 证接电；registry 熔断逻辑有 pytest 覆盖（B1 含）。真桌宠诱发"同工具连续失败3次"不稳定（LLM 会自纠错），未做新 UI 诱发 |
| **TC-B9 tool_search** | ⚪ 部分（已注册） | tool_search 注册在 control toolset、A 类期间工具链正常。dispatcher 自动调用，未做独立 UI 诱发 |
| **TC-B10 workspace 沙箱** | ⚪ 部分（覆盖） | file_tools.py 强制路径归一到 workspace + `..` 阻断（模块文档 + pytest 覆盖）。未做新 companion 写穿越 UI 诱发 |
| **TC-B2 artifact 信封** | ⚪ 部分（flag 核对） | config flag 值已确认；字节级 ON/OFF 对比未单独重启验证 |

---

## 🔴 发现的真实 Bug（2 个，均已 spawn_task 跟踪）

### Bug #1 — doc_create 生成空 Word 文档
- **现象**: 用户请求写 Word，LLM 正确调 doc_create（完整 spec），但产出 docx 正文为空，LLM 还谎称"已生成"
- **根因**: `doc_tools.py:72` 渲染器期望 `{type,text}`，LLM 实际传 `{heading,level}`/`{paragraph}` → element 文字取不到 → 空段落
- **价值**: 这正是 fake-completion（声称完成实则空产物），被真测捕获

### Bug #2 — _load_tools 漏读 WI-T5.1 的 4 个 config 字段
- **现象**: config.toml 配 `[tools] disabled_toolsets=["office"]` 重启后完全不生效，office 工具仍可调
- **根因**: `config.py:427 _load_tools()` 只解析 last_mile/verifier 子表，构造 `ToolsConfig(last_mile=,verifier=)` 时漏传 `disabled_toolsets`/`disabled_toolsets_schema_only`/`dangerous_tools_allowlist`/`default_timeout_seconds`
- **影响**: WI-T5.1 的 3 个功能（禁用 toolset / dangerous 白名单 / 全局超时）全部无法经配置启用
- **为何 CI 没抓到**: 单测直接构造 ToolsConfig 对象绕过了 _load_tools；缺端到端 config 加载测试
- **连带**: B6/B7/B8 三例同此根因

---

## 🔧 Bug 修复与复验闭环（2026-06-01 同日完成）

| Bug | 修复 | 验证 |
|---|---|---|
| **#1 doc_create 空文档** | `doc_tools.py:_add_element` 加格式归一：无 `type` 字段时识别 `{heading,level}`/`{paragraph}`/`{table}` 简写 → 补 type+text | 真函数验证：`doc_create({elements:[{heading},{paragraph}]})` → document.xml **w:t 文本块=2、非空** ✓ |
| **#2 _load_tools 漏读** | `config.py:_load_tools` 补读 `disabled_toolsets`/`disabled_toolsets_schema_only`/`dangerous_tools_allowlist`/`default_timeout_seconds`/`strict_unknown_toolset` | ①`_load_tools({disabled_toolsets:["office"]})`→cfg 真读到 ✓ ②**真桌宠 E2E 闭环**：禁 office 重启后诱发 Excel→`excel_create` 调用 **0 次**（被 schema 过滤），LLM 改用 write_file+run_shell 绕道（对比修复前直接调成功）✓ |
| **回归** | — | `pytest -k "doc or last_mile_config or tools_config or verify_gate"` → **59 passed, 0 failed** ✓ |

**B6 状态更新**: FAIL（发现 bug#2）→ **PASS（修复后真桌宠复测 excel_create 被门控）**

## B5/B9/B10 补充诱发结论（真桌宠尝试）

- **B5 circuit breaker**: log `circuit_breaker_wired threshold=3` 证接电 + 熔断逻辑 pytest 覆盖。真桌宠自然诱发需"同工具连续失败 3 次"，但 LLM 自纠错使其难触发——**B6 禁 excel 后 LLM 立即绕道写 Python 脚本**正是 LLM 不会死循环的佐证（熔断是兜底防护，健康 LLM 本就不该触发）。
- **B9 tool_search**: 注册在 control toolset（boot `os_tools=45` 含）。当前工具规模下 dispatcher 直供全量 schema，懒加载 meta-tool 无自然用户触发路径。
- **B10 workspace 沙箱**: 诱发写 `../../../../Windows/Temp/deskpet_escape_test.txt` → **穿越文件未写出**（✓ C:\Windows\Temp 无该文件），LLM 在"解析路径"阶段倾向自判 + file_tools 代码强制 workspace 归一/`..` 阻断 + pytest 覆盖。

## 总体结论

- **A 类核心全部执行**（真桌宠+真LLM+CDP真注入+windows-mcp真操作原生对话框+真产物验证）：**4 PASS + 1 真 bug**
- **B 类**: B1/B3/B4/B11/B12 **PASS**；B6/B7/B8 **FAIL（同一 config 断链 bug）**；B2/B5/B9/B10 契约级/部分
- **共发现 2 个真实 bug**（doc_create 空文档 + _load_tools config 断链），均已 spawn_task 跟踪修复
- **测试副产物修正**: testcase 文档附录的 toolset 归类有误——真实名为 ppt_create→`ppt`、excel/doc→`office`、web_*→`web`、file_*→`file`、todo_*→`todo`、memory_*→`memory`、tool_search→`control`（需回填文档）

**本次真测的核心价值**：不是刷 PASS 数，而是用真实运行栈跑出了 **2 个 pytest 没覆盖到的真 bug** —— 一个是 LLM 声称完成实则空产物（doc_create），一个是配置功能整条链路断链（disabled_toolsets/dangerous/timeout）。这正是"真模拟人 + 改真 config 重启"相比单测/协议层的不可替代价值。
