# A_discovery.md — Task-1 Discovery (live AppWorld)

Scope: discover public APIs and the user's accounts needed for the "Azure Harbor Bistro dinner" expense
question. No monetary mutation was performed and nothing was summed. All calls below were read-only
observations against the live simulated world.

## 1. Apps available (apis.api_docs.show_app_descriptions())
- api_docs — search/explore API documentation
- supervisor — supervisor's personal info, credentials, addresses, payment cards, task mgmt
- amazon
- phone — contacts, messages, alarms, current date/time
- file_system
- spotify
- **venmo** — "A social payment app to send, receive and request money to and from others."
- gmail
- splitwise — bill splitting app
- simple_note
- todoist

## 2. Relevant app for this question: **venmo** (payment + social)
The user says "everyone venmoed them" and "should be on my social feed". Venmo is the concrete
payment/social app. Relevant venmo APIs (signatures read via api_docs):
- `venmo.login(username, password)` -> {access_token, token_type}
- `venmo.show_account(access_token)` -> first_name, last_name, email, registered_at, last_logged_in, verified, venmo_balance, friend_count
- `venmo.show_social_feed(access_token, page_index=0, page_limit=5)` -> list of friend transactions:
  {transaction_id, amount, description, created_at, updated_at, private, like_count, comment_count, sender{name,email}, receiver{name,email}}
- `venmo.show_transactions(access_token, query="", user_email=None, min_created_at="1500-01-01",
  max_created_at="3000-01-01", min_like_count=0, max_like_count=..., min_amount=0, max_amount=...,
  private=None, direction=None['sent'|'received'], page_index=0, page_limit=5, sort_by=None)` -> list of transactions incl. payment_card_digits
- `venmo.show_transaction(transaction_id, access_token)` -> single transaction detail
- `venmo.show_received_payment_requests(access_token, status=None['pending'|'approved'|'denied'], page_index=0, page_limit=5)`
- `venmo.show_sent_payment_requests(access_token, status=None, page_index=0, page_limit=5)`
- `venmo.create_transaction(receiver_email, amount, access_token, description="", payment_card_id=None, private=False)` (MUTATION — not used)
- Also: search_users, search_friends, show_profile, show_venmo_balance, show_notifications, comments/likes APIs.

## 3. Supervisor (apis.supervisor.*)
- `show_profile()` -> **Ashlee Martinez**, email **ashlee_martinez@gmail.com**, phone **3506492550**,
  birthday 1960-10-16, female
- `show_account_passwords()` -> includes `venmo` password `F4s1Idj`, plus amazon, file_system, gmail,
  phone, simple_note, splitwise, spotify, todoist
- `show_addresses()` -> Home: 7775 Weiss Grove Suite 543, Seattle, WA 21777; Work: 8875 Amy Extensions Suite 797, Seattle, WA 49596
- `show_payment_cards()` -> 5 cards (HSBC, Wells Fargo, American Express, MasterCard, Discover), all owner_name "Ashlee Martinez"
- `show_active_task()` -> the live task instruction (this dinner question), status not set, answer "<<NOT_GIVEN>>"

## 4. User's account identifiers (actually returned by live APIs)
- **Venmo account** (payment/social app): logged in as
  - name: **Ashlee Martinez**
  - email: **ashlee_martinez@gmail.com** (this is the venmo username/login)
  - password: `F4s1Idj` (from supervisor.show_account_passwords)
  - venmo_balance: 5432.0, friend_count: 12, verified: true
  - registered_at / last_logged_in: 2022-05-11T10:49:32
  - access_token obtained (JWT) — used only for read-only calls.
- Supervisor identity (same person): email ashlee_martinez@gmail.com, phone 3506492550.

The Venmo email `ashlee_martinez@gmail.com` is the concrete account identifier to use when reading the
social feed / transactions and when comparing to the manager's records.

## 5. Reference date ("yesterday") in the world
- Observed "today" via `apis.phone.get_current_date_and_time()`: **Thursday, May 18, 2023, 12:00 PM**.
- Therefore **"yesterday" = Wednesday, May 17, 2023** (2023-05-17).

## 6. Dinner location
- **Azure Harbor Bistro** (given by the user; used as the description/search text for transactions).

## Notes / limitations
- "Do not sum" was honored: no transactions were aggregated and no payment/mutation API was called.
- All monetary mutation APIs (create_transaction, payment requests, balance ops) were NOT invoked.
- The date is the simulated world's current date as returned by the live phone app; it may differ from
  real-world wall time.
