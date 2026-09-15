# REPORT — Venmo roommate payment-request reminders

## Overall goal
Send a Venmo reminder for every payment request to the user's roommates that has not been
approved or denied for 30 or more days.

## This worker's task (Task A — Reconnaissance)
Goal: discover the Venmo API surface, the simulated user's account, roommate relationships, and all
payment requests; identify the exact eligible set. **No reminders were sent in this task.**

Full details and tables: `artifacts/reconnaissance.md`.

### Actions actually performed (read-only)
1. `apis.api_docs.show_app_descriptions()` and `show_api_descriptions` / `show_api_doc` for venmo,
   supervisor, phone.
2. `apis.supervisor.show_profile()` / `show_account_passwords()` → user Lindsey Simpson, venmo
   password `%iLp@(g`.
3. `apis.venmo.login(...)` + `apis.venmo.show_account()` → Lindsey Simpson, verified, balance 6140.0,
   friend_count 10. (Token is session-scoped; a token reused in a different shell returned 401.)
4. `apis.phone.login(...)` + `apis.phone.show_contact_relationships()` and
   `search_contacts(relationship="roommate")` → 3 roommates.
5. `apis.phone.get_current_date_and_time()` → Thursday, May 18, 2023, 11:58 AM.
6. `apis.venmo.show_sent_payment_requests(...)` paginated → 68 requests; cross-checked with
   `status` filter → 32 approved, 17 denied, 19 pending.

### Observed facts
- Roommates: Chris Mccoy (chris.mcco@gmail.com), Jose Harrison (joseharr@gmail.com),
  Paul Miller (paul_mill@gmail.com). Chris Mccoy has no sent payment requests.
- Status = pending means `approved_at is None and denied_at is None`.
- 30-day cutoff (created_at <= 2023-04-18T11:58:00).

### Eligible payment requests (to a roommate, pending >= 30 days) — 7 total
| id   | receiver      | amount | description                   | created_at          |
|------|---------------|-------:|-------------------------------|---------------------|
| 3461 | Paul Miller   | 36.0   | Skincare glow-up              | 2022-12-08T21:24:23 |
| 3456 | Jose Harrison | 62.0   | Grocery 🛒 Haul               | 2022-12-17T21:06:12 |
| 3470 | Paul Miller   | 21.0   | Aquarium Tickets              | 2023-01-20T22:41:22 |
| 3463 | Paul Miller   | 60.0   | Treat Yo' Self 💆             | 2023-01-25T20:06:45 |
| 3455 | Jose Harrison | 29.0   | Skincare 💆🌿                 | 2023-02-27T05:16:56 |
| 3457 | Jose Harrison | 17.0   | 🎮 Gaming Marathon Snacks 🎮  | 2023-03-07T10:10:05 |
| 3464 | Paul Miller   | 19.0   | Record Store 🎶 Finds         | 2023-04-15T08:02:02 |

Excluded (pending roommate request but only 2 days old): 3462 Paul Miller, Fishing License,
2023-05-16T03:49:44.

### Not done (owned by later tasks)
- Task B: calling `apis.venmo.remind_payment_request(payment_request_id=<id>, access_token=<str>)`
  for each of the 7 eligible ids.
- Task C: independent re-verification.

### Limitations / caveats
- Venmo access tokens are session-scoped; re-login needed per shell session.
- "Roommate" set is defined solely by the phone contact book relationship field.
- The 30-day rule is applied from the phone app's current date/time (2023-05-18 11:58).
