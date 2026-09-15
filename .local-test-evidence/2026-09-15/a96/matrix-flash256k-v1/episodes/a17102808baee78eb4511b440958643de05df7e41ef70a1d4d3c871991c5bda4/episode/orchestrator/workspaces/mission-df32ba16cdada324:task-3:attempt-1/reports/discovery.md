# Discovery Report — AppWorld mission `mission-df32ba16cdada324`

Scope: discover the simulated user's identity, linked accounts, relevant AppWorld applications, and the public API contracts needed to identify the manager and coworkers. All observations below were produced by live `apis.*` calls in the shared AppWorld shell (not from workspace files).

## 1. Simulated user identity (supervisor.show_profile / show_addresses)
- First name: Ashlee
- Last name: Martinez
- Email: ashlee_martinez@gmail.com
- Phone number: 3506492550
- Birthday: 1960-10-16, Sex: female
- Addresses:
  - Home: 7775 Weiss Grove Suite 543, Seattle, Washington, United States, 21777
  - Work: 8875 Amy Extensions Suite 797, Seattle, Washington, United States, 49596

## 2. Current date/time
- `apis.supervisor` context and `apis.phone.get_current_date_and_time` both report **Thursday, May 18, 2023, 12:00 PM**.
- Therefore "yesterday" = **2023-05-17**.

## 3. Linked accounts / credentials (supervisor.show_account_passwords)
Accounts available to the user: venmo (needed here), amazon, file_system, gmail, phone, simple_note, splitwise, spotify, todoist. Venmo and phone were used in this discovery.
(Passwords are recorded in the live session only; not reproduced here in full.)

## 4. Applications in the world (api_docs.show_app_descriptions)
api_docs, supervisor, amazon, phone, file_system, spotify, **venmo**, gmail, splitwise, simple_note, todoist.
- The "social feed" lives inside **venmo** (`show_social_feed` = transactions of your friends).
- Payment/transaction records live inside **venmo** (`show_transactions` for the user's own transactions).
- People/relationships live inside **phone** (`show_contact_relationships`, `search_contacts`).

## 5. Public API contracts used
- `apis.venmo.login(username, password)` -> `{access_token, token_type}` (username = account email).
- `apis.venmo.show_account(access_token)` -> private account info incl. venmo_balance, friend_count.
- `apis.venmo.show_social_feed(access_token, page_index=0, page_limit=5)` -> list of friends' public transactions with `transaction_id, amount, description, created_at, updated_at, private, like_count, comment_count, sender{name,email}, receiver{name,email}`. page_limit max 20.
- `apis.venmo.show_transactions(access_token, ...)` -> the user's own transactions (same schema plus `payment_card_digits`), supports date/amount/query/direction/sort filters and pagination.
- `apis.phone.login(username=phone_number, password)` -> `{access_token, token_type}`.
- `apis.phone.show_contact_relationships(access_token)` -> relationship vocabulary: child, coworker, friend, husband, manager, partner, son.
- `apis.phone.search_contacts(access_token, query, relationship, page_index, page_limit)` -> contacts with first/last name, email, phone_number, relationships, work_address, etc.
- `apis.supervisor.show_profile / show_addresses / show_account_passwords / show_active_task / complete_task`.

## 6. Manager and coworkers (phone.search_contacts)
- **Manager: Spencer Powell** — email spencer.powell@gmail.com, phone 8267279358, relationships `[manager, coworker]`, work address `8875 Amy Extensions Suite 797, Seattle, Washington, United States, 49596` (identical to the user's Work address).
- Coworkers (relationship `coworker`, all sharing the same Work address 8875 Amy Extensions Suite 797):
  - Jordan Harrison — jo-harr@gmail.com
  - Angela Riddle — angriddle@gmail.com
  - Adam Blackburn — ad.blackburn@gmail.com
  - Jeffrey Smith — jefsmith@gmail.com
  - Connor Brown — connorbrow@gmail.com
  - Glenn Burton — glenn.burton@gmail.com

## 7. Venmo evidence for the dinner (2023-05-17, Azure Harbor Bistro)
Social feed (friends' transactions; user's own transaction is NOT on the feed) contains exactly 6 "Azure Harbor Bistro" entries, all created 2023-05-17T14:46:15, all to receiver **Spencer Powell** (spencer.powell@gmail.com):
| tx id | amount | sender | description |
|-------|--------|--------|-------------|
| 8216 | 29.0 | Jordan Harrison | Dinner at Azure Harbor Bistro |
| 8217 | 20.0 | Angela Riddle | Azure Harbor Bistro |
| 8218 | 42.0 | Adam Blackburn | Azure Harbor Bistro |
| 8219 | 44.0 | Jeffrey Smith | Food at Azure Harbor Bistro |
| 8220 | 23.0 | Connor Brown | Food at Azure Harbor Bistro |
| 8221 | 31.0 | Glenn Burton | Dinner at Azure Harbor Bistro |

The user's own transaction (from `show_transactions`, therefore absent from the social feed):
| tx id | amount | sender | receiver | description |
|-------|--------|--------|----------|-------------|
| 8222 | 38.0 | Ashlee Martinez | Spencer Powell | Dinner at Azure Harbor Bistro |

Sum of the six others' reimbursements = 29+20+42+44+23+31 = **189.0**.
Including the user's share (38.0): **227.0**.

## 8. Notes / limits
- The full social feed was paginated (page_limit 20) until exhausted; only the 6 Azure Harbor Bistro rows above match. Nearby Mirage Melange Diner rows (tx 8223/8224/8225) are a different venue and were excluded.
- Venmo sender/receiver are identified by name+email; matching them to phone contacts corroborates that all six payers are the user's coworkers and the payee is the user's manager.
