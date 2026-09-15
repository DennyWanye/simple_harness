# Social Feed Report — Venmo transactions to the manager on 2023-05-17 ("yesterday")

Task: mission-28142a08e2b44499:task-2
Author: worker
Date context: current simulated time is **Thursday, May 18, 2023, 12:00 PM** (via `apis.phone.get_current_date_and_time`), so "yesterday" = **2023-05-17**.

## Method / source evidence
1. `apis.venmo.login(username='ashlee_martinez@gmail.com', password='F4s1Idj')` -> access_token. Venmo calls require the token explicitly (not auto-persisted).
2. Enumerated the user's full social feed with `apis.venmo.show_social_feed(access_token=..., page_index=p, page_limit=20)` for `p = 0..51`; page 52 returned empty. Total **1032 transactions, 1032 unique transaction_ids** (complete enumeration, no duplicates).
3. Filtered for `receiver.email == 'spencer.powell@gmail.com'` (the manager identified in task-1 via phone contacts, relationship='manager') and `created_at` starting with `2023-05-17`.
4. Result: exactly **6** such transactions, all dated `2023-05-17T14:46:15` and all describing the Azure Harbor Bistro dinner. Each payer is one of the user's six coworkers from the phone contact book.
5. Cross-checked the user's own (excluded) transaction with `apis.venmo.show_transactions(access_token=..., min_created_at='2023-05-17', max_created_at='2023-05-17')`.

## Coworker transactions sent to the manager (spencer.powell@gmail.com) on 2023-05-17

| transaction_id | payer | payer email | amount (USD) | timestamp | description |
|---|---|---|---|---|---|
| 8216 | Jordan Harrison | jo-harr@gmail.com | 29.0 | 2023-05-17T14:46:15 | Dinner at Azure Harbor Bistro |
| 8217 | Angela Riddle | angriddle@gmail.com | 20.0 | 2023-05-17T14:46:15 | Azure Harbor Bistro |
| 8218 | Adam Blackburn | ad.blackburn@gmail.com | 42.0 | 2023-05-17T14:46:15 | Azure Harbor Bistro |
| 8219 | Jeffrey Smith | jefsmith@gmail.com | 44.0 | 2023-05-17T14:46:15 | Food at Azure Harbor Bistro |
| 8220 | Connor Brown | connorbrow@gmail.com | 23.0 | 2023-05-17T14:46:15 | Food at Azure Harbor Bistro |
| 8221 | Glenn Burton | glenn.burton@gmail.com | 31.0 | 2023-05-17T14:46:15 | Dinner at Azure Harbor Bistro |

**Subtotal paid by the other coworkers = 29 + 20 + 42 + 44 + 23 + 31 = 189.0 USD**

These 6 payers are exactly the 6 `coworker` contacts from the phone contact book (Jordan Harrison, Angela Riddle, Adam Blackburn, Jeffrey Smith, Connor Brown, Glenn Burton); the single receiver is the `manager` contact Spencer Powell. All six transactions share the same timestamp, consistent with a single group dinner.

## User's own transaction (excluded from the total above)

Confirmed separately (it is not shown on the social feed, which only lists friends' transactions):

| transaction_id | payer | receiver | amount (USD) | timestamp | description |
|---|---|---|---|---|---|
| 8222 | Ashlee Martinez (ashlee_martinez@gmail.com) | Spencer Powell (spencer.powell@gmail.com) | 38.0 | 2023-05-17T14:46:15 | Dinner at Azure Harbor Bistro |

This matches the user-stated share of $38 and is the same timestamp as the coworkers' payments.

## Notes / scope limits
- The social feed only exposes transactions of the user's friends. All six coworkers' dinner payments to the manager were visible, and the phone contact book contains exactly six `coworker` contacts, so the set appears complete.
- The six transactions were the ONLY feed transactions to `spencer.powell@gmail.com` dated 2023-05-17 (other manager-directed transactions occurred on other dates and are unrelated).
- The subtotal of the other coworkers' shares is **189.0**. Combined with the user's own $38 share, the manager's dinner fronted total would be 189 + 38 = **227.0** (integration/verification handled in later tasks).
- Report reflects the shared AppWorld state at the time of these reads.
