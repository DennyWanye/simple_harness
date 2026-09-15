# Payment Execution — task-3

Mission root goal: Send $91 privately on Venmo to the person with this phone number 2192158297.

Task-3 scope: execute **exactly one** Venmo payment of $91 to recipient phone 2192158297 with
privacy set to `private`; check the API response and subsequent transaction state; record exact
action, parameters, and observed result. Do not repeat a successful mutation without checking state.

Outcome: **SUCCESS — one (and only one) $91 private payment was created and verified.**

## 1. Pre-flight state check (before mutating)

- `apis.venmo.login(username='jes.mill@gmail.com', password='*mR5XTY')` -> Bearer `access_token`.
- `apis.venmo.show_account(access_token=...)` ->
  `{'first_name': 'Jessica', 'last_name': 'Miller', 'email': 'jes.mill@gmail.com',
    'verified': True, 'venmo_balance': 0.0, 'friend_count': 12}`.
- `apis.venmo.show_venmo_balance(access_token=...)` -> `{'venmo_balance': 0.0}`.
- `apis.venmo.show_transactions(access_token=..., user_email='thomas.solomon@gmail.com',
   direction='sent', page_limit=20)` -> 7 prior transactions to Thomas (amounts 46, 283, 18, 178,
   26, 192, 12); **no $91 transaction existed beforehand**. So no prior successful mutation had to
  be avoided; the payment below is the first/only $91.

## 2. The single successful mutation

Call:
```
apis.venmo.create_transaction(
    access_token   = <jes.mill@gmail.com Bearer token>,
    receiver_email = 'thomas.solomon@gmail.com',
    amount         = 91,
    private        = True,
    description    = '',
    payment_card_id= 99            # MasterCard, card_number 3764012372333477
)
```
Response:
```
{'message': 'Sent money.', 'transaction_id': 8216}
```

- `receiver_email = 'thomas.solomon@gmail.com'` is Thomas Solomon, the person whose phone number
  is 2192158297 (resolved in task-2 via `apis.phone.show_profile(phone_number='2192158297')` and
  `apis.phone.search_contacts`; contact_id 407, relationship "coworker").
- Payer / sender = Jessica Miller (`jes.mill@gmail.com`), the simulated user.
- Exactly the requested amount `91`, with `private=True` at creation time.

## 3. Post-mutation verification

- `apis.venmo.show_transaction(access_token=..., transaction_id=8216)` ->
```
{'transaction_id': 8216,
 'amount': 91.0,
 'description': '',
 'created_at': '2023-05-18T12:00:00',
 'updated_at': '2023-05-18T12:00:00',
 'private': True,
 'payment_card_digits': '3477',
 'sender':   {'name': 'Jessica Miller', 'email': 'jes.mill@gmail.com'},
 'receiver': {'name': 'Thomas Solomon', 'email': 'thomas.solomon@gmail.com'}}
```
  Confirms `amount == 91.0`, `private == True`, correct receiver and sender.

- `apis.venmo.show_transactions(access_token=..., user_email='thomas.solomon@gmail.com',
   direction='sent', page_limit=20)` -> exactly **one** $91 sent transaction to Thomas
  (id 8216, private True). No duplicates.

- `apis.venmo.show_venmo_balance(access_token=...)` -> `{'venmo_balance': 0.0}` (unchanged; the
  payment was funded by the MasterCard, `payment_card_digits` reported as "3477").

## 4. Funding-source details (why a card was required)

Venmo balance was $0.00, so `create_transaction` without a `payment_card_id` failed:
`422 "Your Venmo balance does not have $91.00 to make this transaction."`

Payment cards were read via `apis.venmo.show_payment_cards(access_token=...)`:
| payment_card_id | card_name | card_number | expiry |
|---|---|---|---|
| 95 | Wells Fargo | 6957388994762429 | 2022-07 |
| 96 | HSBC | 6138976649422699 | 2024-06 |
| 97 | American Express | 9043636371276781 | 2023-07 |
| 98 | Visa | 2068816277984906 | 2023-11 |
| 99 | MasterCard | 3764012372333477 | 2024-06 |

Attempts and their observed results (only the last one succeeded; failed calls do not mutate state):
- no card: `422 "Your Venmo balance does not have $91.00 to make this transaction."`
- id 98 (Visa): `422 "Your payment card does not have $91.00 to make this transaction."`
- id 95 (Wells Fargo): `422 "The payment card has expired."`
- id 96 (HSBC): `422 "Your payment card does not have $91.00 to make this transaction."`
- id 97 (American Express): `422 "Your payment card does not have $91.00 to make this transaction."`
- id 99 (MasterCard): **SUCCESS** -> `{'message': 'Sent money.', 'transaction_id': 8216}`

So the successful transaction was funded by payment card id 99 (MasterCard). Exactly one
mutation succeeded; no duplicate payment was created.

## 5. Actions performed in this task

- `apis.venmo.login`, `apis.venmo.show_account`, `apis.venmo.show_venmo_balance` (read)
- `apis.venmo.show_transactions` (read)
- `apis.venmo.show_payment_cards` (read), `apis.api_docs.show_api_doc('venmo','create_transaction')`
- `apis.supervisor.show_payment_cards` (read)
- `apis.venmo.create_transaction` x6 attempts, of which exactly 1 succeeded (id 99)
- `apis.venmo.show_transaction` (read, verification)

## 6. Limitations / notes

- The user goal does not specify a funding source; the simulation required a payment card because the
  Venmo balance was $0.00. The card actually charged is MasterCard (id 99, digits 3477).
- `description` was left empty (default) since the goal did not request a note.
- `apis.supervisor.complete_task()` is intentionally NOT called here: per contract, the whole
  mission must be completed before that call, and final verification is another task's scope. This
  task only executes and verifies the single payment.
