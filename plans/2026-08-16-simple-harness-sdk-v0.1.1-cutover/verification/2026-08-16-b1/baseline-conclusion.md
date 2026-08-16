# Slice B baseline conclusion

- Product HEAD: `42fbbd0f1fd2e3cd8eeb0cde650df49ae48aa4a7`
- Command: `backend/.venv/bin/python -m pytest -q backend/tests/sdk_adapters backend/tests/test_workflow_deep_research_v2_recovery.py backend/tests/test_workflow_ppt_pro_graph.py`
- Result: `41 passed, 2 skipped in 8.89s`.
- The two skips are the known T6.1 adapter/conformance implementation placeholders; they must become executable PASS in Slice B.
- DeepResearch/PPT legacy strong oracles are green before product SDK adapter work.
- Raw JUnit index: `.local-test-evidence/2026-08-16/simple-harness-sdk/slice-b-b1/baseline-adapters-workflows.xml`
- Raw JUnit SHA-256: `35f9dfd6ef8353f0d3cdef045a59442841c5f5207774fd20b5fe08bba0b7c8e6`
