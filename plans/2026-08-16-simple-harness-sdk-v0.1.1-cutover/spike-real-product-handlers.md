# Spike：真实产品 handlers 经 SDK public Workflow contracts

> 日期：2026-08-16  
> 状态：PASS（真实handlers、typed Ports、terminal、interrupt、reopen）  
> 仓库改动：无；新identity模块、SDK public patch、runner和SQLite均在`/tmp`

## 命令与真实性边界

```bash
PYTHONPATH=/tmp/simple-harness-public-spike.n0pL3N/sdk-patched/src:/tmp/product-workflow-migration-spike:/Users/denny/projects/simple_harness/backend \
backend/.venv/bin/python \
/tmp/product-workflow-migration-spike/run_real_spike.py
```

使用真实产品DeepResearch/PPT handler函数；LLM/search/fetch/artifact边界是产品精确typed wrapper，Blob
使用真实`BlobStore`。验证值`DR_HANDLERS["plan"] is dr_source.plan_handler == true`。这不是用单一fake
effect替换节点；只有物理外部系统被double。Python 3.12.13，SDK metadata 0.1.0，使用前一个spike的
`/tmp` public API patch。旧v7/PPT v1 source bytes未改。

## 实际结果

- 注册`deep_research@v7_sdk1`和`ppt_pro@v2`成功。
- 首次DeepResearch run真实`normalize/plan/search/synth/persist/finalize`全部执行，6 checkpoints；typed
  Port与`isinstance`边界通过。它在terminal projection精确暴露
  `workflow_engine:invalid_delivery_intent`：v7深层report payload超过SDK depth 8。
- 随后仅在新v7_sdk1图尾新增`terminal_envelope_adapter`，把完整report写入真实content-addressed
  `BlobStore`，delivery intent只保留浅层summary/blob/artifact refs。复跑结果：start=`completed`、
  SQLite stored=`completed`、close/reopen/recover=`completed`，8 checkpoints、1个report blob；旧v7
  bytes/manifest与六个真实handler函数均未改。
- PPT v2共26节点，真实research/outline/outline-store projection执行后进入pure `ppt_outline`
  interrupt并`waiting`；强制close/reopen仍waiting。durable resolve `cancel`后独立
  `apply_outline_decision` effect执行，terminal=`cancelled`，recover仍cancelled，共17 checkpoints。
- reopen后LLM/search/fetch/artifact新增调用均为0，interrupt前physical handlers未重放。
- 最终DeepResearch typed physical calls：LLM=3、search=4、fetch=4、artifact=0（本输入未触发artifact
  save条件）；reopen recover后四类旧调用新增均为0。临时根目录
  `/tmp/real-product-handlers-hp3qiqfe`。

## 发现并固化的兼容改法

1. SDK严格检查handler result为SDK `StatePatch`；新模块必须直接import/return public SDK class，禁止
   运行时module-global monkeypatch。
2. `ResearchPorts.from_workflow_context()`保留产品exact typed checks；Adapter必须实现精确
   `ResearchLLMPort/ResearchSearchPort/FetchPort/ResearchArtifactPort`，普通duck type不够。
3. SDK selector输入是deep-frozen JSON（array为tuple）；新pure selector在边界调用public
   `thaw_json()`再复用产品route逻辑，不能修改冻结state。
4. PPT v2把v1 interrupt拆为capability-free pure barrier与普通`apply_outline_decision` effect节点；
   conditional source和loop binding移到apply节点。
5. DeepResearch v7_sdk1把terminal report/citation envelope扁平化为bounded summary+artifact/blob ref，
   payload深度≤8；不放宽SDK全局安全上限。正式oracle同时验证Artifact/Blob ref可打开且与完整报告hash
   一致，避免为过深载荷静默丢字段。

最终复跑已验证第5项；真实DeepResearch六节点+adapter从start到terminal/reopen均PASS，PPT v2
interrupt回归同时保持PASS。该证据仍不替代正式Slice B真实Provider/检索/渲染/ArtifactCard验收。
