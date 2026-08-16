# Spike：旧 Workflow authority 不可见时的最终 detachment

> 日期：2026-08-16  
> 状态：PASS  
> 仓库改动：无；独立新模块与SDK public patch均在`/tmp`

## 命令

```bash
PYTHONPATH=/tmp/simple-harness-public-spike.n0pL3N/sdk-patched/src:/tmp/product-workflow-detachment:/Users/denny/projects/simple_harness/backend \
backend/.venv/bin/python \
/tmp/product-workflow-detachment/run.py
```

独立模块为`/tmp/product-workflow-detachment/sdk_v7/deep_research.py`与
`sdk_v2/ppt_pro.py`。旧v7/PPT v1字节无diff，正式两仓库未修改。

## Detachment guard

运行时禁止import：

- `deskpet.workflows.contracts/definition/control`
- `deskpet.workflows.definitions.v1`至`v7`

主动尝试旧v7 import得到`ImportError: DETACHMENT_GUARD:deskpet.workflows.contracts`；完成后
`blocked_loaded=[]`，即`sys.modules`无任何被禁模块。guard还真实捕获了
`ppt_tools -> registry -> old workflows`隐式导入；独立PPT模块移除该依赖后通过，guard未放宽。

## 实际结果

- exact identities：`deep_research@v7-sdk1`、`ppt_pro@v2`。
- DeepResearch：compile/register/start=`completed`，close/reopen stored=`completed`，recover=`completed`，
  8 checkpoints，1个真实content-addressed BlobStore文件。关闭前typed calls为LLM=2、artifact=1。
- PPT：compile/register/start成功，pure split interrupt=`waiting`；强制close/reopen仍waiting；只调用新
  public `WorkflowRunner.resolve_and_resume()`后=`cancelled`，recover=`cancelled`，14 checkpoints。
- 两图关闭前累计typed calls：LLM=4、search=1、fetch=1、artifact=1；重开后四类旧调用新增均为0。
- runner未直接调用低层`commit_decision`；临时SDK public API内部原子完成decision persistence+resume。

## 正式迁移约束

为做到完全detachment，BlobStore采用从产品实现机械迁出的独立consumer实现；PPT outline核心LLM/
JSON/SQLite逻辑同样内聚到新consumer模块。正式代码必须落在
`sdk_adapters/product_workflows/`或新definitions模块，不得重新引用旧workflow package。该机械迁移保留
BUSL产品层归属，不进入Apache SDK；SDK只提供generic public contracts/engine。

结论：新产品definitions在旧generic authority物理不可见时可compile/start/fault/reopen/resume/recover，
关键迁移假设不再依赖正式实现阶段试错。
