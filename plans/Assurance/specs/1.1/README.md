# ASSURANCE-EXEC-1.1 完整修订包

入口 `ASSURANCE-EXEC-1.1.zh-CN.md`，逐项回应 `RESPONSE-TO-REVIEW.md`。这是F01–F15一次性规格修订，不是SDK补丁或本地产品PASS。保留现有scoped/root/composition/OCC/OPS主链；旧1.0活动正文和Schema不应与本版混用。

## 阅读顺序

1. 主文档§0–3、RESPONSE；确认单owner和内部Ref来源。
2. 主文档§4–11及附录C；读标签曝光、typed checks、六purpose多invocation、finalizer、barrier、pending cursor、restore和lane。
3. `implementation/BODY-WIRED.md`、`integration-map.json`、`ref-resolution-map.json`：按实际dirty源码定位、集中完成主体与跨层接线，期间只做具体阻塞micro-check。
4. 主体完成后统一SDK场景/继承66与OCC12/变异/状态化/legacy与H1/隔离原生Host/真实模型12trial/独立代码审查。新Mission默认ON是通过后的同交付动作，不另等开关批准。

## 包工具（必须整体保留）

在新解压目录，用项目环境而非系统Python：

```bash
PY=/Users/denny/projects/simple-harness-sdk-h1h-impl/.venv/bin/python
"$PY" -B tools/verify_delivery.py --root .
"$PY" -B tools/check_plan.py --root .
```

包参考测试可以用同解释器 `-m unittest discover -s tests -v`。它只验证本文pure规则、Schema和参考父schema上的SQL；不替代真实SDK/Host。项目主体编码期间不要把批量reference/SDK测试当进度数量。

`tools/schema_support.py`为本包使用的明确JSON Schema子集检查器，无第三方runtime依赖；unknown validation keyword拒绝。`field-producers.json`没有重复nested_schema，只存pointer、展开子树hash和唯一producer。SQL以真实runner/完整schema执行为后续验收，不手工对生产db执行，不改旧checksum。

## 来源与版本

`sources/`保留接收方评审及18份当前dirty源码摘录/文件hash，不冒称全仓source snapshot。本地重新定位名字、迁移下一空闲号、等价接口属于实施选择；不能以AST存在标行为通过。原计划/原66/OCC12在inputs只作追踪，不执行其中与1.1冲突的旧开工安排。

`VALIDATION.md`是本轮实际包验证；`review/CHALLENGE-REPORT.md`是作者四视角对抗自审，不是独立子代理。最终独立生产审阅仍NOT_RUN。全部原始产品日志/截图/receipt/db在ignored证据目录；Git只留已审摘要、命令、实际nodeid、相对索引和SHA。
