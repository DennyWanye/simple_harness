继续切片 H1-D（同一会话，原任务书、06:30 裁定与规则全部有效）。核验第 5 轮两条 P1，测试先行，做完全部 commit，工作树干净：
- P1-7：权威行合并的去重键必须含 kind。补用例：task 与 obligation **同 id** 时两条引用都保留（现有去重用例只用了 obligation 行）。
- P1-8：构建器取任务的 `semantic_revision` 必须来自绑定的真实修订号。补用例：构造 `contract_revision = 3` 的绑定，断言 `visible_refs` 里该 task 的 `semantic_revision == 3`（现有世界修订号恰好全是 1，没有鉴别力）。
另外请自查一遍同类问题：凡是测试输入里"恰好相等/恰好有序/恰好唯一"而使断言失去鉴别力的地方（id 序与 kind 序一致、修订号全为 1、哈希全不同等），各补一条有鉴别力的输入。
完成后原样粘贴：本片测试文件尾行、`tests/orchestrator/full_target -q` 尾行、ruff 输出、`git status --short`。最终回复用「## 结果」开头。
