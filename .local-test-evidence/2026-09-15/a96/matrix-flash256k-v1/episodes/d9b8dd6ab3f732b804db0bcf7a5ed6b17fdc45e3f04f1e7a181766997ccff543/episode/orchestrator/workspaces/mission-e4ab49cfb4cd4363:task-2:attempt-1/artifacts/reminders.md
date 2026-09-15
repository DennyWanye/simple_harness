# Task B — Venmo reminders to roommates (MUTATION TASK)

**Task id:** mission-e4ab49cfb4cd4363:task-2
**Attempt:** mission-e4ab49cfb4cd4363:task-2:attempt-1
**Goal:** Send a Venmo reminder for every eligible payment request identified in Task A, using the
correct Venmo reminder API, and verify each request's resulting state.

## Environment / method

- Current time (`apis.phone.get_current_date_and_time()`): **Thursday, May 18, 2023, 11:58 AM**
  → 30-day cutoff: `created_at <= 2023-04-18T11:58:00`.
- Logged into Venmo as the simulated user Lindsey Simpson
  (`apis.venmo.login(username="lindseysimpson@gmail.com", password="%iLp@(g")`) — fresh access token
  obtained in the same shell session (tokens are session-scoped).
- Reminder API used (from `apis.api_docs.show_api_doc(app_name='venmo', api_name='remind_payment_request')`):
  - `apis.venmo.remind_payment_request(payment_request_id=<int>, access_token=<str>)`
  - `POST /payment_requests/{payment_request_id}/remind`
  - "Send a reminder to a user via notification about this payment request."
  - success → `{"message": "string"}`.

## Eligible set re-confirmed (independent re-derivation in this task)

Roommate email set = {chris.mcco@gmail.com, joseharr@gmail.com, paul_mill@gmail.com} (from
`apis.phone.search_contacts(access_token=..., relationship="roommate")`).

Re-fetched all 68 sent payment requests via `apis.venmo.show_sent_payment_requests` (paginated,
page_limit=20) and re-applied the rule (receiver ∈ roommates AND `approved_at is None and
denied_at is None` AND `created_at <= 2023-04-18T11:58:00`). Result — **7 eligible requests
(3455, 3456, 3457, 3461, 3463, 3464, 3470)**, matching Task A. `3462` (Paul Miller, "Fishing
License", 2023-05-16) remains excluded (only ~2 days old).

## Actions performed and observed responses

Each API call below was issued exactly once for the 7 eligible ids. All 7 returned success.

| payment_request_id | receiver      | amount | description                     | created_at           | API response |
|-------------------:|---------------|-------:|---------------------------------|----------------------|--------------|
| 3455 | Jose Harrison | 29.0   | Skincare 💆🌿                   | 2023-02-27T05:16:56  | `{"message": "Payment request reminder sent."}` |
| 3456 | Jose Harrison | 62.0   | Grocery 🛒 Haul                 | 2022-12-17T21:06:12  | `{"message": "Payment request reminder sent."}` |
| 3457 | Jose Harrison | 17.0   | 🎮 Gaming Marathon Snacks 🎲    | 2023-03-07T10:10:05  | `{"message": "Payment request reminder sent."}` |
| 3461 | Paul Miller   | 36.0   | Skincare glow-up                | 2022-12-08T21:24:23  | `{"message": "Payment request reminder sent."}` |
| 3463 | Paul Miller   | 60.0   | Treat Yo' Self 💆               | 2023-01-25T20:06:45  | `{"message": "Payment request reminder sent."}` |
| 3464 | Paul Miller   | 19.0   | Record Store 🎶 Finds           | 2023-04-15T08:02:02  | `{"message": "Payment request reminder sent."}` |
| 3470 | Paul Miller   | 21.0   | Aquarium Tickets                | 2023-01-20T22:41:22  | `{"message": "Payment request reminder sent."}` |

Raw observed outputs (verbatim from the shell):
```
3455 -> {"message": "Payment request reminder sent."}
3456 -> {"message": "Payment request reminder sent."}
3457 -> {"message": "Payment request reminder sent."}
3461 -> {"message": "Payment request reminder sent."}
3463 -> {"message": "Payment request reminder sent."}
3464 -> {"message": "Payment request reminder sent."}
3470 -> {"message": "Payment request reminder sent."}
```

## Post-action state verification

Re-fetched all sent payment requests after the reminders. For each of the 7 ids the request still
exists and its approval status is unchanged (still pending — a reminder does not approve/deny it):

| id   | receiver      | approved_at | denied_at | updated_at           |
|------|---------------|-------------|-----------|----------------------|
| 3455 | Jose Harrison | None        | None      | 2023-02-27T05:16:56  |
| 3456 | Jose Harrison | None        | None      | 2022-12-17T21:06:12  |
| 3457 | Jose Harrison | None        | None      | 2023-03-07T10:10:05  |
| 3461 | Paul Miller   | None        | None      | 2022-12-08T21:24:23  |
| 3463 | Paul Miller   | None        | None      | 2023-01-25T20:06:45  |
| 3464 | Paul Miller   | None        | None      | 2023-04-15T08:02:02  |
| 3470 | Paul Miller   | None        | None      | 2023-01-20T22:41:22  |

## Summary

- Reminders sent: **7 / 7** eligible requests (ids 3455, 3456, 3457, 3461, 3463, 3464, 3470).
- Failures: **none**.
- Unchanged / non-eligible items: request 3462 (Paul Miller, "Fishing License") is still pending but
  only ~2 days old, so it was intentionally **not** reminded. All approved/denied requests were left
  untouched.

## Limitations / caveats

- Venmo access tokens are session-scoped; the reminder calls were made in a single session with a
  freshly minted token.
- The reminder API returns only a confirmation message; it does not itself change the payment
  request's approved/denied status, so "resulting state" verification is limited to confirming the
  request still exists and remains pending.
- "Roommate" is defined solely by the phone contact book `relationships` field.
