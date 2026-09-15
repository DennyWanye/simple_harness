# Recon A — Read-Only Reconnaissance (Task-1)

Shared AppWorld state. **No business mutations were performed.** Only discovery/read calls plus
authentication (login → access token) were issued. Tokens are auth artifacts, not business mutations.

---

## 1. Environment / current date & the "yesterday" window

- API: `apis.phone.get_current_date_and_time()` → `{"date": "Thursday, May 18, 2023", "time": "12:00 PM"}`
- **Assumption (date window):** "yesterday" = **2023-05-17 (Wednesday, May 17, 2023)**.
  - Confirmed by the matching business data below (all dinner transactions are stamped `2023-05-17T14:46:15`).

## 2. User (supervisor) identity & accounts

- `apis.supervisor.show_profile()` →
  - first_name: `Ashlee`, last_name: `Martinez`
  - email: `ashlee_martinez@gmail.com`
  - phone_number: `3506492550`
  - birthday: `1960-10-16`, sex: `female`
- App accounts from `apis.supervisor.show_account_passwords()` (names only; passwords are credentials,
  stored in the live app, not reproduced here):
  `amazon`, `file_system`, `gmail`, `phone`, `simple_note`, `splitwise`, `spotify`, `todoist`, `venmo`.
- Venmo account (`apis.venmo.show_account`, after login):
  - `Ashlee Martinez`, `ashlee_martinez@gmail.com`, `friend_count: 12`, `venmo_balance: 5432.0`, `verified: true`.
- Phone account (`apis.phone.login`) uses `username = phone_number` = `3506492550`.

## 3. Manager and coworkers (phone contact book)

Source: `apis.phone.show_contact_relationships()` →
`["child", "coworker", "friend", "husband", "manager", "partner", "son"]`
Source: `apis.phone.search_contacts(access_token=ptoken, page_index=0..2, page_limit=20)` (page 1/2 empty; 16 contacts total).

- **Manager: Spencer Powell** — `spencer.powell@gmail.com`, phone `8267279358`,
  relationships `["manager", "coworker"]`.
- **Coworkers** (relationship contains `"coworker"`), sharing work address
  `8875 Amy Extensions Suite 797, Seattle, Washington, United States, 49596`:
  - Jordan Harrison — `jo-harr@gmail.com`
  - Angela Riddle — `angriddle@gmail.com`
  - Adam Blackburn — `ad.blackburn@gmail.com`
  - Glenn Burton — `glenn.burton@gmail.com`
  - Jeffrey Smith — `jefsmith@gmail.com`
  - Connor Brown — `connorbrow@gmail.com`
  - (Spencer Powell is also listed as `coworker`.)
- Non-coworker relationships observed: friends (`Ashley Moore`, `Brian Ritter`, `Eric Bailey`,
  `Jamie Solomon`, `Richard Riddle`); family/partner (`David Martinez`, `Grant Martinez`,
  `Robert Martinez`, `William Martinez`).

## 4. Relevant API paths (contract for downstream branches)

### Venmo (`app_name='venmo'`)
- `login` → `POST /auth/token` params: `username` (email), `password`; returns `access_token`, `token_type`.
- `show_account` → `GET /account` params: `access_token`.
- `show_social_feed` → `GET /social_feed` params: `access_token` (req), `page_index` (default 0),
  `page_limit` (default 5, 1..20). Returns list of transactions with `transaction_id, amount,
  description, created_at, updated_at, private, like_count, comment_count, sender{name,email},
  receiver{name,email}`.
- `show_transactions` → `GET /transactions` params: `access_token` (req), `query`, `user_email`,
  `min_created_at`, `max_created_at` (YYYY-MM-DD), `min_like_count`, `max_like_count`,
  `min_amount`, `max_amount`, `private`, `direction` in `['sent','received']`, `page_index`,
  `page_limit`, `sort_by` (`+/-created_at|like_count|amount`).
- Other useful endpoints exist: `show_venmo_balance`, `show_notifications`,
  `show_received_payment_requests`, `show_sent_payment_requests`, `search_users`, `search_friends`,
  `show_transaction`, `download_transaction_receipt`, comments/likes endpoints.

### Phone (`app_name='phone'`)
- `login` → `POST /auth/token` params: `username` (phone_number), `password`.
- `get_current_date_and_time` → `GET /date_time` (no params).
- `show_contact_relationships` → relationship vocabulary.
- `search_contacts` → params include `access_token`, `page_index`, `page_limit` (**≤ 20**;
  larger values raise HTTP 422 validation error).

### Supervisor (`app_name='supervisor'`)
- `show_active_task` → `GET /active_task` → returns `{instruction, status, answer}`.
- `show_profile`, `show_addresses`, `show_payment_cards`, `show_account_passwords`, `complete_task`.

### Discovery (`app_name='api_docs'`)
- `show_app_descriptions()`, `show_api_descriptions(app_name=...)`,
  `show_api_doc(app_name=..., api_name=...)`.

## 5. Raw discovery evidence for the dinner (2023-05-17)

### 5a. Active task instruction (`apis.supervisor.show_active_task()`)
> "I went on dinner with my coworkers yesterday at Azure Harbor Bistro. My manager paid for food and
> everyone venmoed them. Everyones' transactions except mine should be on my social feed. My share was
> $38. How much did my manager pay for the others, including me, yesterday?"
> status: null, answer: `<<NOT_GIVEN>>`

### 5b. Social feed entries mentioning Azure Harbor Bistro (created_at 2023-05-17T14:46:15), from
`apis.venmo.show_social_feed(access_token=token, page_limit=20)` — all sender → receiver `Spencer Powell`:
| transaction_id | amount | description | sender (email) |
|---|---|---|---|
| 8216 | 29.0 | Dinner at Azure Harbor Bistro | Jordan Harrison (jo-harr@gmail.com) |
| 8217 | 20.0 | Azure Harbor Bistro | Angela Riddle (angriddle@gmail.com) |
| 8218 | 42.0 | Azure Harbor Bistro | Adam Blackburn (ad.blackburn@gmail.com) |
| 8219 | 44.0 | Food at Azure Harbor Bistro | Jeffrey Smith (jefsmith@gmail.com) |
| 8220 | 23.0 | Food at Azure Harbor Bistro | Connor Brown (connorbrow@gmail.com) |
| 8221 | 31.0 | Dinner at Azure Harbor Bistro | Glenn Burton (glenn.burton@gmail.com) |

(All six receivers are `Spencer Powell` — the manager. IDs 8216–8221 are contiguous.)

### 5c. The user's own transaction (NOT on the social feed), from
`apis.venmo.show_transactions(access_token=token, min_created_at='2023-05-17', max_created_at='2023-05-17')`:
- transaction_id `8222`, amount `38.0`, description `Dinner at Azure Harbor Bistro`,
  created_at `2023-05-17T14:46:15`, sender `Ashlee Martinez` → receiver `Spencer Powell`.
- This matches the stated "$38" share and continues the contiguous ID run 8216–8222.

## 6. Notes, limits, and hand-off

- **Date assumption** is the key parameter: "yesterday" = `2023-05-17`.
- Candidate participants paying the manager `Spencer Powell` on that date: the 6 coworkers above
  (via social feed) **plus** the user `Ashlee Martinez` (her own $38 transaction, absent from the feed).
- This recon deliberately does **not** assert a final computed total; the arithmetic / reconciliation
  is the job of the downstream social-feed branch (Task-2) and Venmo branch (Task-3). Raw amounts are
  recorded above for those branches to consume.
- Only pages 0–2 of the social feed and page 0–2 of contacts were enumerated; feed page 0 covered all
  `2023-05-17` entries, so the dinner evidence is complete within observed pages.
- No mutations were executed at recon time.
