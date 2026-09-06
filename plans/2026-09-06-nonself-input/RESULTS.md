# 非SELF来源事实片结果

最后更新：2026-09-06。Host产品0cf37197，严格kind窄修4405a2ac；契约3d73e606。Dirac已完成来源片源码审查，无确定P0/P1；最终新增实测待复核。

- r1：5FAIL/1.27s，全在fixture误用intended_audience=external_party被真实枚举拒绝，未达来源逻辑。原红保留，58247111只改fixture为external。
- r2：5PASS/1.94s。真实签名控制→S1/turn同TX→exact事实回读、政策换代后input拒绝/origin不变、原文本/声明重放冲突、无声明/无live认证/错hash/pointer拒绝、旧S1不升级、after_evidence_insert故障原子回滚。
- r3：2PASS/0.90s，5旧绿deselected。新增实际重新签名connection后原receipt/fact/admission lease保持，旧connection失效；kind=list稳定拒绝。合计7唯一，不将首次连接重读称进程重启。

命令：primary-m0615/venv/bin/python -I -B primary/scripts/run_resource_bounded.py --evidence-dir <本树>/.local-test-evidence/2026-09-06/nonself-input/rN -- <同Python> -I -B <本树>/.local-test-evidence/2026-09-06/nonself-input/run_source.py <rN>/cases；r3追加 -k 'resigned_reconnect or nonstring'。载体路径前缀见该ignored脚本，复用primary-078618/installed的H078/M618/S0313，Host从本树源码加载；没有新venv/install/模型/native或全成员扫描。

r1 PG6812 exit1/remaining[]；r2 PG6904 exit0/remaining[]、elapsed2.63s/peak186848KiB；r3 PG7027 exit0/remaining[]、elapsed1.527s/peak168896KiB。全部cleanup_error=null；默认锁释放。原log/resource/cases仅ignored。

仍缺：SDK实际item-level用途消费及调用审计、Host actualRun→当前turn→实际request physical证明；此片fact登记不是已授权使用，非SELF queued仍由原SDK history拒绝。当前只whole单项，240独立USER+public材料双项未接。不改旧memory分类，不放宽普通history/健康家庭/typed/short门。
