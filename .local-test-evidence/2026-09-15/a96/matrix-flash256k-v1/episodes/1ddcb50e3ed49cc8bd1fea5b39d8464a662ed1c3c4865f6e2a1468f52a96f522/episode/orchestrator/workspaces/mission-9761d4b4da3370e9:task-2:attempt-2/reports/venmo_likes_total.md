# Venmo Likes Total — Sent Transactions This Month

Task: `mission-9761d4b4da3370e9:task-2` (aggregation only; `apis.supervisor.complete_task()` intentionally NOT called).

## Answer

**Total likes on all Venmo transactions the user sent this month = 11** (over **12** transactions).

## Month basis ("this month")

- Shared-shell clock observed: `datetime.datetime.now()` = **2023-05-18 12:00:00**.
- Therefore "this month" = **May 2023**, i.e. `created_at` in the calendar interval `[2023-05-01, 2023-05-31]` (inclusive).
- Boundary verified against live data: the oldest captured row is `2023-05-01T01:14:31` (id 6266); the immediately older sent row is `2023-04-30T16:29:14` (id 1062, April). So no May transactions are cut off by the lower bound.

## User / account basis

- Supervisor profile: **Jose Harrison / joseharr@gmail.com**.
- Single Venmo credential (`account_name = "venmo"`) obtained from `apis.supervisor.show_account_passwords()`; logged in via `apis.venmo.login(username="joseharr@gmail.com", password=<credential>)`.
- `apis.venmo.show_account` confirms email `joseharr@gmail.com`, venmo_balance 15268.0, verified True.
- All 12 scoped rows have `sender.email = joseharr@gmail.com` (genuinely sent by the user).

## Evidence: scoped transactions (May 2023, direction = "sent")

Query: `apis.venmo.show_transactions(access_token=<fresh>, min_created_at="2023-05-01", max_created_at="2023-05-31", direction="sent", page_index=0, page_limit=20)` → 12 rows; `page_index=1` → `[]` (set complete).

| # | transaction_id | created_at | amount | like_count | private | receiver |
|---|----------------|------------|--------|-----------|---------|----------|
| 1 | 1042 | 2023-05-16T05:08:44 | 27.0 | 0 | false | Robert Martinez |
| 2 | 1045 | 2023-05-11T20:42:28 | 97.0 | 2 | true | Melissa Bailey |
| 3 | 1063 | 2023-05-05T17:26:15 | 15.0 | 2 | true | Chris Mccoy |
| 4 | 1067 | 2023-05-04T16:12:14 | 31.0 | 5 | false | William Martinez |
| 5 | 1082 | 2023-05-01T12:00:13 | 19.0 | 0 | true | Bradley Ball |
| 6 | 1086 | 2023-05-01T20:41:55 | 39.0 | 0 | false | Tammy Weaver |
| 7 | 1087 | 2023-05-03T20:28:58 | 280.0 | 2 | true | Tammy Weaver |
| 8 | 2642 | 2023-05-10T15:22:59 | 143.0 | 0 | false | Michael Burch |
| 9 | 4492 | 2023-05-18T04:33:46 | 13.0 | 0 | false | Tammy Weaver |
| 10 | 5923 | 2023-05-13T01:44:49 | 21.0 | 0 | false | Robert Martinez |
| 11 | 6266 | 2023-05-01T01:14:31 | 93.0 | 0 | false | Melissa Bailey |
| 12 | 7418 | 2023-05-12T13:18:22 | 28.0 | 0 | false | Gina Burch |

**Transaction count = 12.**

## Like evidence / arithmetic

like_count values: `0, 2, 2, 5, 0, 0, 2, 0, 0, 0, 0, 0`

Sum = 2 + 2 + 5 + 2 = **11**.

Cross-checks:
- `apis.venmo.show_transactions(..., direction="sent", sort_by="-created_at")` returned the same 12 May rows contiguously (ids 4492, 1042, 5923, 7418, 1045, 2642, 1063, 1067, 1087, 1086, 1082, 6266), then fell over to April rows — no extra May rows beyond the 12.
- Individual `apis.venmo.show_transaction` spot-checks matched the list values: id 1042 → 0, id 1067 → 5, id 1087 → 2, id 4492 → 0, id 7418 → 0.

## Actions performed

1. `apis.supervisor.show_account_passwords()` → Venmo credential.
2. `apis.venmo.login(email, password)` → fresh access_token (re-login per shell session; tokens do not survive across separate `appworld_execute` calls — HTTP 401 observed historically).
3. `apis.venmo.show_account(access_token)` → confirmed identity.
4. `apis.venmo.show_transactions(direction="sent", min_created_at="2023-05-01", max_created_at="2023-05-31")` paged → 12 rows.
5. Cross-check via `sort_by="-created_at"` full sent list and per-transaction `show_transaction` spot checks.
6. `apis.supervisor.show_active_task()` re-read to confirm the instruction (read-only).
7. Wrote this report.

No likes/transactions were mutated. `apis.supervisor.complete_task()` was **NOT** called.

## Unresolved caveats / limitations

- **Month basis is derived from the shell clock** (`2023-05-18`), not from any explicit task-provided period. If the intended "month" were defined by a different timezone or a rolling 30-day window, the set could differ. Under calendar-May interpretation the answer is 11.
- **Auth token lifetime**: the Venmo `access_token` is session-scoped; the value shown in one shell call is not reused here. All figures above come from fresh logins within their own execution blocks.
- The retrieval-layer "VERIFIED" knowledge entry describing the active-task instruction could not be re-read as current in this Mission (`knowledge_read` returned "not current or not available"). The instruction was instead re-confirmed directly via the live `apis.supervisor.show_active_task()` public API; that entry is therefore **excluded** from `used_knowledge`.
- The `like_count` field is read from Venmo's transaction objects as-is; the report does not independently reconstruct who liked (no per-like actor list was queried).
