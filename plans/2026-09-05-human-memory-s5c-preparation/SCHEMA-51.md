# 提醒时间事件日志：隔离 schema51

最后更新：2026-09-06。只完成数据库扩展叶子；scheduler/source/presentation/main 默认运行接线仍在进行。

新 `s5c/043_prospective_time_signals_v51.sql` 追加 prospective_timer_events（11列、4索引、2个禁止修改/删除触发器），保存首时间观察、claim、handed_off、applied/invalidated。显式 initializer 从已验证50升级，在同一事务发布 DDL、恢复A类登记及围栏、迁移记录与版本51。旧50 SQL不变，用户当前默认49未在此切换；完成完整scheduler后再接默认运行。

S5cStore支持完整验证50或51的数据库，读store不会迁移。异常中断保留50或完整51，不出现半发布。发现独审P2：首次读50后另一initializer已发布51，旧table检查错误认作未发布schema；修复为再次验证完整51，其他版本仍拒绝。

测试只覆盖本次变动：首批4 passed/1.48s（初始化及并发重开、提交前/后中断、删除保护触发器后拒绝自动修补）；P2增加一个首次读取与并发发布的控制，1 passed。全部使用现有Python依赖及H075/M616，无新完整环境，无旧套件重跑。PG59211、59353均exit0，remaining=[]，cleanup_error=null。初步源码独审无P0/P1，P2固定复核待回。

原始证据仅本机，以下路径相对仓库根。

|文件|SHA256|
|---|---|
|.local-test-evidence/2026-09-06/timer51/r1/command.log|b0cb4dbe54caa2b3b68b14500340d70dddc1a9e4acf6f3619721ad384b940074|
|.local-test-evidence/2026-09-06/timer51/r1/resource.json|7c7c2f97a14bde6606cde0f4cdfa5898978ac617b699a5f014b583db0afd10e7|
|.local-test-evidence/2026-09-06/timer51/r2/command.log|59b9466559aeabf218a54ec041fd83cd357cf9bf1d80a17c47aab62ed0c205bf|
|.local-test-evidence/2026-09-06/timer51/r2/resource.json|20f13be56af8123ffa00db73c95c9b7deb50afacb3af2cded729d8e271478ef5|
