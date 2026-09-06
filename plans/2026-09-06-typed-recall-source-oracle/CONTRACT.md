# Source10 complete oracle — fixed draft, NOT_RUN

Base fbebdaffcb997d931ededc8105800723335db443，branch feat/typed-recall-source-oracle。
不修改SDK、fixture、401IDs、10sourceAC、阈值或原runner applicability WIP。
保留626/fbeb历史。当前主native持slot，本叶子只写代码/读旧证据，未执行新测试。

## 独立判定与采集边界

验证侧新typed_recall_source_oracle.py不import SDK，不调用/复制SDK验证函数；
既有adapter只在source层使用真实SQLite backend和真实事务fault injector。
读取同一SQLite read transaction中的sqlite_master、全部90张表（包含FTS影子表）、
column/PK描述、完整行、PK排序root、integrity与FK诊断。BLOB以原字节hex保真；
无显式PK表保留实际rowid，不省略不可见/nonfinal表。既有seven final投影继续保留作旧反例。

固定M0613f2 fresh结构：461个sqlite_master成员，schema摘要
51e4f27be1b89e789b013d4ef601ab7bcbfcf08ee8960bcb0d95b4e2bf76e733；
完整column/PK描述摘要07a79a0bc9e997c46618e14b8f502b111456a18b627b5de32e3a6bfc805708d8。
来自旧s00实际seed.sqlite只读结构盘点，仅绑定schema完整性，未拿产品内容作为业务gold。
业务expected仍从原固定claim、独立公开hash域、实际before及合法no-fault对照导出。
全snapshot发生在事务rollback/commit后；不能读取半事务自行拼接为已提交证据。

## 7 fault

- no-fault必须先满足原two-source/public结果、顺序、引文/hash/预算判定。
- before无typed recall rows；所有非final且非request/attempt的表完整不变，包括原证据、
  cognitive revisions/heads、authority epochs、classification、principal、index与schema。
- precommit immediate只有exact请求与attempt1允许新增；complete final仍等于before。
  request实际context/plan/principal/idempotency/deadline/hash和attempt hash/ordinal/关联必须完整。
- precommit recovery只允许追加attempt2并以其结算；原attempt1不得覆盖。
  complete decision/result/item行与独立检查的control绑定；terminal逐字段/JSON/hash绑定actual attempt2。
- postACK/restart：immediate与control全部表相等；recovery与immediate全部表相等；
  只有一个terminal，public replay exact且零candidate reads。
- 禁止统一old/new任选；完整PK/schema、request/result/decision/item/terminal关联缺一判FAIL。

## 3 corruption

保留原派生cognitive_conflict_members限定修改；不删除/重写S1原始证据。
改前两member(7/8)逐项绑定原incumbent/challenger内容、durable revision、全部evidence spans、
member hash、有序group hash、public confirmation identity。改后逐值核确为原指定修改，
所有其他表与schema不变；拒绝后全部原样，recall_calls=0。

现有M0613实际拒绝链：
- one-member：initialize_reopen中canonical conflict integrity，MemoryCorruptionError/
  conflict member cardinality differs。
- cross-memory：schema probe的FK破坏，外层MemoryLegacySchemaUnsupported/LEGACY_SCHEMA_UNSUPPORTED，
  必须保留cause MemoryCorruptionError/human-memory v7 foreign key check failed。
- three-member：schema probe的CHECK破坏，外层同上，cause必须是
  MemoryCorruptionError/human-memory v7 integrity check failed。

主2026-09-06明确决定：CONFLICT_GROUP_CORRUPT是原冻结语义类别，允许验证器将它映射到
M0613真实cause+exact拒绝层；不是修改SDK错误码。验证器实际读取原recall_cases对应项，
assert expect==CONFLICT_GROUP_CORRUPT、reopen_outcome==FAIL_CLOSED及三个零披露预期。
随后验证上述真实outer/cause/frames、限定member变更、其他表不变、零recall及拒绝后零写，
任何一项缺失仍FAIL。不能只凭异常存在PASS。

Dirac P1补齐：保留实际8次mutation source/history、实际ingest envelope/admission inputs。
r7/r8必须匹配原create_case不同evidence IDs；每条durable span逐字段映射原mutation EvidenceSpan，
复用独立check_seed_authority/check_admitted_span绑定真实envelope/receipt/hash/引文和durable envelope。
再计算完整evidence-set/member/group hash，避免错误来源自洽重hash通过。
P2补齐：request_json必须exact schema1及四字段schema_version/principal_id/context/plan，无额外键。

## 待slot验证

新test使用exact f2 clean checkout和M0613installed实际source adapter建立10格，
再对nonfinal、schema缺表、PK、request、attempt、terminal、item、rollback及member/group hash、
拒绝阶段/错误、额外写入和recall调用构造独立篡改。根hash重算也不能绕过业务判定。
测试与实际source10均等slot，经固定145baed3默认共享OS锁，2GiB/180s。
源先固定交Dirac只读挑战；未验证不把旧10BLOCKED改写为PASS。

## 其他122setup（本叶子不修）

llm_inference/unknown authoritative、verified_external非source_verified及观测procedure直接activate等，
当前public API禁止种子构造；明确为“可构造性与原预期冲突”，保留原格和旧BLOCKED。
seed拒绝不等于eligibility PASS。后继必须对照原允许状态/入口与原预期逐格决定，不在这里宽松化。
