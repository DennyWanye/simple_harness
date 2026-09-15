# RECON.md — Reconnaissance for "Pay Kristin for groceries + text confirmation"

Mission root goal: Send Kristin the owed grocery money with description note "Groceries" (as per the phone text conversation), then send them a phone text, "It is done."
This Task (task-1) is **reconnaissance only**. NO payment was made and NO text was sent. All findings below are from read-only AppWorld API calls made on 2023-05-18.

## 1. Identities

### 1.1 Simulated user (the "supervisor")
Source: `apis.supervisor.show_profile()`
- first_name: Matthew
- last_name: Blackburn
- email: matthew.blac@gmail.com
- phone_number: 4886643554

Supervisor app account passwords (`apis.supervisor.show_account_passwords()`):
- phone: `QG77Xz8`
- venmo: `f$paRge`
- (others: amazon, file_system, gmail, simple_note, splitwise, spotify, todoist)

### 1.2 Recipient: Kristin
Source: `apis.phone.search_contacts(access_token=<phone_token>, query="Kristin")`
- contact_id: 824
- first_name: Kristin
- last_name: White
- email: kri-powe@gmail.com
- phone_number: 6017026518
- relationships: ["friend"]

Corroborated in Venmo (`apis.venmo.search_friends(access_token=<venmo_token>, query="Kristin")` and `apis.venmo.search_users(...)`):
- first_name: Kristin, last_name: White, email: kri-powe@gmail.com, friends_since: 2023-03-21T15:34:18
- `apis.venmo.show_profile(access_token=<venmo_token>, email="kri-powe@gmail.com")` → {'first_name': 'Kristin', 'last_name': 'White', 'email': 'kri-powe@gmail.com', 'registered_at': '2022-12-17T11:05:29', 'friends_since': '2023-03-21T15:34:18'}

Phone contact email and Venmo friend email are identical (kri-powe@gmail.com), so the phone contact and the Venmo recipient are the same person.

**Ambiguity / resolution statement:** The recipient identity was resolved **unambiguously**. Only one contact named "Kristin" exists in the phone contact book ("Kristin White", contact_id 824). Her phone number (6017026518) and email (kri-powe@gmail.com) are consistent, and she is an existing Venmo friend under the same email. Note: "Sierra White" (siwhit@gmail.com) also appears in Venmo results, but is a different person and was not the subject of the grocery conversation. **Candidate identities enumerated: none beyond Kristin White.** No guessing was required.

## 2. Phone-text conversation evidence (user owes Kristin for groceries)
Source: `apis.phone.show_text_message_window(phone_number="6017026518", access_token=<phone_token>, page_limit=20)` (and `search_text_messages(query="grocery")`).

Verbatim quotes from the conversation between Matthew Blackburn (4886643554) and Kristin White (6017026518):
- 2023-05-17T13:17:11 — Matthew → Kristin: "hey, how much was yesterday's grocery?"
- 2023-05-17T13:18:03 — Kristin → Matthew: "It was $54."
- 2023-05-17T13:26:32 — Matthew → Kristin: "cool, I'll send it to you on venmo."
- 2023-05-17T13:27:35 — Kristin → Matthew: "great, thanks!"

Additional context later in the same thread (not the grocery amount, but same contact):
- 2023-05-18T18:15:00 — Matthew → Kristin: "sure, let's do it. That reminds me I owe you $25 from the last time."
- 2023-05-18T18:20:42 — Kristin → Matthew: "Oh right, how about you pay this time, and we'll call it even?"

This establishes: (a) the user owes Kristin **$54** for the grocery; (b) the agreed payment channel is **Venmo** ("I'll send it to you on venmo").

Note on amount ambiguity: The grocery amount is unambiguously **$54**. The separate "$25 from the last time" remark is context about a different prior debt that Kristin offered to settle by Matthew covering the next movie outing ("we'll call it even"); it is NOT part of the grocery amount.

## 3. Payment app & exact API call shape (VERIFIED via api_docs)

Payment app: **venmo**.
Endpoint: `apis.venmo.create_transaction` — POST /transactions — "Send money to a user."

Verified parameters via `apis.api_docs.show_api_doc(app_name='venmo', api_name='create_transaction')`:
- `receiver_email` (string, **required**) — "Email address of the receiver." constraint: value is email address
- `amount` (number, **required**) — "Amount of the transaction." constraint: value > 0.0
- `access_token` (string, **required**) — "Access token obtained from venmo app login."
- `description` (string, optional, default "") — "Description of or note about the transaction."
- `payment_card_id` (integer, optional, default null) — "ID of the payment card to use for the transaction. If not passed, Venmo balance will be used."
- `private` (boolean, optional, default false) — whether the transaction is private.

Login requirement (`apis.api_docs.show_api_doc(app_name='venmo', api_name='login')`): `username` (= account email) and `password`, both required; returns `access_token`.

Planned (NOT executed) payment call shape:
```
apis.venmo.create_transaction(
    receiver_email="kri-powe@gmail.com",
    amount=54,
    access_token=<venmo access_token>,
    description="Groceries",
)
```
- Venmo login used: `apis.venmo.login(username="matthew.blac@gmail.com", password="f$paRge")` → access_token obtained successfully.
- `apis.venmo.show_account(...)` showed venmo_balance = 10202.0, so paying $54 from the default Venmo balance is feasible (no payment_card_id required).
- No existing "$54 Groceries" transaction with Kristin was found (`apis.venmo.show_transactions(access_token=<venmo_token>, user_email="kri-powe@gmail.com")`), reducing double-payment risk.

## 4. Phone/text app & exact API call shape (VERIFIED via api_docs)

Phone app: **phone**.
Endpoint: `apis.phone.send_text_message` — POST /messages/text/{phone_number} — "Send a text message on the given phone number."

Verified parameters via `apis.api_docs.show_api_doc(app_name='phone', api_name='send_text_message')`:
- `phone_number` (string, **required**) — "The phone number of the contact to send the message to."
- `message` (string, **required**) — "The content of the text message." constraint: length >= 1
- `access_token` (string, **required**) — "Access token obtained from phone app login."

Login requirement (`apis.api_docs.show_api_doc(app_name='phone', api_name='login')`): `username` (= account phone_number) and `password`, both required; returns `access_token`.

Planned (NOT executed) text call shape:
```
apis.phone.send_text_message(
    phone_number="6017026518",
    message="It is done.",
    access_token=<phone access_token>,
)
```
- Phone login used: `apis.phone.login(username="4886643554", password="QG77Xz8")` → access_token obtained successfully.

## 5. Planned (but NOT performed in this Task) mutation sequence for task-2
1. `apis.phone.login(username="4886643554", password="QG77Xz8")` (phone token) — already obtained in recon.
2. `apis.venmo.login(username="matthew.blac@gmail.com", password="f$paRge")` (venmo token) — already obtained in recon.
3. `apis.venmo.create_transaction(receiver_email="kri-powe@gmail.com", amount=54, access_token=<venmo_token>, description="Groceries")`.
4. Verify with `apis.venmo.show_transactions(access_token=<venmo_token>, user_email="kri-powe@gmail.com")` that a $54 "Groceries" transaction to Kristin exists.
5. `apis.phone.send_text_message(phone_number="6017026518", message="It is done.", access_token=<phone_token>)`.
6. Verify with `apis.phone.show_text_message_window(phone_number="6017026518", access_token=<phone_token>)`.

## 6. Blockers / caveats
- None blocking. All required identifiers and API signatures were read from `apis.api_docs.show_api_doc` and confirmed with successful read-only calls.
- Tokens issued during recon (`exp` in the JWT payload) may expire; task-2 should re-login before mutating if a 401 occurs.

## 7. What was NOT done (per task contract)
- No Venmo transaction was created.
- No phone text message was sent.
- Only read-only APIs were used: supervisor.show_profile/show_addresses/show_payment_cards/show_account_passwords; phone.get_current_date_and_time/login/search_contacts/show_text_message_window/search_text_messages/show_account; venmo.login/search_users/search_friends/show_profile/show_transactions/show_account/show_venmo_balance.
