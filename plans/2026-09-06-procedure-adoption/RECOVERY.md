# Procedure 观察恢复与适用性后继

2026-09-06。基于Host5e513eda / Memorydb7ca22，源组合候选，未构建后继包。旧M618和v3/v4/v5分类协议不变。

## 固定边界

Host保留53全部DDL/registry/行，新显式54仅增加 append-only `procedure_observation_attempts`（authority_id/use_id/ordinal/previous_authority_id/previous_ref_hash/body_json/body_hash）；完整53校验后同事务登记、fence及版本发布。startup、sharedwriter、旧timer的54路径调用完整validator，未知版本仍拒绝。旧prepared为第一尝试，后续尝试链有界128；resolver仍可回读旧ref。默认生产工厂要求SDK恢复能力版本1后升级54。

每次后台观察先公共record原ref。已消费且Host丢ACK时原receipt重放，不重发Provider。仅过期/旧revision/资格窗口变化且SDK真实核旧ref未消费时重新prepare；原来源、Run、操作、步骤、时间不改。实际旧ref、SDK prepare及来源哈希先持久追加，CAS并发只采纳第一个已持久尝试。每次worker最多一次恢复，失败交下一次有界访问，不循环签grant。

SDK无DDL。read_procedure_use_target新增显式allow_observation_rebase=False；prepare同选项另有previous_reference=None。默认严格current revision不变。显式rebase最多128个连续revision，逐个验证真实observation consumption/result及完全同定义、owner、qualification epoch；REVISE或非observation来源拒绝。过期旧authority只作为经真实resolver回读的历史来源，不是假时间验证许可。prepare和consume重新核目标与证据suppression；已经forget不能被旧未消费grant覆盖。公共成功/拒绝仍沿现operation observation及Host sidecar，完整新参数/ref进入request hash。

使用前重新读SDK定义/epoch及Host实际工具身份、workspace inode指纹。并发两Scope可以绑定同旧revision但只能在真实observation链上追加各自独立成功；同Scope重复终态不能增加资格。高risk不自动激活，90天/三独立Scope/低risk可逆规则未改。

## 新控制范围

本叶分批通过，原失败保留（见[结果](RECOVERY-RESULTS.md)）：SDK真实observation链/真实REVISE拒绝、旧ref续期/已消费拒绝、prepare后forget（三项）；Host54回滚/旧registry与完整fence、真实已prepared timer两项；实际Scope过期重开/已消费lostACK两项、两个旧revision Scope、同Scope重复、工具/目录漂移两项、高risk三Scope（七项）。不以源码或测试数量称native/TC-HM04完成。

仍缺首次UNBOUND草稿的产品发现、自动失败归因及实际模型/native全链。F01延期。版本由主与Hegel统一整合，不各自产包。
