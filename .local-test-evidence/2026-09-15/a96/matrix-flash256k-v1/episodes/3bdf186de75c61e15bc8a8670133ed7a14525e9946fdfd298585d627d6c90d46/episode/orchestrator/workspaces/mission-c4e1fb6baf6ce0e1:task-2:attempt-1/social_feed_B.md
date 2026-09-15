# Social Feed B — Coworker→Manager Payments in the Social Feed (Task-2)

**Scope:** Read-only inspection of the user's Venmo **social feed** for "yesterday".
**Goal of this branch:** Extract every transaction *from coworkers to the manager* on yesterday,
excluding the user's own (missing/unposted) transaction; record raw feed entries, counterparties,
amounts, and the computed sum. **No application state was mutated.**

---

## 1. Identifiers & inputs used (from recon_A.md)

| Item | Value |
|---|---|
| User (supervisor) | Ashlee Martinez — `ashlee_martinez@gmail.com` |
| Manager | Spencer Powell — `spencer.powell@gmail.com` |
| API used | `apis.venmo.show_social_feed(access_token, page_index, page_limit)` → `GET /social_feed` |
| Auth | `apis.venmo.login(username=email, password)` then `access_token` |
| Date window ("yesterday") | **2023-05-17** (current date confirmed `Thursday, May 18, 2023` via `apis.phone.get_current_date_and_time`) |

## 2. Method / completeness

- Scanned the social feed across pages `page_index = 0..3` with `page_limit = 20` → 80 entries collected.
- Filtered to entries with `created_at` on **2023-05-17**: 16 entries (page 0 covers 2023-05-17T14:46:15
  through 2023-05-17T08:27:17; page 1 covers down to 2023-05-17T05:33:04 and 04:50:40; page 2 begins
  2023-05-14). Therefore **all** 2023-05-17 entries were observed — the dinner evidence is complete.
- Filtered those to `receiver.email == spencer.powell@gmail.com` (the manager): **6 entries**, all
  timestamped `2023-05-17T14:46:15`, all describing Azure Harbor Bistro.

## 3. Raw feed entries — coworkers → manager (yesterday, 2023-05-17)

Source: `apis.venmo.show_social_feed`, page 0. All six are `private: false`, `like_count: 0`,
`comment_count: 0`, `created_at = updated_at = 2023-05-17T14:46:15`, receiver `Spencer Powell`.

| # | transaction_id | amount | description | sender (counterparty) | sender email | receiver |
|---|---|---|---|---|---|---|
| 1 | 8216 | 29.0 | Dinner at Azure Harbor Bistro | Jordan Harrison | jo-harr@gmail.com | Spencer Powell |
| 2 | 8217 | 20.0 | Azure Harbor Bistro | Angela Riddle | angriddle@gmail.com | Spencer Powell |
| 3 | 8218 | 42.0 | Azure Harbor Bistro | Adam Blackburn | ad.blackburn@gmail.com | Spencer Powell |
| 4 | 8219 | 44.0 | Food at Azure Harbor Bistro | Jeffrey Smith | jefsmith@gmail.com | Spencer Powell |
| 5 | 8220 | 23.0 | Food at Azure Harbor Bistro | Connor Brown | connorbrow@gmail.com | Spencer Powell |
| 6 | 8221 | 31.0 | Dinner at Azure Harbor Bistro | Glenn Burton | glenn.burton@gmail.com | Spencer Powell |

All six senders are `coworker` relationships of the user (per recon_A phone contacts); the receiver
`Spencer Powell` is the manager. Transaction IDs 8216–8221 are contiguous.

## 4. Computed sum (this branch)

```
29.0 + 20.0 + 42.0 + 44.0 + 23.0 + 31.0 = 189.0
```

**Sum of the six coworker→manager social-feed entries for yesterday = $189.0.**

## 5. The user's own (excluded) transaction

The user's own share is **not** present on the social feed, consistent with the user's statement
("Everyone's transactions except mine should be on my social feed"). For reference only, the user's
$38.0 payment (`transaction_id 8222`, Ashlee Martinez → Spencer Powell, `2023-05-17T14:46:15`,
"Dinner at Azure Harbor Bistro") was found via `show_transactions` (Venmo branch / recon_A), **not**
via the social feed, and is therefore **excluded** from this feed-based sum.

For the *grand total* the user ultimately asks about, the user's $38 share must be added separately:
`189.0 (feed) + 38.0 (user, off-feed) = 227.0`. This reconciliation is the responsibility of the
integrating task (Task-4); this branch only certifies the feed-derived component **189.0**.

## 6. Limitations / notes

- "Yesterday" is taken as **2023-05-17** based on the current date 2023-05-18 and matching data stamps.
- This branch relied solely on the **public social feed**; the user's own off-feed transaction is out of
  scope here and is supplied by the Venmo branch.
- No state was mutated (only auth logins + read calls).
- Raw amounts above are recorded exactly as returned by the API; the arithmetic sum ($189.0) is computed
  from those observed values.
