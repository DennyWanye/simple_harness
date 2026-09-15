# DISCOVERY.md — API contract & recipient set for "Please get on venmo." phone message

Task: mission-9f885599628a4975:task-1 (discovery only — NO message is sent by this task)

## 1. App/API discovery (exact names)

`apis.api_docs.show_app_descriptions()` returned these apps:
api_docs, supervisor, amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist.

The **phone** app is the phone/SMS app. Relevant APIs (from
`apis.api_docs.show_api_descriptions(app_name='phone')`):

| API | Purpose |
|---|---|
| `phone.login` | Obtain access token (username = account phone number) |
| `phone.show_account` | Private account info (requires access_token) |
| `phone.show_contact_relationships` | List relationship labels in the address book |
| `phone.search_contacts` | Read contacts (address book) |
| `phone.send_text_message` | **Send an SMS/text message** |
| `phone.show_text_message_window` | Read messages with a contact |

### 1a. `phone.login`
- POST `/auth/token`
- Params: `username` (string, required, = account phone_number), `password` (string, required)
- Success: `{access_token, token_type}`
- Called: `apis.phone.login(username="4226809725", password=")w-S21u")` -> succeeded, token returned.

### 1b. `phone.show_contact_relationships`
- GET `/contact_relationships`
- Params: `access_token` (string, required)
- Success: list[string]
- Observed value: `['coworker', 'father', 'friend', 'husband', 'manager', 'mother', 'parent', 'partner', 'sibling', 'sister']`

### 1c. `phone.search_contacts`
- GET `/contacts`
- Params: `access_token` (required); `query` (string, optional, default ""); `relationship` (string, optional, default null); `page_index` (int, optional, default 0, >=0); `page_limit` (int, optional, default 5, 1..20)
- Success: list of `{contact_id, first_name, last_name, email, phone_number, relationships[], birthday, home_address, work_address, created_at}`
- Called with page_index 0..9, page_limit 20 -> 21 contacts total (relationship filter is available but was not needed).

### 1d. `phone.send_text_message`  (used by the follow-up send task, NOT called here)
- POST `/messages/text/{phone_number}`
- Params: `phone_number` (string, required — recipient's phone number), `message` (string, required, length>=1), `access_token` (string, required)
- Success: `{message, text_message_id}`
- The message text to send is exactly: `Please get on venmo.`

## 2. Simulated user (supervisor) accounts

`apis.supervisor.show_profile()` ->
`{'first_name': 'Erika', 'last_name': 'Blackburn', 'email': 'erikabail@gmail.com', 'phone_number': '4226809725', 'birthday': '1989-06-24', 'sex': 'female'}`

App account passwords `apis.supervisor.show_account_passwords()` ->
`[{amazon:'j7A61ld'},{file_system:'p**UPfc'},{gmail:'M-)NZ+2'},{phone:')w-S21u'},{simple_note:'M2&R$kY'},{splitwise:'--cz&%1'},{spotify:'XZ!zW*T'},{todoist:'3WI5xYn'},{venmo:'Ez$26gJ'}]`

- Phone account (confirmed via `apis.phone.show_account`): Erika Blackburn, phone_number `4226809725`.
- Venmo login uses the email: `apis.venmo.login(username="erikabail@gmail.com", password="Ez$26gJ")` -> succeeded, token returned.

`apis.supervisor.show_active_task()` ->
`{'instruction': 'Send the following phone message to my parents and siblings, who do not have a venmo account, "Please get on venmo."', 'status': None, 'answer': '<<NOT_GIVEN>>'}`

## 3. Venmo status-check API

- `venmo.search_users` (GET `/users`): params `access_token` (required), `query` (optional), `page_index`, `page_limit`. Fuzzy; used as a first pass.
- `venmo.show_profile` (GET `/profile`): params `access_token` (required), `email` (optional, must be an email). Definitive check: returns the profile if the Venmo account exists, otherwise fails with `422 {"message":"Account for this email does not exist."}`.

## 4. Parents and siblings (contact identifiers & phones)

Filtering the 21 contacts by relationship containing `parent` or `sibling`:

| Role | Name | contact_id | phone_number | email |
|---|---|---|---|---|
| Parent (father) | Eric Bailey | 1668 | 9272583586 | eric.bailey@gmail.com |
| Parent (mother) | Kiara Bailey | 1669 | 8909828624 | kia_bailey@gmail.com |
| Sibling (sister) | Melissa Bailey | 1671 | 3383946795 | mel.bailey@gmail.com |
| Sibling (sister) | Sherry Smith | 1672 | 2114992604 | she_bailey@gmail.com |

(No other contacts carry a `parent`/`sibling` relationship.)

## 5. Venmo-account status per parent/sibling (raw observations)

Definitive check via `apis.venmo.show_profile(access_token=VT, email=...)`:

- Eric Bailey — `eric.bailey@gmail.com` -> **NO Venmo account**
  Raw: `422 {"message":"Account for this email does not exist."}` (this raised an exception; error body captured).
  Corroborating `venmo.search_users(query="Eric Bailey")` returned Kiara Bailey, Melissa Bailey, Stephen Mccoy, Laura Mccoy, Chris Mccoy — no "Eric Bailey".
- Kiara Bailey — `kia_bailey@gmail.com` -> **HAS Venmo account**
  Raw: `{'first_name': 'Kiara', 'last_name': 'Bailey', 'email': 'kia_bailey@gmail.com', 'registered_at': '2022-07-03T16:29:55', 'friends_since': None}`
- Melissa Bailey — `mel.bailey@gmail.com` -> **HAS Venmo account**
  Raw: `{'first_name': 'Melissa', 'last_name': 'Bailey', 'email': 'mel.bailey@gmail.com', 'registered_at': '2022-03-13T13:34:32', 'friends_since': None}`
- Sherry Smith — `she_bailey@gmail.com` -> **NO Venmo account**
  Raw: `422 {"message":"Account for this email does not exist."}` (error body captured).
  Corroborating `venmo.search_users(query="Sherry Smith")` returned Marcus Smith, Cody Smith, Mason Smith, Norman Smith, Jeffrey Smith — no "Sherry Smith".

## 6. Derived recipient set for the message (parents & siblings WITHOUT Venmo)

Per the instruction "parents and siblings, who do not have a venmo account", the recipients are:

1. Eric Bailey — phone **9272583586** (father, no Venmo)
2. Sherry Smith — phone **2114992604** (sister, no Venmo)

Kiara Bailey and Melissa Bailey are EXCLUDED (they already have a Venmo account).

## 7. Status

Discovery complete. No text message was sent in this task (sending belongs to the follow-up send task).
