# SimpleHarness 完整目标与差距闭合资料包

建议先读 `complete-plan.zh-CN.md`。

本资料包针对SDK源码 `61a85eb7e8c003fa894497090f5de893419ebbd1`（0.11.1）制作。v1.4（2026-09-16）在 v1.3 基础上并入 AER 专项附件（`annex/aer-1.0/`，主文档 §25）并记录 A96 基线完成，变更清单见主文档 §0 C25–C32；Host 已在本机核对为可读，不再是未知量。

## 内容

- `complete-plan.zh-CN.md`：范围漂移追溯、当前差距、完整目标架构、12项设计裁决、HTN/AND–OR/局部修复/搜索/Context/验证/长期任务/事件重建、代码落点、9工作包。
- `requirements.json`：60项需求，59项必需能力或约束、1项明确排除（用户长期Memory）。不是完成百分比。
- `work-packages.json`：每包完整交付、依赖、代码目标、完成门槛和测试映射。
- `acceptance-scenarios.json` / `.zh-CN.md`：90项待实施的行为验收规范，全部NOT_RUN。
- `original-chapter-map.json`：原始31章保留与补齐位置。
- `sources.json`：48项原始材料、固定源码和一手研究来源，含阅读范围和证据限制。
- `annex/`：TaskGraph 专项实现约定两份 + AER 联合设计与配套包（aer-1.0/）（规范附件，实现约定级；缺失的 TG01–TG32 等配套文件标 MISSING_ATTACHMENT）。
- `schemas/`：4份拟议内部JSON Schema；并非当前SDK已经支持的协议。
- `schema-fixtures/`：13个合成结构正负样本，含一项结构合法但业务必须拒绝的样本。
- `audit-metadata.json`：本轮核查范围。
- `scripts/validate_plan.py` / `deliverable-validation.json`：仅资料包完整性与Schema校验，不执行项目。

## 重新校验资料包

在已安装 `jsonschema` 的隔离Python环境中执行：

```bash
python scripts/validate_plan.py
```

它不会读取用户的生产数据库、不会调用模型、不会访问网络，也不会把90项场景标为通过。输出只说明需求/引用/工作包依赖与Schema示例结构一致。

## 交付边界

没有修改远端仓库，没有执行SimpleHarness单元/集成、mypy、真实Provider、HTN求解器或原生UI测试。P1–P9是同一完整终态设计的实施依赖排序，不是取消后续能力的MVP清单。所有未部署的检查器、未接通的接口和未执行的测试都不得记为完成。

不得把模型提出的方法、事实、预算、状态或权限直接写成正式记录。独立语义审阅、确定性安全约束、实际证据和事务性Commit分别承担自己的职责。
