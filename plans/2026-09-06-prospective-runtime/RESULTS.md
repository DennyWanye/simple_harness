# 时间提醒 runtime leaf — 四个新控制通过

最后更新：2026-09-06。独立树simple_harness-typed-recall-context-use-full，feat/prospective-runtime-lane，base7844cf67。生产d80d61e8、生命周期ec99fa60、定向oracle/提示分隔7c627fbc；未合主。冻结H078/M618、nonSELF原分支及native原件不改。

主r14报告：真实21:00提醒已被后台接受为SDK prospective pending，时间1788699600；Host登记0、occurrence0。到期后用户17+26仅答43，native FAIL。现场主ignored native078618/primary-ui-37o1zq7g，04/06截图与reminder-observation.json保留；本叶没有重放该native或读写其userdata。

源码已确认原main compose仅A7 occurrence coordinator，缺registration consumer/timer生命周期，以及Memory public prospective authority builder透传。本片默认组合已有public consumer/authority/store/timer；独立轻量async child由MemoryAnalysisLane启动、关闭并完整join，长analysis不阻止时间tick。原S5c52初始化、grant/replay/UNKNOWN语义与F01延期保持。Persona明确能力可后台处理，但未真实登记不得声称已设置/已送达。

Dirac只读ec99fa60源码与四控：无新增确定P1，可首跑；明确不是测试/原生ACCEPT。两个关闭/restart意见已修：child显式同对象restart重新bind持久状态；父cleanup整段shield+join不会被第二次caller cancel跳过。7c627fbc增强suppression负控，要求实际SDK time result非matched，而非仅空inbox算通过。

**测试状态：4PASS / 7.15s，首批全绿。** 主r15正常退出后授权首跑，仅运行backend/tests/memory/test_prospective_runtime_lane.py，无业务修正/旧绿色重跑：

- 实际main activation+公共Manager/已有authority/真实登记到期，analysis等待控制不挡timer、重开唯一occurrence。
- SDK已提交但Host ACK丢失，跨grant/lease expiry保持原ref exactreplay；登记scan受控失败仍恢复timer。
- 实际suppression拒time match、关闭join、同对象显式restart唯一任务。
- 父cleanup两次caller取消，等待受控timer/index完成；仅生命周期fixture，不冒充SDK中断恢复。

不重跑已有timer/consumer/analysis控制。首批使用主默认scripts/run_resource_bounded.py共享锁、既有H078M618 installed目标+本树Host source；无新env/build/install、SDK sourceoverlay或candidate pin tests。raw仅本树.local-test-evidence/2026-09-06/prospective-runtime/r1/，原日志与三套fixture数据库保留ignored。

PG16947 exit0，elapsed7.952s，peak395664KiB（约386MiB），minDisk3741MiB，remaining_group_members=[]，cleanup_error=null，stop_reason=null。锁已释放，未启动其它资源任务。Dirac源码已准首跑；实际结果最终限定复核另记。未宣称系统通知、app关闭唤醒、用户已看/ACK或原program完成；原r14原生FAIL要由主后继组合、原userdata正式恢复验证，源码/确定性组件不追认该失败。

可复跑命令（仅出现新风险/失败时，不要求重复本批）：

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -I -B /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir <新ignored目录> -- /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -I -B /Users/denny/projects/simple_harness-typed-recall-context-use-full/.local-test-evidence/2026-09-06/prospective-runtime/run.py <新目录>/cases
```

实际source为7368c8cc；测试文件固定7c627fbc。首个测试通过main真实_activate_memory_analysis_lane/default compose及publicMemory写读；analysis以受控await隔离，无真实模型调用。最后重复cancel控制用受控Child/Index，证明父生命周期join，不扩大为SDK任意DB取消恢复。
