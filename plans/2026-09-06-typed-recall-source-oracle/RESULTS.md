# Source10 successor preparation

状态：NOT_RUN。源固定仅为Dirac只读挑战提供稳定diff，不代表通过验收。
主native/build独占共享锁，未启动pytest/新source run/模型/native；没有新PASS。
原626/fbeb的source10仍0PASS/0FAIL/10BLOCKED。

契约见[CONTRACT.md](CONTRACT.md)。固定source后等主释放统一slot，
再跑必要真实10格与独立反例；新结果按新源码单列，不覆盖旧证据。
`git diff --check`无空白错误，仅静态检查。

## Dirac只读挑战后继（仍NOT_RUN）

2b17未获ACCEPT：P1原证据错绑自洽hash、P2 request非strict JSON。
后继补原admitted+mutation spans回绑及交换/复用来源重hash反例、schema2/extra-key反例。
主已明确原CONFLICT_GROUP_CORRUPT语义类别映射；代码实际读取冻结expect/reopen/零披露字段，
保留真实outer/cause/frames，不修改SDK或fixture。
尚未执行正负例；fixed源码复交Dirac，只读不占Singer/native槽。

Dirac后继只读发现expected semantic source遗漏semantic_kind=claim；已补冻结字段，保留全内容/hash比较。仍NOT_RUN。
