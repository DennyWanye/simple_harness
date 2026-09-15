# Venmo Recipient Discovery — Task A

**Task:** Discover Venmo APIs and resolve phone number `2192158297` to the exact Venmo recipient/user, then document the recipient identity and discovery evidence.

**Status:** COMPLETE (discovery only). No payment was sent in this task.

---

## 1. Conclusion

Phone number **`2192158297`** resolves to:

| Field | Value |
|---|---|
| Name | **Thomas Solomon** |
| Venmo email | **thomas.solomon@gmail.com** |
| Phone (from contact book) | 2192158297 |
| Venmo `registered_at` | 2022-05-09T14:11:52 |
| Venmo `friends_since` (with supervisor) | null (supervisor is NOT a Venmo friend of this user) |
| Phone contact_id | 407 |
| Relationship (phone contact book) | coworker |

The exact Venmo recipient is the user whose email is **`thomas.solomon@gmail.com`**.

---

## 2. Why the phone number must be resolved via the Phone app

Venmo's public API has **no phone-number search endpoint**. `venmo.search_users` accepts only a `query` string documented as *"Search Venmo users by name or email address"* and `venmo.show_profile` accepts only an `email`. Therefore the phone number had to be resolved to a person (and that person's email) through the Phone app contact book, after which the email was confirmed to be a real Venmo user.

### Relevant Venmo API signatures (from `apis.api_docs.show_api_doc`)
- `venmo.search_users(access_token, query="", page_index=0, page_limit=5)` → list of `{first_name, last_name, email, registered_at, friends_since}`
- `venmo.show_profile(access_token, email=null)` → `{first_name, last_name, email, registered_at, friends_since}`
- `venmo.create_transaction(receiver_email, amount, access_token, description="", payment_card_id=null, private=false)` → `{message, transaction_id}`
  - `receiver_email` is the email address of the receiver; `private` boolean controls privacy.

### Relevant Phone API signatures
- `phone.login(username=<phone_number>, password)` → `{access_token, token_type}`
- `phone.search_contacts(access_token, query="", relationship=null, page_index=0, page_limit=5)` → list of contacts incl. `email`, `phone_number`, `relationships`, `contact_id`.
- `phone.show_profile(phone_number=null)` → `{first_name, last_name, phone_number, registered_at}`

---

## 3. Actions performed (raw observations)

### 3.1 Authenticated to Phone and Venmo as supervisor Jessica Miller
- Supervisor profile: `Jessica Miller`, email `jes.mill@gmail.com`, phone `3808719492`.
- `phone.login(username="3808719492", password="5!Dkf5d")` → `succeeded`, returned an access_token.
- `venmo.login(username="jes.mill@gmail.com", password="*mR5XTY")` → `succeeded`, returned an access_token.

### 3.2 Resolved phone number 2192158297 in the Phone contact book
`phone.search_contacts(query="2192158297")` returned (first result):

```json
{
  "contact_id": 407,
  "first_name": "Thomas",
  "last_name": "Solomon",
  "email": "thomas.solomon@gmail.com",
  "phone_number": "2192158297",
  "relationships": ["coworker"],
  "birthday": "1958-02-21",
  "home_address": "2317 Powell Stream Suite 570\nSeattle\nWashington\nUnited States\n32418",
  "work_address": "5840 Craig Turnpike Suite 634\nSeattle\nWashington\nUnited States\n78487",
  "created_at": "2021-11-06T13:17:49"
}
```

`phone.show_profile(phone_number="2192158297")` returned:

```json
{
  "first_name": "Thomas",
  "last_name": "Solomon",
  "phone_number": "2192158297",
  "registered_at": "2022-10-05T14:24:17"
}
```

**Uniqueness check:** The full contact book was paginated (22 contacts total). Exactly **one** contact matched `phone_number == "2192158297"` — contact_id 407, Thomas Solomon. No duplicate/ambiguity.

### 3.3 Confirmed the email as a Venmo user
`venmo.search_users(query="thomas.solomon@gmail.com")` returned Thomas Solomon first:

```json
[
  {"first_name":"Thomas","last_name":"Solomon","email":"thomas.solomon@gmail.com","registered_at":"2022-05-09T14:11:52","friends_since":null},
  {"first_name":"Stephen","last_name":"Mccoy","email":"stmcco@gmail.com","registered_at":"2023-01-24T12:51:50","friends_since":null},
  {"first_name":"Laura","last_name":"Mccoy","email":"la-mcco@gmail.com","registered_at":"2023-02-12T14:30:46","friends_since":null},
  {"first_name":"Chris","last_name":"Mccoy","email":"chris.mcco@gmail.com","registered_at":"2022-08-25T08:57:40","friends_since":null},
  {"first_name":"Jonathan","last_name":"Ball","email":"jo.ball@gmail.com","registered_at":"2022-08-26T16:01:41","friends_since":"2022-08-26T16:01:41"}
]
```

`venmo.show_profile(email="thomas.solomon@gmail.com")` returned:

```json
{
  "first_name": "Thomas",
  "last_name": "Solomon",
  "email": "thomas.solomon@gmail.com",
  "registered_at": "2022-05-09T14:11:52",
  "friends_since": null
}
```

`venmo.search_users(query="Thomas Solomon")` also returned `thomas.solomon@gmail.com` as the top result (distinct from other Solomons: Jamie Solomon `jamie-solomon@gmail.com`, James Solomon `ja-solomon@gmail.com`, Troy Solomon `tr_solo@gmail.com`). The exact email is the unique identifier.

---

## 4. Discovery chain (evidence summary)

1. Supervisor = Jessica Miller (`jes.mill@gmail.com`, phone `3808719492`). — `supervisor.show_profile`
2. Phone `2192158297` → contact **Thomas Solomon**, email **thomas.solomon@gmail.com** (contact_id 407, coworker). — `phone.search_contacts`, `phone.show_profile`
3. Email `thomas.solomon@gmail.com` → existing Venmo user **Thomas Solomon**. — `venmo.search_users`, `venmo.show_profile`
4. Uniqueness confirmed over the full 22-entry contact book; only one record uses `2192158297`.

**Exact Venmo recipient for a future payment:** email **`thomas.solomon@gmail.com`** (Thomas Solomon).

---

## 5. Limitations / notes for the payment task (Task B)

- Venmo exposes no phone-number lookup; identity resolution necessarily goes through the Phone app contact book.
- The supervisor is **not** a Venmo friend of Thomas Solomon (`friends_since` is null). Whether Venmo allows sending to a non-friend is untested here; Task B should verify the `create_transaction` result rather than assume success.
- To send **privately**, Task B must pass `private=true` to `venmo.create_transaction` and `amount=91`.
- No money was moved and no Venmo transaction was created in this task. This report is discovery/identity evidence only; it does not assert that any payment occurred.
