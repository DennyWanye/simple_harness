# 附件 TG（TaskGraph 专项实现约定）

两份文档原样复制自 `升级planV1/taskGraph/`（2026-09-16），作为 FULL-TARGET-1.3 的规范附件，实现约定级。

- 凡与主文档 ADR 或 §23 不一致处，以主文档为准；限定见主文档 §24.2。
- 两稿引用的配套文件 `taskgraph-tests.json`（TG01–TG32）、`requirement-map.json`、`source-index.json` 本机缺失，状态 MISSING_ATTACHMENT；处理见主文档 §24.3。
- 两稿之间文件名漂移的定案见主文档 §24.2。

## 附件 AER（v1.4）

`aer-1.0/` 原样复制自 `升级planV1/三合一/`（设计文档与解压后的配套包，2026-09-16），作为 FULL-TARGET-1.4 的第二份规范附件，实现约定级。

- 凡与主文档 ADR、§23、§24 不一致处，以主文档为准；限定见主文档 §25.2。
- 配套包自检 PASS，30 个参考测试通过（纯规则，不是生产验收）。
- 48 条 AER 场景以 aer_annex 字段并入主文档现有 T 编号，见主文档 §25.3。
