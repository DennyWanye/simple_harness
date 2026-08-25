# Testcase inventory and reuse report

## Inventory

- `testcase_inventory.py build` indexed 109 existing documents before this track and 117 after adding TC-PS-01～08.
- TO-A1～A8 / TO-R1～R5 obligation queries each returned `[]`; there was no active directly-bound candidate.
- The selected set is exactly TC-PS-01～08 revision 1. Historical PASS is not inherited; every case remains unexecuted for the final run.

## Full-text candidate review

| Candidate | Inventory state | Reusable content | Why it cannot be selected for these obligations |
|---|---|---|---|
| `2026-06-28-userdata-path-binding/manual-test.md` | `LEGACY-CCC0F7759682`, needs-review | isolated userdata, full restart and path drift evidence discipline | tests application userdata/config location, not Project identity or Session execution root |
| `2026-08-03-session-model-run-visibility/manual-test.md` | `LEGACY-E88B0289CA4F`, needs-review | Session restart, concurrent roots and real UI evidence discipline | project directory is a Run-time selection in that oracle; it does not prove immutable Session Binding |
| `workbench-ui/TC-WB-05-session-list.md` | `LEGACY-CF5B7E73EF5C`, needs-review | history switching, empty Session visibility, rename/delete smoke | expects a flat Session list and has no Project grouping or Inspector oracle |
| `workbench-ui/TC-WB-18-session-delete-edge.md` | `LEGACY-CA184A465634`, needs-review | deletion, restart non-resurrection and empty-state recovery | useful only as affected regression; it does not cover project migration or binding provenance |
| `2026-08-21-sdk-context-authority-cutover/testcases.md` | legacy document | frozen root/request identity, project/tool and restart evidence patterns | obligations and authority concern SDK Context snapshots rather than the new Project/Session binding |
| `2026-07-12-context-continuity/manual-test.md` | legacy document | same-Session history continuity | natural-language continuity is unrelated to deterministic project CRUD and root authority |

## Decisions

The machine source is `testcase-reuse-report.json`. All 13 decisions are `create-new` because no candidate is active and
none has the required Project Binding oracle. The eight new cases avoid duplication by combining TO-R1/R2/R3/R4/R5 with
their closest delivery chains TC-PS-02/04/05/08/01.

