# REPORT.md — Independent cross-check of the Spotify unique-song count

## Question
"How many unique songs are there across my Spotify song library, albums library and all playlists?"

## Final answer
**81 unique songs.**

## What this report is
An *independent re-execution* of the whole collection, in this attempt, against the **live shared
world**, to cross-check `SONG_DATA.md` (task-2). Every figure below comes from fresh, read-only
Spotify/supervisor calls run through `appworld_execute(code)` in this attempt. No mutation was
performed (login + read-only list/get APIs only).

## Account / identity re-verification (read-only)
- `apis.supervisor.show_profile()` →
  `{'first_name': 'Debra', 'last_name': 'Ritter', 'email': 'de_ritt@gmail.com', 'phone_number': '3375602296', 'birthday': '1994-11-30', 'sex': 'female'}`
- `apis.supervisor.show_account_passwords()` includes `{'account_name': 'spotify', 'password': '7s7!cA8'}`.
- `apis.supervisor.show_active_task()` →
  `{'instruction': 'How many unique songs are there across my Spotify song library, albums library and all playlists?', 'status': 'success', 'answer': 81.0}`
- `apis.spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')` → returned an `access_token` (JWT, `token_type='Bearer'`).
- `apis.spotify.show_account(access_token=token)` →
  `{'first_name': 'Debra', 'last_name': 'Ritter', 'email': 'de_ritt@gmail.com', 'registered_at': '2022-07-06T10:52:39', 'last_logged_in': '2022-07-06T10:52:39', 'verified': True, 'is_premium': False}`

Name/email match the supervisor, confirming the correct Spotify account.

## Exact calls that produced the evidence
All list calls used `page_limit=20` (the max), `page_index=0,1,...`, stopping at the first page with
**fewer than `page_limit`** rows (complete-enumeration stop rule).

| # | Call | Pages (index → rows) | Result |
|---|---|---|---|
| 1 | `apis.spotify.show_song_library(access_token=token, page_index=0, page_limit=20)` | `[(0,16)]` | 16 rows < 20 → complete |
| 2 | `apis.spotify.show_album_library(access_token=token, page_index=0, page_limit=20)` | `[(0,8)]` | 8 rows < 20 → complete |
| 3 | `apis.spotify.show_playlist_library(access_token=token, page_index=0, page_limit=20)` | `[(0,5)]` | 5 rows < 20 → complete |
| 4 | `apis.spotify.show_playlist_library(..., is_public=True)` | `[(0,3)]` | ids `[594,595,597]` |
| 5 | `apis.spotify.show_playlist_library(..., is_public=False)` | `[(0,2)]` | ids `[593,596]` |
| 6 | `apis.spotify.show_playlist(playlist_id=593..597, access_token=token)` | — | full `songs` list per playlist |

**No page was skipped and no error/failure dict was returned.** Playlist-completeness cross-check:
`set(public) | set(private) == set(default)` → `True`.

## Deduplication key
**`song_id`** (integer).
- `show_song_library` rows carry `song_id`.
- `show_album_library` rows carry `song_ids`.
- `show_playlist_library` rows carry `song_ids`; `show_playlist` returns `songs:[{id,...}]`.
- For all 5 playlists, ids in `show_playlist(...).songs` **exactly equal** the library row's
  `song_ids` (`match_library_row=True` for 593,594,595,596,597).

Deduplication = set-union on the integer song id.

## Per-source breakdown
| Source | API | rows | unique songs (song_id) |
|---|---|---:|---:|
| Song library | `show_song_library` | 16 | **16** |
| Album library (union of album `song_ids`) | `show_album_library` | 8 albums (32 entries) | **32** |
| All playlists (union of playlist `song_ids`) | `show_playlist_library` + `show_playlist` | 5 playlists (43 entries) | **40** |

Song library ids (16): `[3, 15, 20, 62, 73, 81, 90, 96, 98, 105, 120, 154, 173, 217, 248, 269]`

Album union (32): `[33,34,35,36,44,45,46,47,48,49,50,51,52,53,54,55,56,57,63,64,65,66,70,71,72,73,74,75,76,80,81,82]`

Playlist union (40): `[32,46,57,58,66,74,89,108,113,115,121,125,128,136,147,153,156,160,161,169,173,175,183,197,199,200,210,215,224,236,244,253,256,265,280,299,306,310,319,323]`

## Cross-source overlaps (by song_id)
- song-library ∩ album-library = `[73, 81]` → 2
- song-library ∩ playlists = `[173]` → 1
- album-library ∩ playlists = `[46, 57, 66, 74]` → 4
- all three = `[]` → 0

Set arithmetic: (16 + 32 + 40) − (2 + 1 + 4 − 0) = 88 − 7 = **81**.

## Total union (81 songs, sorted)
`[3,15,20,32,33,34,35,36,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,62,63,64,65,66,70,71,72,73,74,75,76,80,81,82,89,90,96,98,105,108,113,115,120,121,125,128,136,147,153,154,156,160,161,169,173,175,183,197,199,200,210,215,217,224,236,244,248,253,256,265,269,280,299,306,310,319,323]`
Count = **81**.

## Reconciliation vs SONG_DATA.md
Every figure independently reproduced matches SONG_DATA.md exactly: song library 16; album union 32;
playlist union 40; overlaps {2,1,4,0}; total 81; playlist id set `{593,594,595,596,597}`, public
`{594,595,597}`, private `{593,596}`. **No discrepancy found. The number 81 is accepted.**

## Interpretation note (scope check)
The question names "song library, albums library and all playlists", so the library endpoints
(`show_song_library`, `show_album_library`, `show_playlist_library`) were used. The separate "liked"
collections were inspected only to bound the interpretation:
- `show_liked_songs` → 26 rows (page 0=20, page 1=6; 26 unique) — distinct from the 16-song library.
- `show_liked_albums` → 13 rows — distinct from the 8-album library.
- `show_liked_playlists` → ids `[593, 112, 298, 49, 596]`, which includes playlists owned by others;
  these are *liked* playlists, not the user's own playlist library, and were **not** counted.
If "all playlists" were meant to include liked/foreign playlists the count would differ; flagged, not adopted.

## Uncompleted / unverified items
- None outstanding for the three named collections: each was fully re-enumerated with no unfetched page and no error.
- Scope limitation: "all playlists" is interpreted as the user's own playlist library
  (`show_playlist_library`, owner = Debra Ritter). Playlists owned by other users were not counted as "mine".

## Knowledge
`knowledge_list()` returned 0 items in this attempt, so **no knowledge id is cited as a currently-valid
fact** for this computation; every figure above is derived solely from live read-only API output in this episode.

## Live-world completion
The shared world genuinely satisfies the user goal: the three collections enumerate to 16 + 32 + 40
unique-by-source songs with the stated overlaps, yielding **81 unique songs**.
`apis.supervisor.show_active_task()` currently reports `status='success', answer=81.0`.
