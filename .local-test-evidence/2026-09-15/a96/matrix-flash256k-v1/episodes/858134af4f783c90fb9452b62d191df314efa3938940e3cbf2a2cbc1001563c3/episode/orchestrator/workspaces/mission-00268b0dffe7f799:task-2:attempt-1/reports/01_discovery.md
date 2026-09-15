# Discovery Report — AppWorld Venmo Payment APIs (task-1)

Mission root goal: Send $91 privately on Venmo to the person with phone number 2192158297.
This task (task-1) is discovery-only. **No payment state was mutated** (no create/update transaction,
no friend add/remove, no balance change was called).

## 1. Application inventory (read-only)

Called `apis.api_docs.show_app_descriptions()`. Available apps:

- api_docs — search/explore API documentation
- supervisor — supervisor personal info, account credentials, addresses, payment cards, task management
- amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist

Relevant to this mission: **supervisor** (account discovery/credentials) and **venmo** (payment),
plus **phone** (resolve a phone number to a contact identity/email).

## 2. Supervisor account-discovery APIs (read-only)

`apis.api_docs.show_api_descriptions(app_name='supervisor')` + per-API docs returned:

| API | path/method | params | purpose |
|---|---|---|---|
| show_profile | GET /profile | none | supervisor identity (name, email, phone_number, birthday, sex) |
| show_addresses | GET /addresses | none | supervisor addresses |
| show_payment_cards | GET /payment_cards | none | supervisor payment cards |
| show_account_passwords | GET /account_passwords | none | {account_name, password} for each app → used to log in |
| show_active_task | GET /active_task | none | current assigned task instruction/status/answer |
| complete_task | POST /message | answer?, status? | mark task complete (only when whole goal met) |

Read-only observation (`apis.supervisor.show_profile()`):

```
{'first_name': 'Jessica', 'last_name': 'Miller', 'email': 'jes.mill@gmail.com',
 'phone_number': '3808719492', 'birthday': '1962-11-19', 'sex': 'female'}
```

So the simulated user's Venmo login username is expected to be `jes.mill@gmail.com`.
`apis.supervisor.show_account_passwords()` returns (among others) `{'account_name': 'venmo', 'password': '*mR5XTY'}`.
No payment data was changed by these calls.

## 3. Venmo APIs (read-only docs)

`apis.api_docs.show_api_descriptions(app_name='venmo')` exposed the full list. Key APIs:

### 3a. Authentication / login
- **login** — POST /auth/token
  - params: `username` (string, required; account email), `password` (string, required)
  - success: `{access_token, token_type}` → the `access_token` is required by every other Venmo call.
- show_account (GET /account), show_venmo_balance (GET /balance) — read-only account state.

### 3b. Looking up a user
- **search_users** — GET /users
  - params: `access_token` (required), `query` (string, optional; name **or email address**), `page_index` (>=0, default 0), `page_limit` (1–20, default 5)
  - success: list of `{first_name, last_name, email, registered_at, friends_since}`
  - Limitation: searches by **name or email only — it does NOT accept a phone number**.
- **show_profile** — GET /profile
  - params: `access_token` (required), `email` (optional, must be an email address)
  - success: `{first_name, last_name, email, registered_at, friends_since}`

### 3c. Finding a recipient by phone number
Venmo has **no phone-number lookup parameter**. The phone number must be resolved to a
contact identity (and its email) via the **phone** app, then passed to Venmo as `receiver_email`:
- **phone.search_contacts** — GET /contacts
  - params: `access_token` (required; from phone app login), `query` (string, optional),
    `relationship` (optional), `page_index` (>=0, default 0), `page_limit` (1–20, default 5)
  - success: list of `{contact_id, first_name, last_name, email, phone_number, relationships, birthday, home_address, work_address, created_at}`
- **phone.show_profile** — GET /profile
  - params: `phone_number` (string, optional)
  - success: `{first_name, last_name, phone_number, registered_at}`
- phone.login — POST /auth/token: params `username` (phone number), `password`; returns `access_token`.

Because `search_contacts` has no dedicated phone parameter, the phone number `2192158297` is used
as the `query` string, and/or the full contact list is paged until a matching `phone_number` is found.
The matching contact's `email` is the value to use for the Venmo payment.

### 3d. Sending a payment
- **create_transaction** — POST /transactions — "Send money to a user."
  - params:
    - `receiver_email` (string, **required**, must be an email address)
    - `amount` (number, **required**, must be > 0) → use `91`
    - `access_token` (string, **required**)
    - `description` (string, optional, default "")
    - `payment_card_id` (integer, optional; if omitted Venmo balance is used)
    - `private` (boolean, optional, default `false`)
  - success: `{message, transaction_id}`
- This is the single mutating call that the later task will perform. It was **NOT** called here.

### 3e. Controlling privacy
Two ways:
1. Set at creation: pass `private=true` in **create_transaction** (default is `false` = public).
2. Change afterwards: **update_transaction** — PATCH /transactions/{transaction_id}
   - params: `transaction_id` (required), `description` (string, **required** by schema),
     `access_token` (required), `private` (boolean, optional)
   - Note: `description` is listed as required, so an update needs a description value even when
     only changing privacy.
- Verification/privacy reads: **show_transactions** (GET /transactions; filters incl. `private`,
  `direction` in ['sent','received'], `user_email`, `min_amount`/`max_amount`, `sort_by`) and
  **show_transaction** (GET /transactions/{transaction_id}) returns the `private` field.

## 4. Recommended call sequence for the mission (to be executed in later tasks)

1. `apis.supervisor.show_profile()` → email `jes.mill@gmail.com` (done, read-only).
2. `apis.supervisor.show_account_passwords()` → venmo password (done, read-only).
3. `apis.venmo.login(username='jes.mill@gmail.com', password='*mR5XTY')` → `access_token`.
4. `apis.phone.login(...)` (if needed) + `apis.phone.search_contacts(query='2192158297')`
   to resolve phone 2192158297 → email. (Alternatively paged search_contacts scan.)
5. `apis.venmo.create_transaction(receiver_email=<resolved email>, amount=91,
   access_token=<token>, private=True, description=...)` → `transaction_id`.
6. Verify with `apis.venmo.show_transaction(transaction_id=..., access_token=...)` that
   `amount == 91` and `private == True`.

## 5. Explicit non-actions / limitations

- No `create_transaction`, `update_transaction`, `add_friend`, `remove_friend`, balance, or
  payment-card mutation was invoked in this task. Only docs and read-only supervisor APIs were called.
- The recipient's email for phone 2192158297 was **not** resolved here (that is task-2); Venmo
  cannot look up by phone number directly, so it must go through the phone/contacts app.
- `phone.search_contacts` filters on name/relationship only; a phone-number query relies on the
  app matching the `query` string against the contact record (or full-list paging).
- `update_transaction` requires a `description`, which limits privacy-only edits.
