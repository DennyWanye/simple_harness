# Recipient / Payer / Privacy Confirmation — task-2

Mission root goal: Send $91 privately on Venmo to the person with this phone number 2192158297.

Task-2 scope: locate the payer's Venmo account and the recipient whose phone number is 2192158297,
confirm recipient identity, payer identity, and whether a private $91 payment is supported.
**No payment was sent** (no `create_transaction`, no `update_transaction`, no friend/balance/card mutation).

## 1. Payer identity (simulated user)

- `apis.supervisor.show_profile()` →
  `{'first_name': 'Jessica', 'last_name': 'Miller', 'email': 'jes.mill@gmail.com',
    'phone_number': '3808719492', 'birthday': '1962-11-19', 'sex': 'female'}`
- `apis.supervisor.show_account_passwords()` → venmo account password `*mR5XTY` (used to log in; not stored elsewhere).
- `apis.venmo.login(username='jes.mill@gmail.com', password='*mR5XTY')` succeeded, returned a Bearer `access_token`.
- `apis.venmo.show_account(access_token=...)` →
  `{'first_name': 'Jessica', 'last_name': 'Miller', 'email': 'jes.mill@gmail.com',
    'registered_at': '2022-12-09T17:34:40', 'last_logged_in': '2022-12-09T17:34:40',
    'verified': True, 'venmo_balance': 0.0, 'friend_count': 12}`
- `apis.venmo.show_venmo_balance(access_token=...)` → `{'venmo_balance': 0.0}`

Conclusion — payer is **Jessica Miller**, Venmo account email **jes.mill@gmail.com**, account verified.
Note: Venmo balance is **$0.00**, so a send may need an explicit `payment_card_id`
(`apis.supervisor.show_payment_cards()` listed Visa/MasterCard/Wells Fargo/HSBC/Amex for Jessica Miller);
this is relevant for task-3, not resolved here.

## 2. Recipient identity (phone number 2192158297)

Venmo has no phone-number lookup; the number was resolved via the **phone** app:

- `apis.phone.show_profile(phone_number='2192158297')` →
  `{'first_name': 'Thomas', 'last_name': 'Solomon', 'phone_number': '2192158297',
    'registered_at': '2022-10-05T14:24:17'}`
- `apis.phone.search_contacts(access_token=<phone token>, query='2192158297')` returned the same person first:
  `contact_id=407, first_name='Thomas', last_name='Solomon', email='thomas.solomon@gmail.com',
   phone_number='2192158297', relationships=['coworker'], created_at='2021-11-06T13:17:49'`
- A full contact-list scan (`page_limit=20`) contains exactly one contact with phone `2192158297`
  (contact_id 407, Thomas Solomon). No other contact shares that phone number.

Cross-check on Venmo:
- `apis.venmo.show_profile(access_token=..., email='thomas.solomon@gmail.com')` →
  `{'first_name': 'Thomas', 'last_name': 'Solomon', 'email': 'thomas.solomon@gmail.com',
    'registered_at': '2022-05-09T14:11:52', 'friends_since': None}`
- `apis.venmo.search_users(access_token=..., query='Thomas Solomon')` returns Thomas Solomon
  (email thomas.solomon@gmail.com) as the top hit, distinct from other "Solomon" users.

Conclusion — the person with phone **2192158297** is **Thomas Solomon**, whose Venmo email is
**thomas.solomon@gmail.com** (a work/phone contact, relationship "coworker"; not a Venmo friend,
`friends_since = None`).

## 3. Is a private $91 payment supported?

- `apis.venmo.create_transaction` (POST /transactions) params include:
  `receiver_email` (string, required, must be an email), `amount` (number, required, > 0),
  `access_token` (required), `description` (optional), `payment_card_id` (optional; else Venmo balance used),
  **`private` (boolean, optional, default `false`)**.
- Therefore a **private** payment is directly supported by passing `private=True` at creation time.
  (A post-hoc change is also possible via `update_transaction`, PATCH /transactions/{id}, but that schema
  requires `description`, so setting privacy at creation is the cleaner path.)
- Amount $91 satisfies the `amount > 0` constraint.
- `apis.venmo.show_transactions(access_token=..., user_email='thomas.solomon@gmail.com')` shows existing
  transactions with Thomas of amounts 46, 283, 18, 178, 26 — **no pre-existing $91 transaction**
  (confidence this is a unique amount: amounts seen are 46/283/18/178/26, none equal to 91).

## 4. Ready-to-use inputs for the payment task (task-3) — recorded, NOT executed

- payer/access: `username='jes.mill@gmail.com'`, `password='*mR5XTY'` (venmo login)
- `receiver_email = 'thomas.solomon@gmail.com'`
- `amount = 91`
- `private = True`
- Reservation: Venmo balance is $0.0; if the send requires a funding source, `payment_card_id` from
  `apis.supervisor.show_payment_cards()` may be required (to be determined during task-3 observation).

## 5. Actions performed in this task (all read-only or doc reads)

- `apis.api_docs.show_app_descriptions()`
- `apis.api_docs.show_api_descriptions(app_name='phone')`
- `apis.api_docs.show_api_doc(...)` for phone.login/search_contacts/show_profile and
  venmo.login/show_profile/search_users/create_transaction/show_transaction/show_transactions
- `apis.supervisor.show_profile()`, `apis.supervisor.show_account_passwords()`, `apis.supervisor.show_payment_cards()`
- `apis.venmo.login(...)`, `apis.venmo.show_account(...)`, `apis.venmo.show_venmo_balance(...)`,
  `apis.venmo.show_profile(email=...)`, `apis.venmo.search_users(...)`, `apis.venmo.show_transactions(...)`
- `apis.phone.login(...)`, `apis.phone.show_profile(phone_number=...)`, `apis.phone.search_contacts(...)`

No payment or other mutating call was made.

## 6. Limitations / open items

- Venmo cannot search by phone number directly; resolution relied on the phone/contacts app, whose
  `query` parameter matched the phone string. Only one contact matched 2192158297, so ambiguity is low.
- `venmo.show_profile` without an `email` returned HTTP 422 ("Account for this email does not exist."),
  so self profile was read via `show_account` instead.
- Whether the $91 send needs an explicit `payment_card_id` (given $0 Venmo balance) is not decided here;
  it must be observed and handled in task-3. Payment was intentionally not sent.
