# B_feed.md — Task-2: Social feed extraction (live AppWorld)

Scope: read the user's **venmo** social feed in the live simulated world and extract every
**yesterday** transaction in which a **coworker pays the manager** (i.e. every dinner-related
transaction except the user's own). No mutation was performed; all calls were read-only.

## Method / inputs
- App: `venmo`. User account: **Ashlee Martinez**, venmo email/login `ashlee_martinez@gmail.com`.
- Auth: `apis.venmo.login(username='ashlee_martinez@gmail.com', password='F4s1Idj')` (read-only).
- World date observed: `apis.phone.get_current_date_and_time()` -> **Thursday, May 18, 2023**.
  Therefore **yesterday = 2023-05-17** (per Task-1 A_discovery.md).
- Feed read: `apis.venmo.show_social_feed(access_token=..., page_index=<0..>, page_limit=20)`
  paginated until empty (page_limit max is 20; 422 error if larger). Total feed rows observed: **820**.
- The manager is identified as **Spencer Powell <spencer.powell@gmail.com>** — the single receiver
  of all Azure Harbor Bistro dinner payments below.

## Observed yesterday dinner payments (coworker -> manager)
Filtered feed rows with `created_at` starting `2023-05-17` whose description contains
"Azure Harbor Bistro". All six share `created_at = 2023-05-17T14:46:15` and receiver
`spencer.powell@gmail.com`.

| # | transaction_id | payer (sender) | payer email | amount | timestamp (created_at) | note/memo (description) |
|---|----------------|----------------|-------------|--------|------------------------|--------------------------|
| 1 | 8216 | Jordan Harrison | jo-harr@gmail.com | 29.0 | 2023-05-17T14:46:15 | Dinner at Azure Harbor Bistro |
| 2 | 8217 | Angela Riddle | angriddle@gmail.com | 20.0 | 2023-05-17T14:46:15 | Azure Harbor Bistro |
| 3 | 8218 | Adam Blackburn | ad.blackburn@gmail.com | 42.0 | 2023-05-17T14:46:15 | Azure Harbor Bistro |
| 4 | 8219 | Jeffrey Smith | jefsmith@gmail.com | 44.0 | 2023-05-17T14:46:15 | Food at Azure Harbor Bistro |
| 5 | 8220 | Connor Brown | connorbrow@gmail.com | 23.0 | 2023-05-17T14:46:15 | Food at Azure Harbor Bistro |
| 6 | 8221 | Glenn Burton | glenn.burton@gmail.com | 31.0 | 2023-05-17T14:46:15 | Dinner at Azure Harbor Bistro |

Subtotal of these six coworker payments (recorded here as an intermediate fact only; final
integration/sum belongs to a later task): 29 + 20 + 42 + 44 + 23 + 31 = **189.0**.

### Raw API observations (exact feed rows, verbatim)
```
{"transaction_id": 8216, "amount": 29.0, "description": "Dinner at Azure Harbor Bistro", "created_at": "2023-05-17T14:46:15", "updated_at": "2023-05-17T14:46:15", "private": false, "like_count": 0, "comment_count": 0, "sender": {"name": "Jordan Harrison", "email": "jo-harr@gmail.com"}, "receiver": {"name": "Spencer Powell", "email": "spencer.powell@gmail.com"}}
{"transaction_id": 8217, "amount": 20.0, "description": "Azure Harbor Bistro", "created_at": "2023-05-17T14:46:15", "updated_at": "2023-05-17T14:46:15", "private": false, "like_count": 0, "comment_count": 0, "sender": {"name": "Angela Riddle", "email": "angriddle@gmail.com"}, "receiver": {"name": "Spencer Powell", "email": "spencer.powell@gmail.com"}}
{"transaction_id": 8218, "amount": 42.0, "description": "Azure Harbor Bistro", "created_at": "2023-05-17T14:46:15", "updated_at": "2023-05-17T14:46:15", "private": false, "like_count": 0, "comment_count": 0, "sender": {"name": "Adam Blackburn", "email": "ad.blackburn@gmail.com"}, "receiver": {"name": "Spencer Powell", "email": "spencer.powell@gmail.com"}}
{"transaction_id": 8219, "amount": 44.0, "description": "Food at Azure Harbor Bistro", "created_at": "2023-05-17T14:46:15", "updated_at": "2023-05-17T14:46:15", "private": false, "like_count": 0, "comment_count": 0, "sender": {"name": "Jeffrey Smith", "email": "jefsmith@gmail.com"}, "receiver": {"name": "Spencer Powell", "email": "spencer.powell@gmail.com"}}
{"transaction_id": 8220, "amount": 23.0, "description": "Food at Azure Harbor Bistro", "created_at": "2023-05-17T14:46:15", "updated_at": "2023-05-17T14:46:15", "private": false, "like_count": 0, "comment_count": 0, "sender": {"name": "Connor Brown", "email": "connorbrow@gmail.com"}, "receiver": {"name": "Spencer Powell", "email": "spencer.powell@gmail.com"}}
{"transaction_id": 8221, "amount": 31.0, "description": "Dinner at Azure Harbor Bistro", "created_at": "2023-05-17T14:46:15", "updated_at": "2023-05-17T14:46:15", "private": false, "like_count": 0, "comment_count": 0, "sender": {"name": "Glenn Burton", "email": "glenn.burton@gmail.com"}, "receiver": {"name": "Spencer Powell", "email": "spencer.powell@gmail.com"}}
```
Each was independently re-read via `apis.venmo.show_transaction(transaction_id=<id>, access_token=...)`
and returned identical amount/description/created_at/sender/receiver (single-transaction API adds
`"payment_card_digits": null`).

## Verification: the user's own transaction is ABSENT from the feed
An explicit scan of all **820** feed rows for any transaction whose sender OR receiver equals the
user's email `ashlee_martinez@gmail.com` returned **0 matches** (USER_IN_FEED_COUNT = 0).
So no transaction belonging to Ashlee Martinez itself appears in her social feed — consistent with
the user's statement that "everyone's transactions except mine" are on the feed.

## The user's own share (given by the user, NOT from the feed)
- The user states their own dinner share was **$38**.
- This figure is **provided by the user**; it was deliberately **not** read from and does **not**
  appear in the feed (the user's payment is absent from the feed, per the check above).

## Notes / limitations
- Feed page_limit is capped at 20; pagination was used to exhaustion (820 rows collected).
- Other 2023-05-17 feed rows ("Mirage Melange Diner" to `br_ritt@gmail.com`, "Date Night",
  "Art Supplies", "Gas money", etc.) are NOT Azure Harbor Bistro payments to the manager and were
  excluded. Only the six transactions above are the Azure Harbor Bistro coworker-to-manager payments.
- This task records extracted evidence only; it intentionally does not assert the final answer.
