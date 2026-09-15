# Discovery Report — Mission task-1 (AppWorld environment discovery)

## 1. Public apps enumerated
`apis.api_docs.show_app_descriptions()` returns 11 apps:

| app | description |
|---|---|
| api_docs | search and explore API documentation |
| supervisor | supervisor personal info, account credentials, addresses, payment cards, task management |
| amazon | online shopping |
| phone | contacts, messages, alarms |
| file_system | files/folders |
| spotify | music streaming |
| venmo | social payment app |
| gmail | email |
| splitwise | bill splitting |
| simple_note | notes |
| todoist | task management |

## 2. Simulated user (supervisor)
- `apis.supervisor.show_profile()` -> first_name **Ashlee**, last_name **Martinez**, email `ashlee_martinez@gmail.com`, phone `3506492550`, birthday 1960-10-16, female.
- `apis.supervisor.show_account_passwords()` -> venmo password `F4s1Idj` (username = account email). Other app passwords also listed (amazon, file_system, gmail, phone=`Y^FG$wg`, simple_note, splitwise, spotify, todoist).
- `apis.supervisor.show_api_descriptions(app_name='supervisor')` -> show_active_task, complete_task, show_profile, show_addresses, show_payment_cards, show_account_passwords.
- Venmo login: `apis.venmo.login(username='ashlee_martinez@gmail.com', password='F4s1Idj')` -> returns `{access_token, token_type}`.
  - IMPORTANT: `access_token` must be passed explicitly to every other venmo call; it is NOT auto-persisted (calling `show_account()` without it returns HTTP 401).
- `apis.venmo.show_account(access_token=...)` -> Ashlee Martinez, `ashlee_martinez@gmail.com`, verified=true, venmo_balance=5432.0, friend_count=12.

## 3. Manager and coworkers (phone contact book)
Phone login: `apis.phone.login(username='3506492550', password='Y^FG$wg')` -> access_token (must be passed explicitly).
`apis.phone.show_contact_relationships(access_token=...)` -> ['child', 'coworker', 'friend', 'husband', 'manager', 'partner', 'son'].
`apis.phone.search_contacts(access_token=..., relationship='manager'|'coworker')`:

**Manager**
| name | email | phone | contact_id | relationships |
|---|---|---|---|---|
| Spencer Powell | spencer.powell@gmail.com | 8267279358 | 1529 | manager, coworker |

**Coworkers**
| name | email | phone | contact_id |
|---|---|---|---|
| Jordan Harrison | jo-harr@gmail.com | 2254213734 | 1527 |
| Angela Riddle | angriddle@gmail.com | 6925139040 | 1528 |
| Adam Blackburn | ad.blackburn@gmail.com | 8944155247 | 1530 |
| Jeffrey Smith | jefsmith@gmail.com | 3272301258 | 1531 |
| Connor Brown | connorbrow@gmail.com | 5734599766 | 1532 |
| Glenn Burton | glenn.burton@gmail.com | 8638518861 | 1533 |

Spencer Powell is the only contact with the `manager` relationship; the six others are `coworker`. All share the same work address (8875 Amy Extensions Suite 797, Seattle).

## 4. Social feed API and identifiers
- API: `apis.venmo.show_social_feed(access_token, page_index=0, page_limit=5)` — returns your **friends'** transactions (own transactions are NOT shown here).
- API: `apis.venmo.show_transactions(access_token, query, user_email, min_created_at, max_created_at, min_amount, max_amount, direction, ...)` — returns the user's own transactions; use `user_email` to filter against a specific counterparty (e.g. spencer.powell@gmail.com) and confirm the user's own $38 payment.
- API: `apis.venmo.search_friends(access_token=...)` — friends list (5 on page 1: Richard Riddle, Glenn Burton, Angela Riddle, Spencer Powell, Ashley Moore). Note: friend_count=12, so more pages exist.
- Current date/time: `apis.phone.get_current_date_and_time(access_token=...)` -> **Thursday, May 18, 2023, 12:00 PM** => "yesterday" = **2023-05-17**.

### Azure Harbor Bistro transactions observed on the social feed (2023-05-17T14:46:15)
All six are `sender -> Spencer Powell (spencer.powell@gmail.com)`, description contains "Azure Harbor Bistro":

| transaction_id | amount | sender email |
|---|---|---|
| 8216 | 29.0 | jo-harr@gmail.com (Jordan Harrison) |
| 8217 | 20.0 | angriddle@gmail.com (Angela Riddle) |
| 8218 | 42.0 | ad.blackburn@gmail.com (Adam Blackburn) |
| 8219 | 44.0 | jefsmith@gmail.com (Jeffrey Smith) |
| 8220 | 23.0 | connorbrow@gmail.com (Connor Brown) |
| 8221 | 31.0 | glenn.burton@gmail.com (Glenn Burton) |

These six are exactly the user's six coworkers in the phone contact book and the single receiver is the manager Spencer Powell. Sum of the six visible coworker payments = 29+20+42+44+23+31 = **189.0**.

## 5. Unresolved gaps / notes for later tasks
- The user's OWN transaction (share = $38, presumably Ashlee -> Spencer Powell on 2023-05-17) is **not** on the social feed by design; it must be verified via `apis.venmo.show_transactions(user_email='spencer.powell@gmail.com')`. Task B's job.
- The manager's total "pay for the others, including me" is not returned by any single API and must be computed in Task B (visible coworker total 189.0 + user's own 38.0 hypothesis, to be confirmed against the user's own transaction record).
- friend_count=12 but only 5 friends per page; additional friend pages not fully enumerated (not blocking).
- No splitwise/gmail evidence was consulted for the dinner total in this discovery task (reserved for later tasks).

## 6. Access notes
- All venmo and phone calls require the explicit `access_token` argument; only `login` returns it. No auto-persistence observed.
- Supervisor APIs need no token.
