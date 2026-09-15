# DISCOVERY.md — Reconnaissance for paying Kristin for groceries and texting "It is done."

Task: mission-47d1e68d1bc405a7:task-1 (reconnaissance only — NO money or texts were sent).
Environment: AppWorld. Current simulated time reported by phone app: **Thursday, May 18, 2023, 12:00 PM**.

## 1. Available apps (apis.api_docs.show_app_descriptions())
`api_docs`, `supervisor`, `amazon`, `phone`, `file_system`, `spotify`, `venmo`, `gmail`, `splitwise`, `simple_note`, `todoist`.

Relevant apps:
- **venmo** — "A social payment app to send, receive and request money to and from others." (payment)
- **phone** — "An app to find and manage contact information for friends, family members, etc., send and receive messages, and manage alarms." (text messaging)

## 2. Supervisor read APIs — accounts / credentials
- `apis.supervisor.show_profile()` → Matthew Blackburn, email `matthew.blac@gmail.com`, phone `4886643554`, birthday 1982-12-15, male.
- `apis.supervisor.show_account_passwords()` →
  - venmo account_name `venmo`, password `f$paRge`
  - phone account_name `phone`, password `QG77Xz8`
- `apis.supervisor.show_payment_cards()` → 4 cards (Discover 8082949855768033, HSBC 2230851273011346, American Express 5427014608248470, MasterCard 3039044078327144).

Login used for read access (no money/text sent):
- `apis.phone.login(username='4886643554', password='QG77Xz8')` → `access_token`
- `apis.venmo.login(username='matthew.blac@gmail.com', password='f$paRge')` → `access_token`

## 3. Kristin — identity and contact
- Phone contact `apis.phone.search_contacts(access_token=..., query='Kristin')`:
  - **contact_id 824, first_name Kristin, last_name White, email `kri-powe@gmail.com`, phone_number `6017026518`, relationship "friend"**.
- Phone public profile `apis.phone.show_profile(phone_number='6017026518')` → Kristin White, `6017026518`, registered 2023-02-17.
- Venmo `apis.venmo.search_users(access_token=..., query='Kristin')` → Kristin White, email `kri-powe@gmail.com`, registered 2022-12-17, **friends_since 2022-12-17** (friend confirmed via `apis.venmo.show_profile(access_token=..., email='kri-powe@gmail.com')`, friends_since 2023-03-21).
- Only one "Kristin" appears in contacts and only one Kristin White on Venmo → recipient unambiguous.

## 4. Amount owed — message-thread evidence (phone)
Conversation with Kristin White (phone_number `6017026518`), via `apis.phone.search_text_messages(access_token=..., phone_number='6017026518')`:

| text_message_id | sent_at | sender | message |
|---|---|---|---|
| 16793 | 2023-05-17T13:17:11 | Matthew Blackburn | "hey, how much was yesterday's grocery?" |
| 16796 | 2023-05-17T13:18:03 | Kristin White | "It was $54." |
| 16797 | 2023-05-17T13:26:32 | Matthew Blackburn | "cool, I'll send it to you on venmo." |
| 16800 | 2023-05-17T13:27:35 | Kristin White | "great, thanks!" |

**Resolved amount owed for groceries = $54** (Kristin: "It was $54."; Matthew: "I'll send it to you on venmo.").

Context note (separate, NOT the grocery amount): later messages on 2023-05-18 — id 16803 Matthew: "sure, let's do it. That reminds me I owe you $25 from the last time."; id 16806 Kristin: "Oh right, how about you pay this time, and we'll call it even?"; id 16807 Matthew: "sounds good." The $25 refers to a different ("from the last time") obligation and was resolved by Kristin's "pay this time / call it even" for the movie outing — it is NOT labeled groceries and is not the amount for this task. The requested description note is "Groceries", which maps to the $54 grocery amount.

## 5. Payment app contract — Venmo `create_transaction`
`apis.api_docs.show_api_doc(app_name='venmo', api_name='create_transaction')`:
- path `/transactions`, method POST, description "Send money to a user."
- Required parameters:
  - `receiver_email` (string, email address of the receiver) → **`kri-powe@gmail.com`**
  - `amount` (number, must be > 0) → **`54`**
  - `access_token` (string from venmo login)
- Optional:
  - `description` (string) → **`"Groceries"`** (required by the mission note)
  - `payment_card_id` (integer; if not passed, Venmo balance is used)
  - `private` (boolean, default false)
- Success response: `{"message": string, "transaction_id": int}`.

Supporting read APIs:
- `apis.venmo.show_venmo_balance(access_token=...)` → `10202.0` (sufficient; balance will be used since no `payment_card_id` is needed). Venmo payment cards: ids 181 (Discover), 182 (HSBC), 183 (AmEx), 184 (MasterCard), if a card is preferred.
- `apis.venmo.show_transactions(access_token=..., user_email='kri-powe@gmail.com')` → existing transfers only; none has description "Groceries", so no duplicate exists. (Note: transaction_id 3190 on 2023-05-17T22:57:55 is Kristin→Matthew $54.00 "Computer Software"; it is a different description/direction and is not the reimbursement we need to send.)
- `apis.venmo.show_profile(access_token=..., email='kri-powe@gmail.com')` confirms friendship.

## 6. Messaging app contract — Phone `send_text_message`
`apis.api_docs.show_api_doc(app_name='phone', api_name='send_text_message')`:
- path `/messages/text/{phone_number}`, method POST, description "Send a text message on the given phone number."
- Required parameters:
  - `phone_number` (string) → **`6017026518`** (Kristin White)
  - `message` (string, length >= 1) → **`"It is done."`**
  - `access_token` (string from phone login)
- Success response: `{"message": string, "text_message_id": int}`.

## 7. Planned call sequence (for downstream mutation tasks)
```python
# 0. authenticate (ids/credentials above)
venmo_token = apis.venmo.login(username='matthew.blac@gmail.com', password='f$paRge')['access_token']
phone_token = apis.phone.login(username='4886643554', password='QG77Xz8')['access_token']

# 1. PAYMENT (task-2) — send $54 to Kristin with note "Groceries"
print(apis.venmo.create_transaction(
    receiver_email='kri-powe@gmail.com',
    amount=54,
    description='Groceries',
    access_token=venmo_token))
# expect: {"message": ..., "transaction_id": ...}

# 2. VERIFY payment = shows transaction with description "Groceries"
print(apis.venmo.show_transactions(
    access_token=venmo_token, user_email='kri-powe@gmail.com',
    direction='sent', page_limit=20))

# 3. TEXT (task-2) — send "It is done." to Kristin
print(apis.phone.send_text_message(
    phone_number='6017026518',
    message='It is done.',
    access_token=phone_token))
# expect: {"message": ..., "text_message_id": ...}

# 4. VERIFY text
print(apis.phone.search_text_messages(
    access_token=phone_token, phone_number='6017026518', page_limit=5))
```

## 8. Confirmations / constraints for this task
- Read-only reconnaissance performed; **no money and no text were sent**.
- Only `login` (token acquisition) and read APIs were called; both are non-destructive and do not send money/texts.
- Recipient resolved to a single Venmo account (`kri-powe@gmail.com`) and a single phone contact (`6017026518`).
- Recommended amount: **$54**, description: **"Groceries"**, message: **"It is done."**
- Payment source: Venmo balance ($10,202.00 available); `payment_card_id` optional.
