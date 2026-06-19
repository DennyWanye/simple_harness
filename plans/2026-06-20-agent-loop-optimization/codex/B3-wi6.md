# codex 作业 B3：实现 WI-6（SEARCH/REPLACE 降级匹配 + did-you-mean）

DeskPet 项目（仓库根 `G:\projects\deskpet`，已在此 cwd）。**严格按权威 plan 实现。**

## 第 0 步：读 plan + 现状
1. 打开 `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`，读 **§7（WI-6）、§13.6（G1/G2）、§15.2 A-7、§16.2**。以 §16.2 为准（修正了 A-7 漏定义 `file_lines` 的 bug、明确 `_err` 是 `_err(error, hint, **extra)`）。
2. 读 `backend/deskpet/tools/os_tools/edit_file.py` 全文，确认现状：现在是精确字符串匹配（`old_string`→`new_string`，count==0 报错、count>1 且非 replace_all 报错），无降级。读文件内容变量是 `text = p.read_text(...)`，`_err(error, hint, **extra)` 在约 L30，成功返回 `{"replacements","path"}`。

## 第 1 步：实现（只改 `backend/deskpet/tools/os_tools/edit_file.py`）
给 `edit_file` 增**降级匹配 + did-you-mean**（新增路径，不破坏现有精确匹配）：
1. 新增参数 `fuzzy: bool = True`（默认开；schema 也加该参数说明）。`fuzzy=False` → 只走精确（BC）。
2. 精确未命中（count==0）且 `fuzzy=True` 时，按序降级（§16.2）：
   - **先定义** `file_lines = text.split("\n")`（§16.2 修正：A-7 原漏这行）。
   - ① **whitespace fallback**：对 `old_string` 与文件各行 strip 后比对；命中**唯一**则按原缩进替换；多命中→转 ③。
   - ② **anchor fallback**：取 `old_string` 首尾各 1-2 行作锚点定位区间，`difflib.SequenceMatcher(...).ratio() >= 0.85` 且唯一→替换；否则→③。
   - ③ **did_you_mean**：
     ```python
     import difflib
     cands = difflib.get_close_matches(old_string, file_lines, n=3, cutoff=0.6)
     return _err("no exact match for old_string", hint="未精确命中；见 did_you_mean 最相近行",
                 matched_by="none",
                 did_you_mean=[{"line": file_lines.index(c) + 1, "text": c} for c in cands])
     ```
     （`matched_by`/`did_you_mean` 作为 `**extra` kwargs 直接传给 `_err`，它内部 `body.update(extra)`。）
3. 降级命中时返回结构加 `{"matched_by": "whitespace"|"anchor", "confidence": float}`，并仍含 `replacements`/`path`。

## 第 2 步：测试
新建 `backend/tests/test_wi6_edit_fallback.py`（§7.4）：
- `test_exact_match_unchanged`：精确匹配路径行为与现在一致（BC）。
- `test_whitespace_fallback`：old_string 缩进与文件差空白 → 降级命中并正确替换。
- `test_anchor_fallback`：中间几行微差、首尾一致 → 锚点命中。
- `test_no_match_returns_did_you_mean`：完全找不到 → ok=false 且 did_you_mean 非空、含 line 行号。
- `test_fuzzy_off_is_exact_only`：fuzzy=False → 降级不触发（BC）。
用 tmp_path 建临时文件测试，参考其它 os_tools 测试写法（grep `edit_file` in backend/tests）。

跑：
```
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi6_edit_fallback.py -v
```
改到全绿。若仓库有现成 edit_file 相关测试也一并跑确保不破。

## 约束
- **不要 `git commit` / `git add`**。**只改 `edit_file.py` + 新建 test_wi6_edit_fallback.py**，不碰其它文件。
- 完成输出：edit_file.py 改动摘要 + 测试结果。
