# verify_gate shadow→strict + code patterns(item ③)报告

> **日期**: 2026-06-02
> **被测**: 决策3"硬卡" —— code 模式 fake-completion 防护翻 strict + 补 code 场景
> claim patterns。
> **方法**: 单元测试(strict 拦/放逻辑)+ CDP 真机(真任务不被误杀)+ backend 日志。

---

## 实现(改动)

- **`verify/claim_patterns.yaml`** 补 4 条 code 场景 pattern(5→9）：
  - `zh_created_file` "已创建/创建了 X.py" → write_file / artifact_create / doc_create
  - `zh_modified_file` "已修改/更新了 X" → edit_file / write_file
  - `zh_tests_passed` "测试通过/所有测试已通过" → run_shell
  - `en_modified_file` "I modified X" → edit_file / write_file
- **dev `config.toml`** `[tools.verifier] verify_gate_mode = "strict"`（出厂默认仍
  `off`，见 config.py；invariant 要求 strict→emit_receipts=true，dev 已开）。
- **新单测** `tests/test_verify_gate_code_patterns.py`：9 例。

### 为什么 strict + code patterns 安全（关键判断）
- `registry.execute_tool` 对**每个工具** dispatch 都 `emit_receipt`（带真 tool_name）
  → 真调过 write_file/edit_file/run_shell 的任务，ledger 必有对应 receipt → claim 命中。
- 裸声明（fake completion，零工具调用）→ ledger 无 receipt → unmatched → strict 拦。
- strict 拦截 = 回灌"请真调工具"system msg + continue（max_verify_nudges 上限，
  到顶 ephemeral 救援后放行）→ **不死循环**，误判最多多几轮 nudge。

---

## 验证

### 单元测试(strict 拦/放逻辑)— 9/9 + 全套 31/31 PASS
| 用例 | 验证 |
|---|---|
| code_patterns_load | 4 新 pattern 加载 |
| created_file_claim_extracted | "已创建 hello.py" → claim path=hello.py |
| strict_passes_when_receipt_present | 有 write_file receipt → **放行** |
| strict_blocks_fake_created_claim | 无 receipt → **拦截**(unmatched) |
| strict_blocks_fake_tests_passed | "测试通过" 无 run_shell → **拦截** |
| strict_passes_tests_with_run_shell_receipt | 有 run_shell → 放行 |
| modified_file_claim | edit_file 有/无 → 放/拦 |
| shadow_never_blocks_even_fake | shadow 模式 fake 也放行（出厂安全） |
| no_false_positive_on_future_tense | "我将创建 X"(未来时)→ 0 claim 放行 |

### 真机 E2E — strict 不误杀真任务
| 阶段 | 证据 |
|---|---|
| 任务 | "请在项目根目录创建 STRICT_CHECK.md…创建后读回文件确认内容" |
| strict 激活 | `verify_gate_init mode='strict' patterns=9` |
| 真调工具 | `write_file path=…/STRICT_CHECK.md`（receipt 落 ledger） |
| **未误杀** | **无 `verify_gate_nudge_injected`** → 最终 claim 匹配 receipt → 放行 |
| 产物 | STRICT_CHECK.md 创建成功(93B,内容正确)；tile 正常 idle |
| **判定** | ✅ PASS — strict 对真任务放行，任务正常完成 |

---

## 结论

- **strict 翻转安全**:真任务（真调工具）放行(真机证)；fake（裸声明）拦截(单测证)；
  未来时不误判(单测证)；shadow 出厂兜底(单测证)。31/31 单测 + 真机 1 例 PASS。
- pass 路径真机证 + block 路径单测证（强制 LLM 说谎以真机复现 block 不现实，
  单测覆盖该逻辑）。

## 决策点(留给你)— 出厂默认是否翻 shadow
- 现状:**出厂 `off`**（config.py 默认）/ **dev `strict`**。
- 决策3 当初建议"出厂 shadow（稳）+ dev strict"。翻出厂 shadow 需同时
  `emit_receipts=true`（invariant）→ 所有安装都发 receipt（小开销）+ 记 shadow warn。
  shadow **永不拦**，无用户可见破坏。**是否翻出厂 shadow 留你拍板**（涉及所有用户）。
