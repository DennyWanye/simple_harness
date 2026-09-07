# 非空 resume 来源接线：定向验证

最后更新：2026-09-06。分支 `feat/closure-resume-source`，base `ff2f2009`，产品/测试固定 `0a52085e`。尚未合入主组合；Dirac已复核固定源码、r1/r2和资源记录，给出本批限定ACCEPT，未发现新增确定P0/P1，不代表全契约完成。

## 实际改变与边界

真实 `task_scope_update` 的公开SDK effect、canonical plan/closure receipt、最新operation共同证明字段；后台fallback在原mutation/attempt成功事务内追加真实Provider response的Host S1，精确绑定input/attempt/plan，不改旧幂等hash。字段依赖进入原scope manifest，再走当前Memory可见性和最终Host token检查。缺来源仍pending；没有伪造SDK receipt或回填旧fallback。

`read_run_dependencies` 以实际effect index及SDK公开effect确定生产前缀，route/search/page统一采用同一严格小于的sequence截止点。保留真实访问审计。两个新S1 policy仅显式登记到Host builder的精确集合：`host-closure-attempt-input/v1`、`host-closure-result-source/v1`；SDK凭据/canonical/subject校验不变。整合仅取该常量hunk，保留主线Singer的typed modes改动。

载体为既有H077/M616 installed target、借用generic依赖与本树Host源码。前台SDK走确定性model响应；后台closure经生产resolver/Provider adapter到实际HTTP MockTransport。不是外部Provider、native或主H077/M617组合证据；未新建环境、安装或构建。

## 分批结果

| 批次 | 固定源 | 结果 | 说明 |
| --- | --- | --- | --- |
| r1 | f02881d9 | 1 PASS / 4 FAIL，9.94s | atomic commit fault已绿；tool fixture绕过原`_result`包装，fallback新S1 policy未登记，失败保留 |
| r2 | 0a52085e | 4 PASS / 1 deselected，12.24s | 仅重试四红；未重跑atomic fault |

共 **5个不同场景通过**，不是两批全量各自绿：

- `tool`：真实tool mutation后下一Run取得非空resume，实际后续工作及closure物理请求含该字段，attempt成功。
- `tool_then_resume`：同Run update后再context_route，已冻结字段proof在公开effect完成后保持可验证；再完成下一Run工作/closure。
- `fallback`：实际后台响应产生mutation与唯一result S1，下一Run闭合非空resume链。
- `fallback_forget`：新result S1先可见，公开Memory suppression后resume字段不可见；没有额外closure发送。这是来源读取门控制，不冒称遗忘后物理发送负控。
- `fallback_commit_fault`：真实发送一次，result S1追加后故障导致原TX完整回滚，无mutation decision、result S1、succeeded attempt半提交；不把已发送说成not_sent。

本次修正分别是fixture返回包装与真实Host支持policy缺口，不能将fixture TypeError归为生产SDK缺陷。`_matches`仅按生产handler剥离声明过的`deskpet_public_progress`，其余字段仍严格校验并与plan精确比较。

## 可复跑命令与原始证据

本机ignored launcher `run_tests.py`固定H077/M616目标及Host源码，保留stack启动身份检查。下面是本次仅四红命令；以后复跑需新证据目录，不覆盖旧批次。

```sh
TREE=/Users/denny/projects/simple_harness-typed-recall-context-use-full
PY="$TREE/.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python"
"$PY" /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py \
  --evidence-dir "$TREE/.local-test-evidence/2026-09-06/closure-resume-source/r2" -- \
  "$PY" -I -B "$TREE/.local-test-evidence/2026-09-06/closure-resume-source/run_tests.py" \
  "$TREE/.local-test-evidence/2026-09-06/closure-resume-source/r2-temp" \
  -k 'not fallback_commit_fault'
```

所有路径相对本树 `.local-test-evidence/2026-09-06/closure-resume-source/`：

| 文件 | SHA-256 |
| --- | --- |
| r1/command.log | 1e99ab6a159162fc419bf384c9bad23abe3ee80e199aac525885819c187db52d |
| r1/resource.json | c93d23f6906d772ce5a3b3fe7a3178d16035be35bcc3e26a6128a4f522d23604 |
| r2/command.log | 0adc9fcfb88221c83112d06774d06bd62eecbdfd54939549580c2ffc1f3a148d |
| r2/resource.json | ba5c9d3df15a52282b9d95be04b0cf20cfc8938b14fa30c600502f2c9c8350cc |

r1 PG93696 exit1，remaining[]、cleanupnull；r2 PG95551 exit0，13.199s、峰392224KiB、最低可用磁盘4218MiB，remaining[]、cleanupnull。遵守默认2GiB/180s和磁盘准入，已释放共享槽给Carver。raw/DB均ignored，旧绿未重跑。

## 尚未证明

独立plan/effect混接、旧无carrier fallback、多个字段版本的latest不可见不回退、旧manifest新增字段不扩张、ACK后恢复的专门控制尚缺；goal修改虽共享实现，本批只执行resume。跨库最终检查到实际发送不是原子撤权事务，原有最终fence仍保留。缺来源旧数据不重写、不冒证。

当前primary未发现旧`_build_product_agent_loop`/compactor可达调用，本叶不复活它，也不标完整compaction、长旅程或program完成。主组合/native仍需其实际验证，本批不触用户原库、冻结SDK制品或主树。
