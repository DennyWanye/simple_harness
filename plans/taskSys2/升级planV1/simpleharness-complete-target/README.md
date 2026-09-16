# SimpleHarness 完整目标与差距资料包

主文档：`plan.zh-CN.md`。其余文件用于实施追踪和防止需求丢失。

- `requirements.json`：60项完整目标要求及源码证据分类。
- `requirements-traceability.zh-CN.md`：同一数据的可读矩阵。
- `original-chapter-map.json`、`original-31-chapters-map.zh-CN.md`：原始31章逐章映射。
- `scope-decisions.zh-CN.md`：原文、讨论扩展、范围收缩和显式裁决。
- `code-changes.json`：真实现有路径与建议新增路径，尚未应用。
- `work-packages.json`：七个完整工作包及依赖，不是可任意删减的MVP列表。
- `acceptance-scenarios.json`：84项验收场景规范，全部NOT_RUN，尚非可执行测试代码。
- `sources.json`：源码基线、读取范围、限制与一手研究。
- `scripts/validate_plan.py`：仅校验本资料包的编号/引用/覆盖/JSON一致性。

本资料包不包含生产补丁，不包含已运行的项目测试结果。Host在本轮当前连接不可访问；不应把它判断为未实现。

运行 `python scripts/validate_plan.py` 仅验证计划结构，绝不代表HTN、SDK或UI已通过测试。
