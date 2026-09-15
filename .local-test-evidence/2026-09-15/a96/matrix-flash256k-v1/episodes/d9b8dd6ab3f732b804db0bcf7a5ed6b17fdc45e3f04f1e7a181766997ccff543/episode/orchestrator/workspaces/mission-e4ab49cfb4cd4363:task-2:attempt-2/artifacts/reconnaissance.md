# Task A — Reconnaissance (Venmo reminders to roommates)

**Task id:** mission-e4ab49cfb4cd4363:task-1
**Attempt:** mission-e4ab49cfb4cd4363:task-1:attempt-1
**Scope:** Read-only discovery. NO reminders sent in this task.

## 1. Environment / current time

- `apis.phone.get_current_date_and_time()` → `{"date": "Thursday, May 18, 2023", "time": "11:58 AM"}`
- Reconnaissance "now" = **2023-05-18T11:58:00**.
- 30-day cutoff: created_at **<= 2023-04-18T11:58:00** (a request pending for 30 or more days).

## 2. Simulated user (supervisor) and Venmo account

- `apis.supervisor.show_profile()` → Lindsey Simpson, lindseysimpson@gmail.com, phone 3567801924.
- Venmo password from `apis.supervisor.show_account_passwords()`: `%iLp@(g`.
- `apis.venmo.login(username="lindseysimpson@gmail.com", password="%iLp@(g")` succeeded (access_token obtained).
- `apis.venmo.show_account()` → first_name Lindsey, last_name Simpson, email lindseysimpson@gmail.com,
  registered_at 2022-10-05T14:57:08, verified true, venmo_balance 6140.0, friend_count 10.

Note: the Venmo access token is only usable in the same shell/session it was minted in; a token from an
earlier execution returned HTTP 401. Re-login is required in each session.

## 3. Roommates (source of "roommate" set)

`apis.phone.login(username="3567801924", password="8qAz[-V")` then
`apis.phone.show_contact_relationships()` → includes "roommate".

`apis.phone.search_contacts(relationship="roommate", page_limit=20)` returned exactly 3 contacts:

| contact_id | Name          | Email                  | Relationship |
|-----------:|---------------|------------------------|--------------|
| 1261       | Chris Mccoy   | chris.mcco@gmail.com   | roommate     |
| 1262       | Jose Harrison | joseharr@gmail.com     | roommate     |
| 1263       | Paul Miller   | paul_mill@gmail.com    | roommate     |

Roommate email set used for matching = {chris.mcco@gmail.com, joseharr@gmail.com, paul_mill@gmail.com}.

## 4. All sent payment requests

`apis.venmo.show_sent_payment_requests()` paginated (page_limit=20): 68 total (20+20+20+8).
Cross-checked with the `status` filter: approved = 32, denied = 17, pending = 19 → 68. Consistent.

Distinct receivers (9): alexwhite(5), as_moore(9), brenda.webe(6), cod.smith(7), eri_powe(7),
joseharr(9), paul_mill(10), ric.riddle(8), ta.weav(7). Note: roommate **Chris Mccoy has zero sent
payment requests**.

Status definition used: a request is "pending" (neither approved nor denied) when
`approved_at is None and denied_at is None`.

## 5. Candidate set — eligible payment requests

Eligibility rule = receiver ∈ roommate set AND pending (`approved_at is None and denied_at is None`)
AND pending for >= 30 days (`created_at <= 2023-04-18T11:58:00`).

**Eligible (7):**

| payment_request_id | receiver      | amount | description                     | created_at           | age (days) |
|-------------------:|---------------|-------:|---------------------------------|----------------------|-----------:|
| 3461               | Paul Miller   | 36.0   | Skincare glow-up                | 2022-12-08T21:24:23  | 160        |
| 3456               | Jose Harrison | 62.0   | Grocery 🛒 Haul                 | 2022-12-17T21:06:12  | 151        |
| 3470               | Paul Miller   | 21.0   | Aquarium Tickets                | 2023-01-20T22:41:22  | 117        |
| 3463               | Paul Miller   | 60.0   | Treat Yo' Self 💆               | 2023-01-25T20:06:45  | 112        |
| 3455               | Jose Harrison | 29.0   | Skincare 💆🌿                   | 2023-02-27T05:16:56  | 80         |
| 3457               | Jose Harrison | 17.0   | 🎮 Gaming Marathon Snacks 🎮    | 2023-03-07T10:10:05  | 72         |
| 3464               | Paul Miller   | 19.0   | Record Store 🎶 Finds           | 2023-04-15T08:02:02  | 33         |

**Explicitly excluded pending roommate request (too recent):**

| payment_request_id | receiver    | amount | description      | created_at           | age (days) |
|-------------------:|-------------|-------:|------------------|----------------------|-----------:|
| 3462               | Paul Miller | 44.0   | Fishing License  | 2023-05-16T03:49:44  | 2          |

No other pending+roommate requests exist (see the 8-row pending∩roommate list; 3462 is the only one
under 30 days).

## 6. Reminder API contract (for Task B, not executed here)

- `apis.venmo.remind_payment_request(payment_request_id=<int>, access_token=<str>)`
  - HTTP `POST /payment_requests/{payment_request_id}/remind`
  - "Send a reminder to a user via notification about this payment request."
  - success → `{"message": "string"}`; failure → `{"message": "string"}`.
- Requires a valid Venmo access_token (re-login each session).

## 7. Status of this task

Reconnaissance complete. **No reminders were sent** (Task B owns all mutations).
