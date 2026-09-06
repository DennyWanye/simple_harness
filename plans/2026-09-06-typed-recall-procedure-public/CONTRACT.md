# Procedure公开applicability最小后继

2026-09-06；base5819b15e；复用source-oracle worktree，分支feat/typed-recall-procedure-public。
初次源码冻结时只做准备、未运行测试，无新venv/checkout。其后Dirac限定ACCEPT；
主授权默认锁下已完成3方法及原4格，实际结果以[RESULTS.md](RESULTS.md)为准。
H073/M0613固定artifact不变。原401/阈值/原32禁止组合/Prospective/旧未跟踪applicability均不改。

## 实施范围

- CaseManager新增可信ProcedureObservationAuthorityPort；新typed_recall_procedure_cases.py只发
  APPLICABILITY_SNAPSHOT，不提供terminal_receipt/outcome，不声称物理Tool成功。
- 合成fixture的独立USER applicability声明经原公开admit+conversation registration持久绑定同subject/run/task。
  task_id为fixture输入，不伪造Host production scope。原tool/version保持；environment与空object输入schema
  明确为fixture issuer自身输入。只用于SDK公开能力证明，非真实git运行。
- 原explicit_user active（lifecycle/epistemic）走CREATE→snapshot→实际revision+1→同fingerprint recall。
  新声明是独立S1，不替换原记忆来源；RecallContext evidence refs包含二者，typed item仍须绑定原source内容/证据。
- eligible原literal/冻结INELIGIBLE不变；输入compiler显式路径draft→eligible，wire将后者映射
  eligible_for_activation，以已有exact memory action grant执行REVISE，随后snapshot不改变state。
  此路径只说明显式用户授权的状态写入，绝不算自动观察晋升。若公开写入拒绝，保留实际setup失败，不改SDK绕过。
- observed_behavior晋升本片不做；没有实际terminal链，不能仅在fixture签一个成功receipt。

## 独立oracle

normal_expected显式识别ELIGIBLE_WITH_APPLICABILITY/ELIGIBLE_WITH_TRIGGER_SIGNAL；默认未具证明为false，
实际assess仅将已验证procedure proof传入；Prospective仍BLOCKED。
新typed_recall_procedure_oracle.py无SDK import，从原tool/version、固定schema、admitted原字节、
exact注册task/run、intent/ref/authority/result/hash及重放关系验证适用性，不用SQLite或SDK内部验证函数。
原完整mutation/source/ref、typed result/projection/score/candidate-read/replay断言保留；
来源revision改为实际snapshot result的committed_revision，而非借用旧CREATE revision。
输入中的tool/version对象与SDK已有Procedure payload list(tool@version)作显式结构适配；
projection原格不纳入此能力，不宣称关闭完整projection/hash门。
任何public setup异常仍BLOCKED，不能当召回负例通过；32禁止组合保持原construction-conflict事实。

## 原定必要验证（现已执行，见RESULTS）

新增tests/test_typed_recall_procedure_public.py三个测试方法：
1. 四原格（active、eligible、explicit_user/source_bound、explicit_user/user_confirmed）真实public+独立oracle；
   exact target/run/伪terminal三篡改须命中intent检查。
2. 匹配fingerprint选中、错fingerprint真实no_recall；close/reopen同request零candidate read，新request真实revision。
3. 条件型expected及eligible原literal/原否定预期不变。

源码先交Dirac只读挑战，未测不声称可用；获slot后用既有installed解释器、145共享锁入口，2GiB/180s，
先必要测试再原格正式定向执行。raw仍ignored，RESULTS与ARCH通过后更新；不重跑source10。

## 合并边界

自有改动：CaseManager、新procedure adapter/oracle/test、normal_cases最小dispatch、normal_inputs仅eligible路径、
a2仅normal_expected/normal_projection/assess_normal、bridge仅新增两文件copy/hash inventory。
未修改context-use实现；合Hegel时bridge代码清单应并存双方新增文件，a2各自函数并存。
本契约不是Program完成，也不把旧182与source10并成新完整run。
