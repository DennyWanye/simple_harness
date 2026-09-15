# Venmo API Recon (read-only)

Task: mission-1c2f7c6030c8d119:task-1
Scope: read-only discovery of public APIs + supervisor account discovery. No app data was modified.

## 0. Environment
- AppWorld app list (via `apis.api_docs.show_app_descriptions()`): api_docs, supervisor, amazon, phone, file_system, spotify, **venmo**, gmail, splitwise, simple_note, todoist.
- Shell date observed: **2023-05-18** (`from datetime import date, datetime`). Therefore the user's "this month" = **2023-05-01 .. 2023-05-31**.

## 1. Supervisor account discovery APIs (public)
- `apis.api_docs.show_api_descriptions(app_name='supervisor')` returns:
  show_active_task, complete_task, show_profile, show_addresses, show_payment_cards, show_account_passwords.
- `supervisor.show_profile()` — no params. Returns: first_name, last_name, email, phone_number, birthday, sex.
- `supervisor.show_account_passwords()` — no params. Returns list of {account_name, password}.
- `supervisor.show_active_task()` — no params. Returns {instruction, status, answer}.

### Observed values (read-only)
- profile: first_name="Jose", last_name="Harrison", email="joseharr@gmail.com", phone_number="2474975253", birthday="1985-12-15", sex="male".
- account_passwords: venmo password = `uNK8[nt` (other apps: amazon `EfWDm1a`, file_system `mv-l]Ud`, gmail `-Uf_)OB`, phone `b4GXZH6`, simple_note `g{WPN$i`, splitwise `+xGGeSF`, spotify `rvP5MvY`, todoist `Ibi7M7b`).
- active_task instruction: "How many likes did all Venmo transactions, I sent this month, have in total?" status=null, answer="<<NOT_GIVEN>>".

### Simulated user identity
- Venmo account email / login username: **joseharr@gmail.com**
- Venmo password: **uNK8[nt**

## 2. Venmo APIs relevant to the goal
Discovered via `apis.api_docs.show_api_descriptions(app_name='venmo')` (name -> description):
- auth: login, logout, signup, delete_account, send_verification_code, verify_account, send_password_reset_code, reset_password
- account/profile: show_account, update_account_name, show_profile, search_users, search_friends, add_friend, remove_friend
- money/balance: show_venmo_balance, add_to_venmo_balance, withdraw_from_venmo_balance, show_bank_transfer_history, download_bank_transfer_receipt
- transactions: **show_transactions**, show_transaction, update_transaction, create_transaction, download_transaction_receipt
- likes/comments: **like_transaction**, unlike_transaction, show_transaction_comments, create_transaction_comment, show_transaction_comment, delete_transaction_comment, update_transaction_comment, like_transaction_comment, unlike_transaction_comment
- payment requests: show_received_payment_requests, show_sent_payment_requests, create_payment_request, delete_payment_request, update_payment_request, approve_payment_request, deny_payment_request, remind_payment_request
- social/notifications: show_social_feed, show_notifications, delete_notifications, mark_notifications, show_notifications_count, delete_notification, mark_notification

### 2.1 venmo.login  (required before any authenticated call)
- Path/method: POST /auth/token
- Parameters: `username` (required, = account email), `password` (required).
- Returns: {access_token, token_type}. Use access_token as the `access_token` argument for all other Venmo APIs.

### 2.2 venmo.show_account
- Path/method: GET /account
- Parameters: `access_token` (required).
- Returns: first_name, last_name, email, registered_at, last_logged_in, verified, venmo_balance, friend_count.

### 2.3 venmo.show_transactions  (PRIMARY API for the goal)
- Path/method: GET /transactions
- Parameters:
  - `access_token` (string, **required**)
  - `query` (string, optional, default "")
  - `user_email` (string, optional; email format) — restrict to transactions with that user
  - `min_created_at` (string, optional, default "1500-01-01") — **format YYYY-MM-DD**
  - `max_created_at` (string, optional, default "3000-01-01") — **format YYYY-MM-DD**
  - `min_like_count` (integer, optional, default 0)
  - `max_like_count` (integer, optional, default 9223372036854775807)
  - `min_amount` (number, optional, default 0, constraint > 0.0)
  - `max_amount` (number, optional, default 9223372036854775807, constraint > 0.0)
  - `private` (boolean, optional, default null)
  - `direction` (string, optional) — **constraint: value in ['sent','received']**; skipped if not passed
  - `page_index` (integer, optional, default 0, >= 0)
  - `page_limit` (integer, optional, default 5, **1 <= value <= 20**)
  - `sort_by` (string, optional) — valid: created_at, like_count, amount (prefix +/-)
- Response item fields (success is a list of): transaction_id, amount, description, created_at, updated_at, private, **like_count**, payment_card_digits, comment_count, sender{name,email}, receiver{name,email}.
- Note: `like_count` is the number of likes on a transaction; the goal asks to sum `like_count` over sent transactions this month.

### 2.4 venmo.show_transaction
- Path/method: GET /transactions/{transaction_id}
- Parameters: `transaction_id` (integer, required), `access_token` (required).
- Returns same single-object schema as show_transactions items.

### 2.5 venmo.show_social_feed
- Path/method: GET /social_feed
- Parameters: `access_token` (required), `page_index` (default 0), `page_limit` (default 5, 1..20).
- Returns list of friends' transactions (same fields minus payment_card_digits).

## 3. Verified example calls (read-only)

```
# 1) Discover apps / apis
apis.api_docs.show_app_descriptions()
apis.api_docs.show_api_descriptions(app_name='venmo')
apis.api_docs.show_api_descriptions(app_name='supervisor')
apis.api_docs.show_api_doc(app_name='venmo', api_name='show_transactions')

# 2) Account discovery
apis.supervisor.show_profile()            # -> email joseharr@gmail.com
apis.supervisor.show_account_passwords()  # -> venmo password uNK8[nt
apis.supervisor.show_active_task()

# 3) Venmo auth
tok = apis.venmo.login(username="joseharr@gmail.com", password="uNK8[nt")["access_token"]

# 4) Account info
apis.venmo.show_account(access_token=tok)

# 5) The goal query: sent transactions this month
apis.venmo.show_transactions(
    access_token=tok, direction="sent",
    min_created_at="2023-05-01", max_created_at="2023-05-31",
    page_index=0, page_limit=20)
```

## 4. Observed data existence (read-only confirmation)
`show_transactions(access_token=tok, direction="sent", min_created_at="2023-05-01", max_created_at="2023-05-31", page_index=0, page_limit=20)`
returned **12** sent transactions, all with sender.email == joseharr@gmail.com:

| transaction_id | created_at | like_count | receiver.email |
|---|---|---|---|
| 1042 | 2023-05-16T05:08:44 | 0 | robertmartinez@gmail.com |
| 1045 | 2023-05-11T20:42:28 | 2 | mel.bailey@gmail.com |
| 1063 | 2023-05-05T17:26:15 | 2 | chris.mcco@gmail.com |
| 1067 | 2023-05-04T16:12:14 | 5 | william_mart@gmail.com |
| 1082 | 2023-05-01T12:00:13 | 0 | bradley_ball@gmail.com |
| 1086 | 2023-05-01T20:41:55 | 0 | ta.weav@gmail.com |
| 1087 | 2023-05-03T20:28:58 | 2 | ta.weav@gmail.com |
| 2642 | 2023-05-10T15:22:59 | 0 | mi.burch@gmail.com |
| 4492 | 2023-05-18T04:33:46 | 0 | ta.weav@gmail.com |
| 5923 | 2023-05-13T01:44:49 | 0 | robertmartinez@gmail.com |
| 6266 | 2023-05-01T01:14:31 | 0 | mel.bailey@gmail.com |
| 7418 | 2023-05-12T13:18:22 | 0 | gina-ritter@gmail.com |

(Sum of like_count = 0+2+2+5+0+0+2+0+0+0+0+0 = 11 — this is an observation for the downstream summing task, not the recon deliverable.)

## 5. Interface contract for downstream tasks
- Authenticate with `apis.venmo.login(username="joseharr@gmail.com", password="uNK8[nt")` and reuse the returned `access_token`.
- Query with `apis.venmo.show_transactions(access_token=<tok>, direction="sent", min_created_at="2023-05-01", max_created_at="2023-05-31", page_index=<n>, page_limit=20)`.
- `page_limit` max is 20, so if a page returns exactly 20 rows, continue with page_index+1 until a short/empty page to avoid missing transactions (pagination completeness check).
- Aggregate `like_count` over all returned items to get total likes.
- Read-only: no writes were performed in this recon; no data was modified.

## 6. Limitations / notes
- "This month" derived from observed shell date 2023-05-18; if executed on a different date the month bounds must be recomputed.
- No dedicated "month" parameter exists; use min_created_at/max_created_at with YYYY-MM-DD.
- The 12-row result used page_limit=20 (single page, < 20), but downstream should still verify pagination.
