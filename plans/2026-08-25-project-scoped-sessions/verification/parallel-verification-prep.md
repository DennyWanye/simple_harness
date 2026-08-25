# Parallel verification preparation status

- Inventory: built, 117 testcase documents indexed after adding TC-PS-01～08.
- Candidate review: six relevant legacy/full-text assets reviewed; none active/selectable for new obligations.
- Reuse decisions: 13/13 recorded, all `create-new` with incremental reason; history PASS not inherited.
- Required black-box set: 8 active revision-1 testcases, one per MUST AC, five also close change risks.
- Challenger: two sequential rounds, both PASS; no open required obligation.
- Fixtures: isolated user-data/project setup, exact-marker cleanup, three-level surface-smoke draft.
- Evidence root: `.local-test-evidence/<YYYY-MM-DD>/<run-id>/<scenario-id>/`; no raw evidence in Git.
- Expensive prerequisites: current exact desktop build; macOS Computer Use; Windows 10/11 runner for identity probe;
  isolated user-data; test Provider login entered by operator without capture; no pre-existing backend/Vite instance.
- Not executed here: implementation tests, real UI, restart, migration, Windows probe. This track defines/finalizes oracles only.
