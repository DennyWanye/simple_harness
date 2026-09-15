# Discovery — Venmo reminder target set (Task-1)

Mission root goal: *Send a reminder on Venmo for all my payment requests to my roommates
which have not been approved or denied for 30 or more days.*

This task is **discovery only, no mutations**. All observations below come from live
`appworld_execute` calls against the shared AppWorld state. Raw API payloads are quoted
verbatim (trimmed only where marked "…").

---

## 1. Environment / API discovery

Apps exposed (`apis.api_docs.show_app_descriptions()`):
`api_docs, supervisor, amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist`

Relevant APIs used:
- `supervisor.show_profile` — identify the simulated user.
- `supervisor.show_account_passwords` — obtain app credentials.
- `venmo.login`, `venmo.show_account`, `venmo.show_sent_payment_requests`,
  `venmo.search_users`.
- `phone.login`, `phone.get_current_date_and_time`, `phone.show_contact_relationships`,
  `phone.search_contacts`.

## 2. Simulated user

`apis.supervisor.show_profile()` →
```
{ "first_name": "Lindsey", "last_name": "Simpson",
  "email": "lindseysimpson@gmail.com", "phone_number": "3567801924",
  "birthday": "1993-11-23", "sex": "female" }
```
`apis.venmo.login(username='lindseysimpson@gmail.com', password=<venmo pw>)` succeeded and
`apis.venmo.show_account(access_token=…)` returned:
```
{ "first_name": "Lindsey", "last_name": "Simpson",
  "email": "lindseysimpson@gmail.com", "registered_at": "2022-10-05T14:57:08",
  "last_logged_in": "2022-10-05T14:57:08", "verified": true,
  "venmo_balance": 6140.0, "friend_count": 10 }
```
So the acting Venmo account is `lindseysimpson@gmail.com`.

## 3. "Now" (reference time for the 30-day rule)

`apis.phone.get_current_date_and_time()` (no auth required) →
```
{ "date": "Thursday, May 18, 2023", "time": "11:58 AM" }
```
Reference instant used: **2023-05-18 11:58:00**. 30 days earlier = **2023-04-18 11:58:00**.

## 4. Who are the roommates?

Roommate relationship source = phone contact book:
`apis.phone.show_contact_relationships(access_token=…)` →
```
["brother","coworker","father","friend","manager","mother","parent","roommate","sibling"]
```
`apis.phone.search_contacts(access_token=…, relationship='roommate')` →
```
[
 {"contact_id":1261,"first_name":"Chris","last_name":"Mccoy","email":"chris.mcco@gmail.com",
  "phone_number":"5584932120","relationships":["roommate"],"birthday":"1983-01-02",
  "home_address":"172 Matthew Knolls Suite 730\nSeattle\nWashington\nUnited States\n65644", "…"},
 {"contact_id":1262,"first_name":"Jose","last_name":"Harrison","email":"joseharr@gmail.com",
  "phone_number":"2474975253","relationships":["roommate"],"birthday":"1985-12-15",
  "home_address":"172 Matthew Knolls Suite 730\nSeattle\nWashington\nUnited States\n65644", "…"},
 {"contact_id":1263,"first_name":"Paul","last_name":"Miller","email":"paul_mill@gmail.com",
  "phone_number":"3379617841","relationships":["roommate"],"birthday":"1997-08-01",
  "home_address":"172 Matthew Knolls Suite 730\nSeattle\nWashington\nUnited States\n65644", "…"}
]
```
**Roommates (Venmo identities):**
- Chris Mccoy — `chris.mcco@gmail.com`
- Jose Harrison — `joseharr@gmail.com`
- Paul Miller — `paul_mill@gmail.com`

`apis.venmo.search_users(query='chris.mcco@gmail.com', access_token=…)` confirms Chris Mccoy is a
real Venmo user (`"friends_since": "2022-08-25T08:57:40"`), but **no sent payment request targets
`chris.mcco@gmail.com`**, so he contributes no candidates.

## 5. Enumerating Lindsey's sent payment requests

`apis.venmo.show_sent_payment_requests(access_token=…, page_index=p, page_limit=20)` for
p = 0,1,2,3 returned 20+20+20+8 = **68 requests** (all `sender.email = lindseysimpson@gmail.com`).

Distinct receivers found: `alexwhite@gmail.com, as_moore@gmail.com, brenda.webe@gmail.com,
cod.smith@gmail.com, eri_powe@gmail.com, joseharr@gmail.com, paul_mill@gmail.com,
ric.riddle@gmail.com, ta.weav@gmail.com`.
Only `joseharr@gmail.com` (Jose Harrison) and `paul_mill@gmail.com` (Paul Miller) are roommates.

## 6. Criteria

A sent request qualifies **iff all** hold:
1. `receiver.email ∈ {chris.mcco@gmail.com, joseharr@gmail.com, paul_mill@gmail.com}` (roommate), AND
2. `approved_at IS NULL AND denied_at IS NULL` (neither approved nor denied → still pending), AND
3. `now − created_at ≥ 30 days`, i.e. `created_at ≤ 2023-04-18 11:58:00`
   (equivalently age_in_days ≥ 30 vs. 2023-05-18 11:58).

## 7. Qualifying request set (the target set)

| # | payment_request_id | created_at | age (days) | amount | receiver | description | approved_at | denied_at |
|---|---|---|---|---|---|---|---|---|
| 1 | 3456 | 2022-12-17T21:06:12 | 151 | 62.0 | Jose Harrison (joseharr@gmail.com) | Grocery 🛒 Haul | null | null |
| 2 | 3461 | 2022-12-08T21:24:23 | 160 | 36.0 | Paul Miller (paul_mill@gmail.com) | Skincare glow-up | null | null |
| 3 | 3470 | 2023-01-20T22:41:22 | 117 | 21.0 | Paul Miller (paul_mill@gmail.com) | Aquarium Tickets | null | null |
| 4 | 3463 | 2023-01-25T20:06:45 | 112 | 60.0 | Paul Miller (paul_mill@gmail.com) | Treat Yo' Self 💆 | null | null |
| 5 | 3455 | 2023-02-27T05:16:56 | 80 | 29.0 | Jose Harrison (joseharr@gmail.com) | Skincare 💆🌿 | null | null |
| 6 | 3457 | 2023-03-07T10:10:05 | 72 | 17.0 | Jose Harrison (joseharr@gmail.com) | 🎮 Gaming Marathon Snacks 🎲 | null | null |
| 7 | 3464 | 2023-04-15T08:02:02 | 33 | 19.0 | Paul Miller (paul_mill@gmail.com) | Record Store 🎶 Finds | null | null |

**QUALIFYING IDs (7): `[3455, 3456, 3457, 3461, 3463, 3464, 3470]`**

### Raw API evidence for the 7 targets
`apis.venmo.show_sent_payment_requests(access_token=…)` returned these objects verbatim
(order as returned; only the private flag/order shown, fields unchanged):
```json
[
 {"payment_request_id":3464,"amount":19.0,"description":"Record Store 🎶 Finds","created_at":"2023-04-15T08:02:02","updated_at":"2023-04-15T08:02:02","approved_at":null,"denied_at":null,"private":false,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Paul Miller","email":"paul_mill@gmail.com"}},
 {"payment_request_id":3457,"amount":17.0,"description":"🎮 Gaming Marathon Snacks 🎲","created_at":"2023-03-07T10:10:05","updated_at":"2023-03-07T10:10:05","approved_at":null,"denied_at":null,"private":false,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Jose Harrison","email":"joseharr@gmail.com"}},
 {"payment_request_id":3455,"amount":29.0,"description":"Skincare 💆🌿","created_at":"2023-02-27T05:16:56","updated_at":"2023-02-27T05:16:56","approved_at":null,"denied_at":null,"private":true,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Jose Harrison","email":"joseharr@gmail.com"}},
 {"payment_request_id":3463,"amount":60.0,"description":"Treat Yo' Self 💆","created_at":"2023-01-25T20:06:45","updated_at":"2023-01-25T20:06:45","approved_at":null,"denied_at":null,"private":false,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Paul Miller","email":"paul_mill@gmail.com"}},
 {"payment_request_id":3470,"amount":21.0,"description":"Aquarium Tickets","created_at":"2023-01-20T22:41:22","updated_at":"2023-01-20T22:41:22","approved_at":null,"denied_at":null,"private":false,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Paul Miller","email":"paul_mill@gmail.com"}},
 {"payment_request_id":3456,"amount":62.0,"description":"Grocery 🛒 Haul","created_at":"2022-12-17T21:06:12","updated_at":"2022-12-17T21:06:12","approved_at":null,"denied_at":null,"private":false,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Jose Harrison","email":"joseharr@gmail.com"}},
 {"payment_request_id":3461,"amount":36.0,"description":"Skincare glow-up","created_at":"2022-12-08T21:24:23","updated_at":"2022-12-08T21:24:23","approved_at":null,"denied_at":null,"private":false,"sender":{"name":"Lindsey Simpson","email":"lindseysimpson@gmail.com"},"receiver":{"name":"Paul Miller","email":"paul_mill@gmail.com"}}
]
```

## 8. Near-miss / explicitly excluded

- **3462** — Paul Miller, `created_at 2023-05-16T03:49:44`, pending (`approved_at=null, denied_at=null`)
  but only **2 days** old → excluded by the ≥30-day rule (only pending-to-roommate item excluded on age).
- All requests to `chris.mcco@gmail.com`: none exist → Chris Mccoy contributes no candidates.
- Pending requests to non-roommates (Alex White, Ashley Moore, Tammy Weaver, Richard Riddle,
  Cody Smith, Brenda Smith, Erica Wilson): excluded by the roommate criterion.
- Approved/denied requests to Jose/Paul: excluded by the pending criterion.

## 9. Notes / limitations

- No mutations were performed in this task (read-only enumeration + login token acquisition).
- Reminder/sent state is not part of the qualifying criteria here; whether any of the 7 was
  already reminded was not required for target-set discovery. (Venmo exposes no direct
  "shown already reminded" field on the payment-request object.)
- Age is computed against the simulated clock `2023-05-18 11:58`. If re-run at a later date,
  re-evaluate the boundary; `3462` would cross the 30-day threshold on ~2023-06-15.
