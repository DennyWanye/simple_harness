# DISCOVERY.md — Grocery repayment to Kristin (AppWorld task-1)

**Task:** Discover the identity of Kristin, the owed grocery amount, the phone-text context, the payment source, and the public APIs needed to send money and a text. **No money was sent and no text was sent in this task (discovery only).**

**Simulated current date/time:** Thursday, May 18, 2023, 12:00 PM (from `apis.phone.get_current_date_and_time()`).

---

## 1. The user (account owner)

`apis.supervisor.show_profile()`:
- Name: **Matthew Blackburn**
- Email: **matthew.blac@gmail.com**
- Phone number: **4886643554**
- Birthday 1982-12-15, male.

Venmo account (`apis.venmo.show_account`): Matthew Blackburn, matthew.blac@gmail.com, verified=True, friend_count=11.

---

## 2. Kristin's identity (recipient)

Phone contact book (`apis.phone.search_contacts(query="Kristin")`) found exactly one match:
- **Name: Kristin White**
- contact_id: **824**
- email: **kri-powe@gmail.com**
- phone_number: **6017026518**
- relationships: `["friend"]`
- birthday 1987-05-31.

Venmo user search (`apis.venmo.search_users(query="Kristin")`) confirmed:
- **Kristin White, email kri-powe@gmail.com**, `friends_since: 2022-12-17` (i.e., an existing Venmo friend of Matthew).

So the recipient is **Kristin White, email `kri-powe@gmail.com`, phone `6017026518`**. The Venmo receiver identifier needed by `create_transaction` is the email `kri-powe@gmail.com`.

---

## 3. The phone text conversation (source of the amount)

`apis.phone.search_text_messages(access_token=<phone token>, phone_number="6017026518")` returned the thread with Kristin (contact_id 824). The relevant, most-recent exchange:

| text_message_id | sent_at | sender | message |
|---|---|---|---|
| 16793 | 2023-05-17T13:17:11 | Matthew Blackburn (4886643554) | "hey, how much was yesterday's grocery?" |
| 16796 | 2023-05-17T13:18:03 | Kristin White (6017026518) | "It was $54." |
| 16797 | 2023-05-17T13:26:32 | Matthew Blackburn (4886643554) | "cool, I'll send it to you on venmo." |
| 16800 | 2023-05-17T13:27:35 | Kristin White (6017026518) | "great, thanks!" |
| 16802 | 2023-05-18T18:10:00 | Kristin White | "how about we go to watch a movie next weekend?" |
| 16803 | 2023-05-18T18:15:00 | Matthew Blackburn | "sure, let's do it. That reminds me I owe you $25 from the last time." |
| 16806 | 2023-05-18T18:20:42 | Kristin White | "Oh right, how about you pay this time, and we'll call it even?" |
| 16807 | 2023-05-18T18:30:17 | Matthew Blackburn | "sounds good." |

### Amount determination
- The grocery purchase Kristin covered is **$54** ("It was $54." from Kristin, 2023-05-17T13:18:03).
- Matthew explicitly committed to repay it: "cool, I'll send it to you on **venmo**." (2023-05-17T13:26:32).
- Therefore the owed grocery money to send is **$54.00**.
- The later "$25 from the last time" was NOT grocery and was settled conversationally ("you pay this time, and we'll call it even"), so it is **excluded**. Only the grocery $54 is owed now.

Cross-checks (no contradicting grocery figure found):
- `search_text_messages(query="grocery"/"groceries")`: only the above thread relates to Kristin; other hits are with Debra Ritter (roommate) about shopping, unrelated to repayment.
- Voice messages with Kristin (2023-03-17, 2022-08-19): no grocery/money content.
- Gmail inbox/outbox search for "grocery"/"Kristin": no grocery repayment figure involving Kristin (only unrelated "Grocery Shopping List"/Book Club threads).

---

## 4. Available payment sources

`apis.venmo.show_venmo_balance(access_token=<venmo token>)` → **venmo_balance = 10202.0** (more than sufficient for $54).

`apis.venmo.show_payment_cards(access_token=<venmo token>)`:
| payment_card_id | card_name | expiry | note |
|---|---|---|---|
| 181 | Discover | 2022-09 | expired |
| 182 | HSBC | 2023-12 | |
| 183 | American Express | 2023-09 | |
| 184 | MasterCard | 2023-08 | |

`apis.supervisor.show_payment_cards()` mirrors the same four cards (Discover/HSBC/Amex/MasterCard).

**Chosen source account:** the **Venmo balance** (`create_transaction` defaults to Venmo balance when `payment_card_id` is omitted). Balance ($10,202.00) easily covers $54, and the narrative says the cards were not working, consistent with using the balance instead of a card. Venmo bank transfer history is empty (`show_bank_transfer_history` → `[]`).

---

## 5. Current state checks (duplicate prevention)

- Venmo transactions (`apis.venmo.show_transactions`): **no transaction to Kristin White / kri-powe@gmail.com exists.** No $54 grocery payment has been sent yet.
- Notifications (`apis.venmo.show_notifications`): note notification 6006 — "Kristin White has approved your payment request of $54.00." This refers to payment_request **2268** ($54, description **"Computer Software"**, created 2023-05-16, sent by Matthew to Kristin, approved 2023-05-17T22:57:55). That is **Kristin paying Matthew** for a different purpose (Computer Software), **NOT** the grocery repayment. It must not be confused with, and does not satisfy, the grocery debt.
- Received payment requests: none from Kristin for groceries.
- So the grocery repayment of $54 is still **outstanding** and must be sent.

---

## 6. Chosen APIs and exact call plan

### A. Send the owed money (Venmo)
1. `login_resp = apis.venmo.login(username="matthew.blac@gmail.com", password="f$paRge")`
   - `venmo_token = login_resp["access_token"]`
2. Send money:
   ```python
   apis.venmo.create_transaction(
       receiver_email="kri-powe@gmail.com",
       amount=54,
       description="Groceries",
       access_token=venmo_token,
   )
   ```
   - `receiver_email` = `kri-powe@gmail.com` (Kristin White's Venmo email; she is an existing friend).
   - `amount` = `54` (USD).
   - `description` = **"Groceries"** (exact note required by the instruction).
   - No `payment_card_id` → uses the Venmo balance (sufficient).
   - Expected success schema: `{"message": str, "transaction_id": int}`.

### B. Send the phone text message (Phone)
1. `login_resp = apis.phone.login(username="4886643554", password="QG77Xz8")`
   - `phone_token = login_resp["access_token"]` (username is the phone number).
2. Send text:
   ```python
   apis.phone.send_text_message(
       phone_number="6017026518",
       message="It is done.",
       access_token=phone_token,
   )
   ```
   - `phone_number` = `6017026518` (Kristin White).
   - `message` = **"It is done."** (exact text required by the instruction).
   - Expected success schema: `{"message": str, "text_message_id": int}`.

**Order:** send the money first (A), verify it succeeded, then send the text (B).

---

## 7. Summary of the factual basis

| Item | Value |
|---|---|
| Recipient (name) | Kristin White |
| Recipient Venmo email | kri-powe@gmail.com |
| Recipient phone | 6017026518 |
| Owed amount | $54.00 |
| Currency | USD (Venmo balance / $) |
| Source account | Matthew Blackburn's Venmo balance (10202.0), cards 181–184 available but unused |
| Description note | "Groceries" |
| Payment API | `apis.venmo.create_transaction(receiver_email, amount, description, access_token)` |
| Text message API | `apis.phone.send_text_message(phone_number, message, access_token)` |
| Text body | "It is done." |

## 8. Limitations / caveats
- The "$25 from the last time" and the movie arrangement are explicitly netted out in the conversation and are excluded from the payment; only "Groceries" $54 is owed.
- Payment request 2268 ($54 "Computer Software") is a separate, already-approved request and is **not** the grocery repayment.
- Values above are observations from the live AppWorld state at the time of this discovery; the amounts/identities depend on that state.
- No mutation (money transfer or text message) was performed in this task.
