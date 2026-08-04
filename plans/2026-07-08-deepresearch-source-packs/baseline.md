# Baseline：deepresearch source-pack 优化

执行前基线记录：

- 当前 git worktree 已有大量用户/既有改动，`git status` 非干净；本任务只修改 deepresearch source-pack 相关文件和文档。
- 已读关键现状：
  - `research_tools.py` 目前搜索阶段由普通子问题、site-directed、query expansion 组成。
  - `default_extract()` 在真实运行且未注入测试 client 时优先走 Scrapling，再回落 httpx/trafilatura/JS/Jina。
  - `agent_loop.py` 已有 deepresearch 完整结果强制收口逻辑。

执行前 focused baseline：

- 待实现前未跑完整 pytest；执行后以 focused regression 证明新增功能不破坏 deepresearch 相关测试。

执行后结果见 [test-results.md](test-results.md)。
