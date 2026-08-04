# codex 作业 A2：实现 WI-3（阶段化提示词 + 收尾自查清单）

你是 DeskPet 项目（仓库根 `G:\projects\deskpet`，已在此 cwd）的实现工程师。**严格按权威 plan 实现。**

## 第 0 步：读 plan
打开 `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`，读 **§4（WI-3）、§13.3（D1/D2）**。以修订节 §13.3 为准。

## 第 1 步：实现
1. 打开 `backend/deskpet/agent/assembler/components/persona.py`，找到 `_CODE_MODE_PERSONA_TEMPLATE`（约 L36-62，现第 4 步只讲"验证"）。**在该模板末尾追加**（只增不改既有文本）一段「第 5 步 · 收尾自查清单」，内容对标 plan §4.3 / §13.3：
   ```
   【第 5 步 · 收尾自查】说"我完成了"之前，逐项对照原始需求打勾：
     1. 列出用户最初要什么（需求清单）
     2. 逐项标 ✓/✗/部分；有 ✗ 必须回到执行或显式说明放弃理由
     3. 改了代码 → 必须已 verify（跑测试/真机/diff 看生效），不可"应该没问题"
     4. 最终回复给出：做了什么 + 验证证据 + 剩余项
   ```
   （注意 persona.py 是 Python 字符串模板，按其既有拼接风格——若是带 `{model}` 等占位的 f/format 模板，新增文本里**不要**引入未定义的 `{}`，必要时 `{{`/`}}` 转义。）
2. **不改 companion persona**（D2：companion 条件化自查推迟为 follow-up）。

## 第 2 步：测试
新建 `backend/tests/test_wi3_persona.py`：
- `test_code_persona_has_closing_checklist`：解析/取 code 模式 persona 文本，断言含「收尾自查」且含 "✓"（参考 §16 建议多关键字校验，避免过宽松）。
- `test_companion_persona_unchanged`：companion persona 不含「收尾自查」（保持原样，BC）。
- 读 `persona.py` 里取 persona 的真实函数（如 `_resolve_persona(config)` 约 L83），测试按真实接口构造 code/companion 两种 config 调用。

跑：
```
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi3_persona.py -v
```
反复改到全绿。

## 约束
- **不要 `git commit` / `git add`**。只改 `persona.py` + 新建 `test_wi3_persona.py`，不碰其它文件。
- 完成后输出：persona.py 改动摘要 + 测试结果。
