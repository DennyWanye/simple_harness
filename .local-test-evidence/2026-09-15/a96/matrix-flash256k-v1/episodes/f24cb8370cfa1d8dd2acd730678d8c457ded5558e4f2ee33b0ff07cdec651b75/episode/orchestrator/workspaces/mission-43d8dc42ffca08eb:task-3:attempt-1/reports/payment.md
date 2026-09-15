# Payment Report — mission-43d8dc42ffca08eb:task-2

Scope: execute the owed money transfer via the payment app (venmo) and verify it.
This task did NOT send any phone text message (that is task-3).

## Recipient and amount (from reports/discovery.md)
- Recipient: **Kristin White**, email **kri-powe@gmail.com** (Venmo friend since 2023-03-21).
- Amount: **54** (USD).
- Description note: **"Groceries"**.
- Source of amount: phone text conversation with Kristin (6017026518) of 2023-05-17, where Kristin said "It was $54."

## Pre-flight state (before the mutation)
- `apis.venmo.login(username='matthew.blac@gmail.com', password=<venmo password>)` → succeeded, returned a Bearer access_token.
- `apis.venmo.show_venmo_balance(access_token=...)` → `{"venmo_balance": 10202.0}`.
- `apis.venmo.show_transactions(access_token=...)` → no existing "Groceries" payment to Kristin; the grocery debt was still unsent.

## API call performed (mutation)
```python
apis.venmo.create_transaction(
    receiver_email='kri-powe@gmail.com',
    amount=54,
    access_token=<venmo access_token>,
    description='Groceries'
)
```
Result:
```json
{"message": "Sent money.", "transaction_id": 8216}
```

Parameters used:
- `receiver_email` = `kri-powe@gmail.com` (required)
- `amount` = `54` (required)
- `description` = `Groceries` (optional)
- `access_token` = Bearer token from venmo login (required)
- `payment_card_id` = not passed → Venmo balance was used
- `private` = not passed → default (false)

## Post-flight verification
- `apis.venmo.show_venmo_balance(access_token=...)` → `{"venmo_balance": 10148.0}` (decreased by exactly 54.0).
- `apis.venmo.show_transaction(transaction_id=8216, access_token=...)` →
```json
{
  "transaction_id": 8216,
  "amount": 54.0,
  "description": "Groceries",
  "created_at": "2023-05-18T12:00:00",
  "updated_at": "2023-05-18T12:00:00",
  "private": false,
  "like_count": 0,
  "payment_card_digits": null,
  "comment_count": 0,
  "sender": {"name": "Matthew Blackburn", "email": "matthew.blac@gmail.com"},
  "receiver": {"name": "Kristin White", "email": "kri-powe@gmail.com"}
}
```

## Confirmation
- Transaction identifier: **8216**
- Status: **success** ("Sent money."), and confirmed by `show_transaction` (amount 54.0, description "Groceries", receiver Kristin White / kri-powe@gmail.com) and the balance drop of 54.0.
- The owed grocery money has been sent with the exact description note "Groceries".

## Not performed in this task
- The follow-up phone text message "It is done." was NOT sent (deferred to task-3).

## Limitations
- Venmo `private` flag left at its default (false); this detail was not specified by the user.
- The `show_transactions` list returned only a page of records; earlier-state confirmation that no prior "Groceries" payment existed relies on the discovery report plus the observed balance.
