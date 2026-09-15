# Execution — Venmo reminders for aged pending roommate payment requests (Task-2)

Mission root goal: *Send a reminder on Venmo for all my payment requests to my roommates
which have not been approved or denied for 30 or more days.*

Acting account: `lindseysimpson@gmail.com` (Lindsey Simpson).
Simulated clock at execution: **Thursday, May 18, 2023, 11:58 AM** (`apis.phone.get_current_date_and_time()`),
so the 30-day threshold is `created_at <= 2023-04-18 11:58:00`.

## 1. Pre-mutation state re-read

Before any mutation I re-fetched the full sent-payment-request list
(`apis.venmo.show_sent_payment_requests(access_token=…, page_index=0..3, page_limit=20)`),
returning **68** requests. Applying the discovery criteria
(receiver ∈ roommates {`chris.mcco@gmail.com`, `joseharr@gmail.com`, `paul_mill@gmail.com`}
AND `approved_at IS NULL AND denied_at IS NULL` AND age ≥ 30 days) reproduced exactly the
7 qualifying IDs listed in `discovery.md`: **3455, 3456, 3457, 3461, 3463, 3464, 3470**.
Their re-read state (still pending) is shown below.

## 2. Login

`apis.venmo.login(username='lindseysimpson@gmail.com', password=<venmo pw>)` →
`{"token_type": "Bearer"}` (access token acquired; not reproduced here).

## 3. Reminder calls made and observed results

API used: `apis.venmo.remind_payment_request(payment_request_id=<id>, access_token=<token>)`
(POST `/payment_requests/{payment_request_id}/remind`).
Each of the 7 qualifying IDs was reminded **exactly once**; no call was retried or re-issued.

| # | payment_request_id | receiver | created_at | age (days) | amount | call made | observed result |
|---|---|---|---|---|---|---|---|
| 1 | 3455 | Jose Harrison (`joseharr@gmail.com`) | 2023-02-27T05:16:56 | 80.3 | 29.0 | `remind_payment_request(3455)` | `{"message": "Payment request reminder sent."}` |
| 2 | 3456 | Jose Harrison (`joseharr@gmail.com`) | 2022-12-17T21:06:12 | 151.6 | 62.0 | `remind_payment_request(3456)` | `{"message": "Payment request reminder sent."}` |
| 3 | 3457 | Jose Harrison (`joseharr@gmail.com`) | 2023-03-07T10:10:05 | 72.1 | 17.0 | `remind_payment_request(3457)` | `{"message": "Payment request reminder sent."}` |
| 4 | 3461 | Paul Miller (`paul_mill@gmail.com`) | 2022-12-08T21:24:23 | 160.6 | 36.0 | `remind_payment_request(3461)` | `{"message": "Payment request reminder sent."}` |
| 5 | 3463 | Paul Miller (`paul_mill@gmail.com`) | 2023-01-25T20:06:45 | 112.7 | 60.0 | `remind_payment_request(3463)` | `{"message": "Payment request reminder sent."}` |
| 6 | 3464 | Paul Miller (`paul_mill@gmail.com`) | 2023-04-15T08:02:02 | 33.2 | 19.0 | `remind_payment_request(3464)` | `{"message": "Payment request reminder sent."}` |
| 7 | 3470 | Paul Miller (`paul_mill@gmail.com`) | 2023-01-20T22:41:22 | 117.6 | 21.0 | `remind_payment_request(3470)` | `{"message": "Payment request reminder sent."}` |

Raw response payloads (verbatim), in the order the IDs were called:
```json
{"3455": {"message": "Payment request reminder sent."},
 "3456": {"message": "Payment request reminder sent."},
 "3457": {"message": "Payment request reminder sent."},
 "3461": {"message": "Payment request reminder sent."},
 "3463": {"message": "Payment request reminder sent."},
 "3464": {"message": "Payment request reminder sent."},
 "3470": {"message": "Payment request reminder sent."}}
```

## 4. Post-mutation state check

Re-read of `show_sent_payment_requests` after the calls confirmed all 7 target requests are
still present and still `approved_at = null, denied_at = null` (i.e. the reminders did not
approve or deny anything):

```
3464 None None
3457 None None
3455 None None
3463 None None
3470 None None
3456 None None
3461 None None
```

## 5. Outcome / limitations

- All 7 qualifying payment requests received a reminder; every call returned the success
  message `"Payment request reminder sent."`. No failure responses and no exceptions occurred.
- The Venmo payment-request object exposes no "reminded"/"last reminded" field, so the only
  direct evidence of success from this task is the API success response plus the unchanged
  (still-pending) request state. Independent confirmation of the recipient-side notification
  is deferred to the verification task.
- No non-qualifying request was reminded. No request was reminded more than once.
