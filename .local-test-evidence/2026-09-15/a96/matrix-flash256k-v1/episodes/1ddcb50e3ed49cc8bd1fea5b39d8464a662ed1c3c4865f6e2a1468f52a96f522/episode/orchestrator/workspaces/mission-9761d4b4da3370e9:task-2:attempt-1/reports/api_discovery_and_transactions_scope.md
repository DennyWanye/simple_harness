# API Discovery & Venmo Sent-Transaction Scope

Task: mission-9761d4b4da3370e9:task-1 (discovery only; `complete_task` intentionally NOT called).

## 1. Environment / clock

- `datetime.datetime.now()` observed in the shared shell: **2023-05-18 12:00:00**; `datetime.date.today()` = **2023-05-18**.
- Therefore "this month" = **May 2023**, i.e. `created_at` in `[2023-05-01, 2023-05-31]`.

## 2. Public AppWorld app surface

`apis.api_docs.show_app_descriptions()` lists 12 apps:
`api_docs, supervisor, amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist`
(+ the `api_docs` helper itself).

- `apis.api_docs.show_api_descriptions(app_name='api_docs')` -> `show_app_descriptions`, `show_api_descriptions`, `show_api_doc`, `search_api_docs`.
- `apis.api_docs.show_api_descriptions(app_name='supervisor')` -> `show_active_task`, `complete_task`, `show_profile`, `show_addresses`, `show_payment_cards`, `show_account_passwords`.

## 3. Supervisor / user account

- `apis.supervisor.show_profile()`:
  - first_name: **Jose**, last_name: **Harrison**, email: **joseharr@gmail.com**, phone_number: 2474975253, birthday: 1985-12-15, sex: male.
- `apis.supervisor.show_account_passwords()` returned one credential per app; the Venmo one is:
  - account_name: **venmo**, password: `uNK8[nt`.
- `apis.supervisor.show_active_task()` instruction: "How many likes did all Venmo transactions, I sent this month, have in total?" (status null, answer `<<NOT_GIVEN>>`).
- There is exactly **one** Venmo account credential, so no account ambiguity for the Venmo scope.

## 4. Venmo query surface (relevant APIs)

`apis.api_docs.show_api_descriptions(app_name='venmo')` returned the full Venmo API list. Key ones for this goal:

- `venmo.login(username, password)` -> `{access_token, token_type}`. username is the account **email**.
- `venmo.show_account(access_token)` -> first/last name, email, registered_at, last_logged_in, verified, venmo_balance, friend_count.
- `venmo.show_transactions(access_token, query="", user_email=None, min_created_at="1500-01-01", max_created_at="3000-01-01", min_like_count=0, max_like_count=MAX, min_amount, max_amount, private=None, direction=None in ['sent','received'], page_index=0, page_limit=1..20, sort_by=None in {created_at, like_count, amount})`.
  - Each row: `transaction_id, amount, description, created_at, updated_at, private, like_count, payment_card_digits, comment_count, sender{name,email}, receiver{name,email}`.
  - `direction='sent'` filters to transactions the user sent.
- `venmo.show_transaction(transaction_id, access_token)` -> single transaction detail (same schema).
- `venmo.like_transaction` / `venmo.unlike_transaction` (POST) mutate likes; NOT used here (read-only discovery).

### Auth note (operational)
A `login` token captured in one `appworld_execute` call was rejected with HTTP 401 ("...access token is missing, invalid or expired") when reused in a later separate call. Re-running `venmo.login` and using the fresh `access_token` **within the same execution block** worked reliably. Downstream tasks should treat the Venmo `access_token` as short-lived / same-session and re-login as needed.

## 5. Identified Venmo account

`venmo.show_account` (fresh token): first_name **Jose**, last_name **Harrison**, email **joseharr@gmail.com**, registered_at 2022-12-14T14:46:35, last_logged_in 2022-12-14T14:46:35, verified True, venmo_balance 15268.0, friend_count 12.

## 6. Scope: transactions Jose SENT this month (May 2023)

Query used:
`venmo.show_transactions(access_token, min_created_at="2023-05-01", max_created_at="2023-05-31", direction="sent", page_index=0, page_limit=20)`.

Returned **12** transactions on page 0; page_index=1 returned `[]`, so the May-sent set is complete (12 rows). For reference, total sent ever (all dates) = 121 rows across months 2022-12 .. 2023-05.

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

All 12 rows have `sender.email = joseharr@gmail.com` (i.e., genuinely sent by the user).

Preliminary like count over this scoped set (raw observations, for task-2 to confirm):
`0+2+2+5+0+0+2+0+0+0+0+0 = 11`.

## 7. Findings summary

- Current month = **May 2023**.
- Venmo user = **Jose Harrison / joseharr@gmail.com** (single account; password from supervisor).
- User's sent-transaction scope for the month = **12 transactions** (ids listed above), each carrying a `like_count` field.
- Preliminary total likes over that scope = **11** (to be computed/verified by the aggregation task).
- `complete_task` NOT called by this discovery task.

## 8. Limitations / caveats

- Discovery used read-only Venmo APIs; no likes/transactions were mutated.
- The Venmo login token is not reusable across separate shell invocations (401 observed); re-login per session.
- "This month" is derived from the shell clock (2023-05-18) in America/UTC-agnostic form; interpreted as calendar May 2023.
