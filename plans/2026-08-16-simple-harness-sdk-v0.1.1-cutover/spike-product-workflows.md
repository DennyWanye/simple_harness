# Spike：产品 DeepResearch/PPT 经 SDK public Workflow Runtime

> 日期：2026-08-16  
> 状态：PASS（技术路线）；不等于真实产品 Adapter/LLM/渲染验收  
> 仓库改动：无；SDK patch、迁移图和运行库均在 `/tmp`

## 假设与命令

验证产品图是否能在不包裹旧 Runner 的前提下，通过 SDK public graph construction、Registry、Runner、
SQLite checkpoint和Runtime完成start→fault/close→reopen→resume/recover。

```bash
PYTHONPATH=/tmp/simple-harness-public-spike.n0pL3N/sdk-patched/src:/tmp/product-workflow-migration-spike \
/tmp/simple-harness-public-spike.n0pL3N/venv/bin/python \
/tmp/product-workflow-migration-spike/run_spike.py
```

环境：Python 3.11.15；metadata 0.1.0；临时SDK source
`/tmp/simple-harness-public-spike.n0pL3N/sdk-patched`。exact 0.1.0 未公开所需graph construction/
SQLite factory/host-port API，所以该patch只是0.1.1设计验证，不能作为candidate receipt。

## 实际结果

- DeepResearch v7：6 nodes、0 conditional；public compile/register成功，经SDK Runtime+SQLite完成；
  close/reopen后仍`completed`，7 checkpoints。
- PPT拓扑：25 nodes、7 conditional；补purity合同后compile/register，执行到真实`ppt_outline`
  interrupt为`waiting`，11 checkpoints；此处强制close。
- reopen后仍`waiting`，关闭前13个physical effect均未重交。durable resolve decision后resume到
  `completed`，共21 checkpoints，再次recover仍`completed`。
- reopen后只新增interrupt之后8个effect；
  `prior_effects_recommitted_after_reopen=[]`，`duplicate_physical_commits=false`。
- 当前public层仍缺高层`answer_interrupt()/resolve_and_resume()`：直接resume会因pending interrupt CAS
  冲突；spike先调用了低层UoW commit decision。Slice A必须补此public原子操作。

本次复跑临时输出库为`/tmp/product-workflow-consumer-qynsdotv/`；它是可丢弃spike数据，不属于验收
证据，未复制到Git。

## Purity disposition

### DeepResearch v7

- `normalize`、`finalize`：纯状态转换/terminal intent assembly。
- `plan/search/synth/persist`：分别含LLM/projection、search/fetch/subagent/blob、LLM/blob、artifact
  effect，必须只经typed Host Ports并以run/node/effect identity幂等。
- 无selector、无interrupt；无需purity annotation。

### PPT现有v1

- 7个selector：`research_gap_route`、`outline_decision_route`、`preflight_route`、
  `prepare_slides_route`、`image_map_route`、`render_route`、`visual_route`均只读冻结state，可显式声明
  `selector_effect_policy="pure"`。
- `wait_outline_decision`在interrupt前的prompt构造纯，但回答后会读写`ppt_outline_store`；整个handler
  不能诚实声明pure。必须把interrupt节点缩为纯barrier，把回答后的store读写移到新的幂等effect节点。
- 其余LLM/search/fetch/blob/artifact/notifier/render/evaluator/receipt操作都走产品Host Ports；
  `research_cite/prepare_slides/visual_revise/publish`保持确定性状态节点。

## 版本与正式门

- 不篡改现有`ppt_pro@v1` manifest。产品开发历史Run按frozen acceptance执行获批reset，不承诺旧行
  recovery；切换后的active registration使用新`ppt_pro@v2`，保留v1源码作为切换前事实/回归参照，
  不注册到新SDK Runtime。
- DeepResearch active registration使用独立新模块`definitions/sdk_v7/deep_research.py`和cutover identity
  `deep_research@v7-sdk1`；v1-v6旧执行不迁移。旧生产definitions在Slice C删除，字节不被改写，历史
  只由Git pre-cutover commit保留；新SDK catalog只暴露active v7-sdk1。
- 正式Slice B必须用真实产品handlers重跑compile/register、fault injection、close/reopen/resume和
  effect ledger；本spike的物理实现是临时幂等Host Port，不证明真实Provider、检索、PPT渲染或
  ArtifactCard。

结论：`VERDICT: PASS`。类型边界与恢复技术路线已验证；版本化拆interrupt与真实Adapter验证仍是实现门。
