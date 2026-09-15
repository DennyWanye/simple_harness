# RECON.md — API / Account Reconnaissance (Task A)

Task id: `mission-444460f678a595df:task-1`
Attempt id: `mission-444460f678a595df:task-1:attempt-1`

Scope: discover the relevant AppWorld apps and public APIs, resolve the simulated
user's Spotify account, and confirm the album-library read interface and its
required parameters. **No playlist was created and no application state was
modified in this Task** (see "State-change statement" at the end).

All names/values below come from real `appworld_execute(...)` calls made in this
Task; each item is annotated with the call that produced it.

---

## 1. Apps discovered

Call: `apis.api_docs.show_app_descriptions()` → returned a JSON list of 11 apps.

| app `name` | `description` (verbatim from the return) |
|---|---|
| `spotify` | `A music streaming app to stream songs and manage song, album and playlist libraries.` |
| `supervisor` | `An app to access supervisor's personal information, account credentials, addresses, payment cards, and manage the assigned task.` |
| `api_docs` | `An app to search and explore API documentation.` |
| `amazon` | `An online shopping app to buy products and manage orders, returns, etc.` |
| `phone` | `An app to find and manage contact information for friends, family members, etc., send and receive messages, and manage alarms.` |
| `file_system` | `A file system app to create and manage files and folders.` |
| `venmo` | `A social payment app to send, receive and request money to and from others.` |
| `gmail` | `An email app to draft, send, receive, and manage emails.` |
| `splitwise` | `A bill splitting app to track and split expenses with people.` |
| `simple_note` | `A note-taking app to create and manage notes` |
| `todoist` | `A task management app to manage todo lists and collaborate on them with others.` |

**Music app = `spotify`** (the user's "music library" is the Spotify app).

Also read (same call returns): nothing else needed.

## 2. API discovery calls (return status = succeeded for all)

- `apis.api_docs.show_api_descriptions(app_name='spotify')` → full list of Spotify APIs (88 names), including `show_album_library`, `show_album`, `show_song`, `show_song_privates`, `create_playlist`, `add_song_to_playlist`, `show_playlist`, `show_playlist_library`, `login`, `show_account`.
- `apis.api_docs.show_api_descriptions(app_name='supervisor')` → `show_active_task`, `complete_task`, `show_profile`, `show_addresses`, `show_payment_cards`, `show_account_passwords`.
- `apis.api_docs.show_api_descriptions(app_name='api_docs')` → `show_app_descriptions`, `show_api_descriptions`, `show_api_doc`, `search_api_docs`.

## 3. Simulated user identity (from supervisor app)

Call: `apis.supervisor.show_profile()` → `outcome: succeeded`, returned:

```json
{
 "first_name": "Debra",
 "last_name": "Ritter",
 "email": "de_ritt@gmail.com",
 "phone_number": "3375602296",
 "birthday": "1994-11-30",
 "sex": "female"
}
```

Call: `apis.supervisor.show_account_passwords()` → `outcome: succeeded`, returned a list of account credentials. The Spotify entry is:

```json
{
 "account_name": "spotify",
 "password": "7s7!cA8"
}
```

Call: `apis.supervisor.show_addresses()` → `outcome: succeeded` (Home: 5309 Rios Cliff Suite 287, Seattle, Washington, 84348; Work: 162 Smith Lake Suite 664, Seattle, Washington, 18461). (Not needed for the music task; recorded for completeness.)

### User's music (Spotify) account identifier
- **Account name key:** `spotify` (from `show_account_passwords`).
- **Login username / account identifier:** `de_ritt@gmail.com` (the supervisor's email; `spotify.login`'s `username` parameter is described as "Your account email").
- **Password:** `7s7!cA8` (from `show_account_passwords`).

## 4. Album library read interface (the user's album library entrance)

Call: `apis.api_docs.show_api_doc(app_name='spotify', api_name='show_album_library')` → `outcome: succeeded`, returned:

- Full API name: **`spotify.show_album_library`**
- `path`: `/library/albums`
- `method`: `GET`
- `description`: `Get a list of albums in the user's album library.`
- Parameters:

| `name` | `type` | `required` | `default` | `constraints` |
|---|---|---|---|---|
| `access_token` | string | **true** | null | `Access token obtained from spotify app login.` |
| `page_index` | integer | false | 0 | `value >= 0.0` |
| `page_limit` | integer | false | 5 | `value >= 1.0, <= 20.0` |

- Success response schema: a list of objects with fields `album_id`, `title`, `genre`, `artists` (list of `{id, name}`), `rating`, `like_count`, `review_count`, `release_date`, `song_ids` (list of int), `added_at`.
- Only **one required parameter: `access_token`**.

**Required parameter = `access_token`**, which is obtained from `spotify.login`.

### Token acquisition (needed before reading the album library)
Call: `apis.api_docs.show_api_doc(app_name='spotify', api_name='login')`:
- Full API name: **`spotify.login`**, `path`: `/auth/token`, `method`: `POST`.
- Required parameters: `username` ("Your account email") and `password`.
- Success response: `{ "access_token": "string", "token_type": "string" }`.

So the required flow is: `spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')` → use returned `access_token` in `spotify.show_album_library(access_token=...)`.

## 5. Supporting read APIs for the album → song play-count analysis (Task B)

All from `apis.api_docs.show_api_doc(...)` → `outcome: succeeded` in this Task:

- **`spotify.show_album`** — `path` `/albums/{album_id}`, `method` GET. Required: `album_id` (integer). Success includes `songs`: list of `{id, title, artist_ids}`.
- **`spotify.show_song`** — `path` `/songs/{song_id}`, `method` GET. Required: `song_id` (integer). Success includes `song_id`, `title`, `album_id`, `album_title`, `duration`, `artists` (`[{id, name}]`), `release_date`, `genre`, **`play_count`**, `rating`, `like_count`, `review_count`, `shareable_link`.
- **`spotify.show_song_privates`** — `path` `/songs/{song_id}/privates`, GET. Required: `song_id`, `access_token`. Success: `{liked, reviewed, in_song_library, downloaded}`.
- **`spotify.show_album_privates`** — `path` `/albums/{album_id}/privates`, GET. Required: `album_id`, `access_token`. Success: `{liked, reviewed, in_album_library}`.

## 6. Supporting write APIs for the playlist creation (Task C)

From `apis.api_docs.show_api_doc(...)` → `outcome: succeeded` in this Task (documented here, **NOT called**):

- **`spotify.create_playlist`** — `path` `/playlists`, `method` POST. Required: `title` (string, length >= 1), `access_token`. Optional: `is_public` (boolean, default false). Success: `{message, playlist_id}`.
- **`spotify.add_song_to_playlist`** — `path` `/playlists/{playlist_id}/songs/{song_id}`, `method` POST. Required: `playlist_id` (int), `song_id` (int), `access_token`. Success: `{message}`.
- **`spotify.show_playlist`** — `path` `/playlists/{playlist_id}`, GET. Required: `playlist_id`, `access_token`. Success includes `playlist_id`, `title`, `is_public`, `owner {name, email}`, `created_at`, `songs` (`[{id, title, artist_ids}]`).
- **`spotify.show_playlist_library`** — `path` `/library/playlists`, GET. Required: `access_token`. Optional: `is_public`, `page_index` (default 0), `page_limit` (default 5, 1–20).

## 7. Return statuses observed

- `apis.api_docs.show_app_descriptions()` → `outcome: succeeded`
- `apis.api_docs.show_api_descriptions(app_name=...)` (spotify/supervisor/api_docs) → `outcome: succeeded`
- `apis.api_docs.show_api_doc(app_name=..., api_name=...)` for: `spotify.show_account`, `spotify.show_album_library`, `spotify.show_album`, `spotify.show_song`, `spotify.show_song_privates`, `spotify.show_album_privates`, `spotify.login`, `spotify.show_profile`, `spotify.create_playlist`, `spotify.add_song_to_playlist`, `spotify.show_playlist`, `spotify.show_playlist_library`, `spotify.show_liked_albums`, `supervisor.show_profile`, `supervisor.show_account_passwords` → all `outcome: succeeded`
- `apis.supervisor.show_profile()` → `outcome: succeeded`
- `apis.supervisor.show_account_passwords()` → `outcome: succeeded`
- `apis.supervisor.show_addresses()` → `outcome: succeeded`

No call in this Task returned `error_code` / a failure outcome.

## 8. State-change statement

This Task performed **only read-only operations**: `api_docs` documentation reads and `supervisor` profile/credential/address reads.

- **No playlist was created** (`spotify.create_playlist` was never called). ✅
- **No application state was modified** — no `spotify.login` (no token issued / no session), no `add_song_to_playlist`, no `add_album_to_library`, no `like_*`, no `review_*`, no `update_*`. ✅
- The album library was **not** fetched in this Task; only its API contract (parameters) was read from the docs. Downstream Tasks (B/C) will login and act.
