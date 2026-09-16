# 拟议内部Schema

这四份Schema明确了HTN方法、执行反馈、计划提案和系统Resolution的内部结构，不是当前SDK或Host已支持的API。

JSON Schema只验证结构，不验证引用存在、scope、权限、方法合法性、根要求覆盖、真实工具结果、并发版本、预算或效果。因此合成fixtures里特意包含“结构合法但域规则必须拒绝”的样本。

Method只能提出数据定义；registry状态/执行权限由系统另外生成。ValueExpr中的output引用由编译器产生DATA边，ordering字段只表达ORDER。禁止把condition解析为Python eval。

参数Schema、任务类型和谓词引用由本地、已批准、不可变的注册表解析，不从模型提供的URL下载。所有合成hash和ID只是结构占位，不能作为实际证据。

生产实现需补：递归深度/节点/消息尺寸限制、绑定类型/端口/引用检查、谓词四值逻辑、计划无环/覆盖校验、只读源范围和事务性Command处理。目标设计在主文档中已规定，本文不将Schema通过当成这些能力通过。
