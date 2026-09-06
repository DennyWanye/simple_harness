# Applicability integration verification

Updated 2026-09-06. Main reviewed ecaeb50f/1f301147 public snapshot mapping and independent oracle; all nine raw hashes matched. Source had independent scoped ACCEPT; final evidence review remains separately tracked. Merged at048b72eb, all prior execution fingerprints retained.

Six affected integration tests passed: original applicability, three Procedure neighbors, trigger and context-use. Exact installed H073/M0613, shared resource runner2GiB/180s; PG43603, peak79728KiB, elapsed4.087s, exit0, remaining empty, cleanup_error null. Minimum sampled disk3510MiB. Original formal Run67db4f2a02d544db83a20da646f8e16e remains three PASS; this is not a new full401 run. No model, Host environment detection, or observed Procedure promotion.

Command: installed M0613 Python -B via primary scripts/run_resource_bounded.py; clear PYTHONPATH, disable plugin autoload, pytest -q -p no:cacheprovider -p pytest_asyncio.plugin selecting test_typed_recall_applicability_executor.py/test_typed_recall_procedure_public.py/test_typed_recall_trigger_public.py/test_typed_recall_context_use_bridge.py. Basetemp is the ignored r1-db sibling.

| Local ignored evidence | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/applicability-combined/r1/command.log | b87b4b0d207fa281f591ed2a96b2ff2ef549273f494255186a4cb9cc531b57cb |
| .local-test-evidence/2026-09-06/applicability-combined/r1/resource.json | f3e9c50a132e6add74d3fc1bdba3869a1d60bd9d37710a0ea66caab46b9ee421 |
