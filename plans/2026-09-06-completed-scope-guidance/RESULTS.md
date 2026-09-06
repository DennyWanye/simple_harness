# r19 completed Scope 指导：实际链结果

最后更新：2026-09-06。测试源码 `2691e364`，生产为主原 `a189ec4e`，未改生命周期、权限或SDK。H079/M618/S0313借用主target；非独立安装身份验证。**2个唯一新控制PASS**，非原生或模型质量完成。

- r1：1PASS/1FAIL，5.06s。真实closure输入保留完整创建/回读目标，未执行readback且Scope非complete通过，此后不重跑。另一项resume/status/inactive/sticky/下一真实HTTP/无文件已到达，末端错误地要求Host成功产物表记录拒绝而FAIL。
- r2：仅上述失败项，1FAIL/2.28s。错误地转为要求SDK effect head，仍FAIL。SDK准入前拒绝明确 `effect=None`；两次是oracle错误，不是产品缺审计，也没有删除失败记录。
- r3：修正oracle后仅该项，1PASS/1deselected，2.15s。公开tool proposal精确关联真实Run、turn5/6、call ordinal0、write_file和原始call公开复合hash；要求provider/request引用。下一物理请求分别携带真实inactive/sticky拒绝，无SDK effect head、无文件、原Scope仍complete。Dirac已接受2691源码delta；结果最终审另记。

资源：r1 PG46231 exit1/5.965s/peak377344KiB；r2 PG46372 exit1/2.999s/peak199424KiB；r3 PG47277 exit0/2.791s/peak198192KiB/minDisk3600MiB。均remaining=[]、cleanup_error=null，已交槽Singer。

## 可复跑最小命令

在本worktree执行；`PY` 为主 `.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python`，`MAIN` 为 `/Users/denny/projects/simple_harness-primary-candidate`。默认资源锁/2GiB/180s/磁盘门不覆盖。carrier只加载本Host源码及主 `primary-079618/installed`，不跑模型。

```sh
"$PY" -I -B "$MAIN/scripts/run_resource_bounded.py" --evidence-dir "$OUT" --  "$PY" -I -B .local-test-evidence/2026-09-06/completed-scope-guidance/run.py  "$PWD/$OUT/cases" -k completed_scope_actual_resume
```

已绿closure不重复跑。其首次使用同carrier省略 `-k`，文件仅这2控。OUT每次使用新目录。

## 本机ignored原始索引

根 `.local-test-evidence/2026-09-06/completed-scope-guidance/`，旧main `completed-scope-guidance/r1` 的缺reader旧harness失败另保留，未迁移或追认为绿。

| 文件 | SHA-256 |
|---|---|
| r1/command.log | 698c073c4556d2c8da91b9a73ca3a357f9d057fcbfc9860a65f22d2e31d09b83 |
| r1/resource.json | 4e0fca25f579b7c6aa4e3b0a666cffc4f62fefd7eda5b3a12b9955ba634fe303 |
| r2/command.log | b492941e593f5b85cc3651ec7fd34c489f35cf03778575f76a6338ec85a26983 |
| r2/resource.json | 5f4ced38714daf3d19cc611e34398ff46db7c673f2e1b5e220a940d219a2417e |
| r3/command.log | fbd71905e50443d0d021c6d2c21863549ce249d0b8d2a6482eb0e7ae2f7e0f84 |
| r3/resource.json | 253c71f5340364568ba8943649e8050f5ff96ca9a42b15e6342c4aef8cb3f08d |

## 仍未完成

原complete Scope不会复活；当前只验证拒绝和指导。真正继续旧workspace文件需要新active Scope并经真实公开authority显式绑定原root，属下一独立叶；普通create_new默认新目录不替代该能力。旧Scope的历史/status读取不等于允许其read_file项目effect。确定性closure响应不代表模型完成判断质量，r19原生FAIL和原24轮旅程未完成均保留。
