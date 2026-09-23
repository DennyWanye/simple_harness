# ARP-EXEC-1.1.1 完整交付入口

这是在1.1上仅关闭R1–R5的局部规范修订，不是SDK补丁，不启动生产业务。全包只需一个ZIP；保留所有Q01–Q20、native-only、原账本、主体优先/验收后置的约定。

## 阅读顺序

1. `DECISIONS.md`：R1–R5唯一裁定和保留的Q01–Q20。
2. `ARP-EXEC-1.1.1.zh-CN.md`：当前统一主计划。
3. `implementation/CONTEXT-RECALL.md`：R3全部内部字段/producer/分页/恢复/错误/manifest绑定；`CONTEXT-SEARCH.md` C5/C7：R2/R4/R5；`JOBS-LIFECYCLE.md` J6/J7：R1。
4. `contracts/`、`sql/`、字段/来源/接线表：完整可核资产；`review/CHALLENGE-REPORT.md`、`VALIDATION.md`：验证边界。
5. `implementation/BODY-WIRED.md`、`TEST-PLAN.md`：主体完成后的生产验收，原80场景/16变异不删，本次7场景/5变异追加。

inputs仅追踪；旧冻结Schema不覆盖新正文。不得覆盖dirty SDK或共享Host。实际源码/制品/资源指纹在本地盘点，不用历史HEAD代替。

## 轻量包检查

在解压根执行，用真实集成父候选的解释器（无SDK import、不打开产品DB、不调用模型）：

```bash
SDK="/path/to/current-integrated-native-sdk"
KIT="$(pwd -P)"
PY="$SDK/.venv/bin/python"
test -x "$PY" || exit 2
"$PY" -B "$KIT/tools/verify_delivery.py" --root "$KIT"
"$PY" -B "$KIT/tools/check_plan.py" --root "$KIT"
```

## 可选参考复核（不是实施开工门，也不是SDK测试）

```bash
cd "$KIT"
"$PY" -B -m unittest tests.test_review_r1_r5 -v
"$PY" -B -m unittest discover -s tests -v
"$PY" -B tools/run_reference_mutations.py
"$PY" -B tools/run_review_mutations.py
```

全部参考测试只用标准库；无需为此安装jsonschema或修改项目依赖。真实SDK/migration、native UI、meter/embedding/model与独立代码审阅仍PENDING。作者四视角自审不冒充独立子代理。
