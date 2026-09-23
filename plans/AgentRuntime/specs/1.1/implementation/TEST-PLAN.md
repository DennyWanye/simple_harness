# 统一实施验收（主体后执行）

原60场景和16mutation原文保存在inputs并在sdk-cases.json全部继承；新增case:N01–N20对应Q01–Q20，不为固定60删原要求。case、seam、mutation使用显式前缀，避免Rxx混同。目标nodeid不是已存在或已执行；本地主体完成后收集实际nodeid、断言位置、candidate/source/wheel hash与证据。

## 1. 执行顺序

BODY_WIRED后：合约/生成资产检查→真实Store/Compiler/Commit与原runtime deterministic场景→16原mutation＋新增Q反例→stateful→legacy/相关完整回归→exact wheel→macOS原生UI→已登记Linux场景→真实模型4×3。Windows无环境保留PENDING，不伪称兼容。正式集成模型受当前批准profile约束，不修改聊天模型为方便测试。

允许仅stub外部Provider传输和可控测试目标来注入机制故障。不能stub授权producer、原UOW/Commit、TokenReceipt来源、ContextManifest持久writer、真实Journal/索引、正式Review结果为PASS。真实embedding/meter/model/platform项禁止以HashEmbedder/固定token值替代。

## 2. 关键负测/突变补充

N01错kind；N02part-scan写COMPLETE；N03省expectedref/rev冲突；N04root/critic绕wire；N05漏prior或双扣；N06超时另call；N07AllowAll包装receipt；N08kernel先drive；N09UNKNOWN换ordinal；N10逐group计N；N11跨gen混向量；N12平方溢出/短中文零命中；N13index已提交仍重复embed；N14lease到期当purge证明；N15probe正常更新全目录epoch；N16权限取并集；N17全filelist进模型；N18未知event未登记回放；N19OR REPLACE/旧root自动信任；N20collect-only填PASS。每项必须由相关实际行为断言发现，不把导入/语法错误算mutation kill。

## 3. Stateful

状态模型动作：create/submit/finish/group-close/index/append/rebuild/search-page/settings-update/skill-suspend/cancel/close/destroy/restart/revoke。比较参考简单全量算法与生产查询：输入预算与完整工具协议、完整N、来源版本/权限、固定snapshot、跨Agent隔离、同call成本不重置、已销毁不复活。初始200个生成序列×最多50动作作为可调整资源上限，记录seed和最小反例；这是验收配置不是需要写进Core的算法。

## 4. 原生UI首次用户路径

隔离Host源码/venv/userdata/端口，以当前集成candidate精确wheel装载；记录实际import位置/hash。Tauri单独管理backend/vite，不并起第二套服务。按顺序：创建Agent→输入→查ContextSummary→读历史两页→提交新policy→CAS更新→故意冲突→断线receipt重放→发现800文件Skillsummary/详情→加载instructions→执行获准script→撤销Skill后新调用拒绝→close(drain)→destroy→重连见PURGED。每步实际点击，截图/日志只存ignored，API脚本不代替UI。

当前macOS必须验原生；Linux地点由已有部署环境登记，不擅自新建远端/容器；至少exec SQL+FileGuard/close/rebuild/脚本停止+实际embedding进程。Windows条件未知保持PENDING，不以POSIX路径测试声称通过。

## 5. 真实模型12局（另一个runner，不混入离线用例）

四类各3次，先冻结实际profile/tokenizer/template/embedding资源、输入fixturehash、oracle、预算与故障点；失败全部保留，不挑选成功trial。

|场景|固定输入与允许轨迹|独立oracle|故障点|
|---|---|---|---|
| LM01 长上下文早期信息 | 生成固定seed会话10000条，其中早期登记工程暗号与约束；后面大量无关文本；请求解释最早约束 | literal事实来自原Journal且返source refs；记录N是否准确、跨Agent秘密不得出现 | 索引分2页完成，不许只搜索最后2000条 |
| LM02 settings/恢复 | 连续工具任务，Provider已PREPARED后修改普通policy；结束该请求再发下一步 | 旧hash原policy不变，新请求new adoption；raw/result费用幂等 | prepare后kill，再恢复原调用 |
| LM03 Skill权限 | 一个approved只读instruction+script fixture，结果有严格schema；另一Skill要求不存在权限 | 允许的输出真由原runner产生；未获权写入次数0；无endpoint/cred-ref泄露 | Skill加载后suspend，下一handoff拒绝 |
| LM04 销毁与迟到 | 两Agent各独立历史；A发查询/embedding后取消destroy，B继续 | A迟到结果费用可记录但不再写索引，B不泄漏A；正式pins不丢 | index提交前/后选择预登记一个kill切点 |

每局上限：原Provider调用24次、总计费token2,000,000、真实工具60次、embedding index/query批次128、wall30min；采用当前更紧限额。预算单位来自原实际ledger，不能因重启归零。512K不是每局都强塞：部署限制不足时报MODEL_CONTEXT_UNSUPPORTED并该项环境未验，不用短窗口替它填512K PASS。

硬不变量12/12必须通过（误授权、串Agent、重复效果、错误完成、请求hash漂移均0）。每类业务oracle至少2/3且全体至少10/12；不足保留FAIL分析，不换模型/样本补考覆盖原记录。阈值是本项目验收政策，不是模型性能保证。机制故障/环境缺失和模型诚实未解分别统计；skip不算PASS。人工独立审阅事实与UI/成本来源，不以模型自报成功评分。

## 6. 命令（仅主体完成后）

```bash
SDK="/path/to/actual-integrated-native-candidate"  # 用本地盘点结果，不替换dirty原树
HOST="/Users/denny/projects/simple_harness"
PY="$SDK/.venv/bin/python"
RUN="$(date +%Y%m%dT%H%M%S)-$$"
EVID="$HOST/.local-test-evidence/2026-09-23/arp-acceptance/$RUN"
test -x "$PY" && git -C "$HOST" check-ignore -q "$EVID" || exit 2
mkdir -p "$EVID"
cd "$SDK"
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/agent_runtime_plane -q \
  -o cache_dir="$EVID/pytest-cache" --junitxml="$EVID/arp.junit.xml" \
  > "$EVID/arp.log" 2>&1
```

测试目录是按本计划需实现的生产测试目标。没有目录应实施测试，不能命令失败后改为参考suite冒充。原继承nodeids和native/model runner另留实际receipt。当前自审不是独立生产代码核验；仅真正独立且可调用的reviewer完成后才能标独立审阅PASS。
