# M619 主组合两项验证

最后更新：2026-09-07。固定主 Host `d86e4805842c5cfbfcb6e3c463ae47d8935147ff`（产品0e146792），借用主 `memory619-artifact/installed` 的 H079/M619/S0313。

**Auto 原root新Scope真实写入 + alreadyBound 拒绝两项 2PASS / 5 deselected，4.05s。** 只为本次合并参数与Memory候选变化做两项组合；不是新增两个不同业务控制，不重复累计原7unique，也非模型/native。Manual公开service旧绿仍属M618，实际Manual UI仍待独立叶。

PG61943 parent exit0，elapsed4.723s，peak184544KiB，minDisk3120MiB，remaining=[]、cleanup_error=null、stop_reason=null。默认共享锁/2GiB/180s/磁盘门未覆盖，已交槽主native。PID56392未触碰。Manual源码暂停，主source与原userdata没有修改。

carrier在本独立树 `.local-test-evidence/2026-09-07/completed-scope-619-prepared/run.py`；前置精确HEAD/clean及后置HEAD守卫通过。205个实际加载SDK模块均来自指定target，未重做全成员比对、安装或构建。原r1业务红与后续fixture/hash oracle红保留，不回填为通过。

最小命令（在本独立树，OUT为新ignored目录；仅主明确交槽时运行）：

```sh
MAIN=/Users/denny/projects/simple_harness-primary-candidate
PY="$MAIN/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python"
"$PY" "$MAIN/scripts/run_resource_bounded.py" --evidence-dir "$OUT" -- \
  "$PY" -I -B .local-test-evidence/2026-09-07/completed-scope-619-prepared/run.py "$PWD/$OUT/tmp"
```

原始根 `.local-test-evidence/2026-09-07/completed-scope-619-combination/r1/`，全部ignored：

| 文件 | SHA-256 |
|---|---|
| command.log | 67e7e3f611401853359e96972a8ba52b522bc201d15e19ad9a52452ea8613731 |
| resource.json | dfe93d064fa358e4d67e7f1a12516a4c0826596ca225aa67243b3dbcc373f4ae |
| loaded-origins.json | 5f9bfb40d31a1d6caf26aaf61fd763795254e0ea9a6fd5f3041241cd06057d95 |
