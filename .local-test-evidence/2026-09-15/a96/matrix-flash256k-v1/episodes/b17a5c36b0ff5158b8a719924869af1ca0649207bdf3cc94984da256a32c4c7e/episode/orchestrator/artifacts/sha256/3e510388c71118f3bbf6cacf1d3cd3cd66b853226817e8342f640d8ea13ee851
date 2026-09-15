# Venmo — Total likes on transactions I "sent" this month

Task: mission-1c2f7c6030c8d119:task-2 (attempt-2)
Goal: Sum the `like_count` of all Venmo transactions the user SENT this month.
Deliverable path: recon/venmo_likes_result.md
Result: **TOTAL LIKES = 11** across **12** sent transactions in May 2023.

> Re-verification note: attempt-1 was rejected by rule_check only because its
> `used_knowledge` referenced a knowledge ID that is now **SUPERSEDED**
> (`appworld-api:21eb3fbf5948b5f51e8501782b8ac4a70a8d21453e0dd5590e7583b9c776f6aa`;
> current version `appworld-world:world_version:11`). No verified knowledge was
> used as a fact in this attempt: all business facts below were **re-observed
> live via public APIs in this run**. No knowledge ID is cited as a premise.

## 1. Environment / identity (confirmed this run)
- Shell date observed (`from datetime import date`): **2023-05-18** → "this month" = **2023-05-01 .. 2023-05-31**.
- Month bounds computed from the current date, not hard-coded.
- User identity from `apis.supervisor.show_profile()`: first_name="Jose", last_name="Harrison",
  email="joseharr@gmail.com", phone_number="2474975253", birthday="1985-12-15", sex="male".
- Venmo password from `apis.supervisor.show_account_passwords()`: account_name="venmo", password="uNK8[nt".
- Venmo auth: `apis.venmo.login(username="joseharr@gmail.com", password="uNK8[nt")`
  → access_token (str, len 149).
- `apis.venmo.show_account(access_token=tok)` confirmed the logged-in account:
  `{'first_name': 'Jose', 'last_name': 'Harrison', 'email': 'joseharr@gmail.com', 'registered_at': '2022-12-14T14:46:35', 'last_logged_in': '2022-12-14T14:46:35', 'verified': True, 'venmo_balance': 15268.0, 'friend_count': 12}`

## 2. Executed API calls (parameters + raw results)

### 2.1 Primary query (the goal)
```
apis.venmo.show_transactions(
    access_token=tok, direction="sent",
    min_created_at="2023-05-01", max_created_at="2023-05-31",
    page_index=0, page_limit=20)
```
Raw result: **12** items (page 0, count 12; since 12 < 20 the result set is complete for this filter).
Fields per item: transaction_id, amount, description, created_at, updated_at, private, like_count,
payment_card_digits, comment_count, sender{name,email}, receiver{name,email}.

### 2.2 Per-transaction detail (verbatim from responses)
| # | transaction_id | created_at | like_count | sender.email | receiver.email |
|---|---|---|---|---|---|
| 1 | 1042 | 2023-05-16T05:08:44 | 0 | joseharr@gmail.com | robertmartinez@gmail.com |
| 2 | 1045 | 2023-05-11T20:42:28 | 2 | joseharr@gmail.com | mel.bailey@gmail.com |
| 3 | 1063 | 2023-05-05T17:26:15 | 2 | joseharr@gmail.com | chris.mcco@gmail.com |
| 4 | 1067 | 2023-05-04T16:12:14 | 5 | joseharr@gmail.com | william_mart@gmail.com |
| 5 | 1082 | 2023-05-01T12:00:13 | 0 | joseharr@gmail.com | bradley_ball@gmail.com |
| 6 | 1086 | 2023-05-01T20:41:55 | 0 | joseharr@gmail.com | ta.weav@gmail.com |
| 7 | 1087 | 2023-05-03T20:28:58 | 2 | joseharr@gmail.com | ta.weav@gmail.com |
| 8 | 2642 | 2023-05-10T15:22:59 | 0 | joseharr@gmail.com | mi.burch@gmail.com |
| 9 | 4492 | 2023-05-18T04:33:46 | 0 | joseharr@gmail.com | ta.weav@gmail.com |
| 10 | 5923 | 2023-05-13T01:44:49 | 0 | joseharr@gmail.com | robertmartinez@gmail.com |
| 11 | 6266 | 2023-05-01T01:14:31 | 0 | joseharr@gmail.com | mel.bailey@gmail.com |
| 12 | 7418 | 2023-05-12T13:18:22 | 0 | joseharr@gmail.com | gina-ritter@gmail.com |

Sum: 0+2+2+5+0+0+2+0+0+0+0+0 = **11**
Unique transaction_id count = 12 (no duplicates).

### 2.3 Pagination completeness check
- `direction="sent"` + month bounds, `page_index=1, page_limit=20` → **0 items** (`[]`),
  confirming page 0 already returned the full set.

### 2.4 Independent cross-check (no `direction` filter)
```
apis.venmo.show_transactions(access_token=tok,
    min_created_at="2023-05-01", max_created_at="2023-05-31",
    page_index=0/1, page_limit=20, sort_by="-created_at")
```
- Page 0 = 20 rows, page 1 = 14 rows → 34 total rows for May (sent + received).
- Filtering `sender.email == "joseharr@gmail.com"` → **12** rows; sum `like_count` = **11**;
  ids = [1042, 1045, 1063, 1067, 1082, 1086, 1087, 2642, 4492, 5923, 6266, 7418]
  (identical to the primary query).
- Both independent methods (direction filter vs. sender-email filter) agree: 12 transactions, sum 11.

## 3. Result
**Total likes on Venmo transactions the user sent this month (May 2023) = 11.**

## 4. Errors encountered and handling
- No errors occurred in this attempt. (attempt-1 had a read-only 422 for an invalid `sort_by`
  value with no "+/-" prefix; retried with `sort_by="-created_at"`. Not repeated here.)

## 5. Method / limitations
- "This month" derived from the observed shell date 2023-05-18 → May 2023. If run on a different
  date the bounds must be recomputed.
- No dedicated "month" parameter; used `min_created_at`/`max_created_at` (YYYY-MM-DD).
- `like_count` is the number of likes on a transaction; summed over all 12 sent items.
- All calls in this task were read-only (login + show_transactions); no application data was modified.
- Evidence scope: only the Venmo API responses recorded here. `page_limit` ≤ 20 satisfies the API max.
