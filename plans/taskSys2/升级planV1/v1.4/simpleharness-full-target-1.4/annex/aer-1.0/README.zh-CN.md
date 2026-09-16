# 三专项联合设计 AER-DESIGN-1.0

先读 `design.zh-CN.md`。本包细化既有目标，不改原60项需求或P1—P9。

- `acceptance-scenarios.json/.md`：48项待实施的生产/集成验收。
- `requirements-map.json`：保留全部原需求的ID/标题/P归属，记录本包贡献，不代表完成。
- `code-changes.json`：实际修改与拟新增模块，不是可apply补丁。
- `sources.json`：固定源码范围、blob、文档输入hash及研究依据。
- `schemas/`、`fixtures/`：4种新内部结构草案与8个结构样本，不验证真实权限/效果。
- `reference/`、`tests/`：30个局部纯规则测试；不连接生产SDK/模型/工具/数据库。

从本目录执行：

```bash
python -m unittest discover -s tests -v
python scripts/validate_pack.py
```

参考测试只需Python 3.10+。资料包校验需要当前环境安装 `jsonschema`（本轮实际版本见validation.json）；不应据此变更你的项目依赖。

本轮没有修改远端仓库，没有执行SDK、真实Provider、连接器或Host UI测试，也没有把旧文件标记为过时/完成。新要求、正式结构和迁移通过代码与实际验收落实。
