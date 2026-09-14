# Local DGX child observations

Last updated:2026-09-14. Parent remains GPT-6 Astra/high. Three scoped Sol children closed; only parent ran tests, SSH/API and native UI. Durations overlap; no matched-task savings claim.

| Child | Actual model/effort | Elapsed | Uncached input | Cached input | Output | Accepted / rework |
|---|---|---:|---:|---:|---:|---|
|Locke|gpt-5.6-sol/medium|108.3s|94561|761600|4290|Accepted bounded routing audit; no API or changes|
|Turing|gpt-5.6-sol/high|325.86s|191710|1209600|15108|HF estimator accepted after parent fixed Transformers5 return_dict=False, exact RequestGuard hook, typing; final105SDK checks pass|
|Ampere|gpt-5.6-sol/high|287.57s|60139|1112576|14253|Local source launcher default and frozen identity accepted; Host81checks pass; nativepending|

Parent subsequently repaired HF JSON arguments for real tool continuation, added HTTP-private opt-in and configured/pinned actual tokenizer. Coordination/rework wall time was not separately instrumented; do not subtract child times from total elapsed or claim token savings. Read-only child inherited substantial context: its94,561 uncached input for a108s audit is costly. Future similarly small audits should stay in parent; use bounded child input when interface permits.

Raw numeric evidence: `.local-test-evidence/2026-09-14/dgx-local-connect/usage-latest.json`; no messages or credentials copied.
