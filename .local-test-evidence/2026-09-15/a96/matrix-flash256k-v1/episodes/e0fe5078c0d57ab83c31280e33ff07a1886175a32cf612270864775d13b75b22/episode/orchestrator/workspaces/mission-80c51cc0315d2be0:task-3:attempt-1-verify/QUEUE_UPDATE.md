# QUEUE_UPDATE.md — Spotify queue update (Task 3)

Scope: Add every song identified in `SELECTION.md` (Lily Moon songs with play count
over 980) to the user's Spotify player queue. Inspect the current queue first to avoid
duplicates, perform only the missing mutation(s), then verify the queue contains the
selected track. All observations below come from real AppWorld Spotify API calls in the
shared shell.

## 1. Selected songs (input from SELECTION.md)

`SELECTION.md` identifies exactly **one** qualifying song:

| song_id | title | artist | artist_id | play_count |
|---|---|---|---|---|
| 311 | Infinite Dreams | Lily Moon | 34 | 990 |

Threshold: "played over 980 times" interpreted as `play_count > 980` (strict).

## 2. Pre-mutation queue inspection (duplicate avoidance)

- Login: `apis.spotify.login(username="susanmiller@gmail.com", password="%CCvl8v")`
  -> succeeded, returned a Bearer `access_token`.
- Queue inspection: `apis.spotify.show_song_queue(access_token=tok)`
  -> returned **9** entries (positions 0..8):

| position | song_id | title | artists |
|---|---|---|---|
| 0 | 102 | Autumn's Lament | Marigold Muse |
| 1 | 108 | Cold Embrace | Ava Morgan |
| 2 | 214 | The Sweet Pain of Reminiscence | Oceanic Odyssey |
| 3 | 202 | Summer's End | Ethan Wallace |
| 4 | 114 | When All Hope Seems Lost | Seraphina Dawn |
| 5 | 10 | The Curse of Loving You | Lucas Grey |
| 6 | 287 | Painted Skies | Hazel Winter |
| 7 | 285 | Wading Through the Ashes of Love | Marcus Lane |
| 8 | 38 | Destiny's Game | Aria Sterling |

- Target song_id **311** was **NOT** present before the mutation, so a single addition
  was required and no duplicate existed.

I also re-verified the Lily Moon song set at this time with
`apis.spotify.search_songs(artist_id=34, page_limit=20)`; it returned the same 11 songs
with the same play counts, and `apis.spotify.show_song(song_id=311)` confirmed
play_count=990, title "Infinite Dreams", artist Lily Moon (id 34). So the selection is
still valid.

## 3. Mutation performed

- API doc checked: `apis.api_docs.show_api_doc(app_name="spotify", api_name="add_to_queue")`.
  Parameters: `access_token` (required), and at most one of `song_id` / `album_id` /
  `playlist_id`. Path `POST /music_player/song_queue`.
- Call: `apis.spotify.add_to_queue(access_token=tok, song_id=311)`
- Result: `{'message': 'Song added to the queue.'}`

Exactly **one** mutation was performed (one song). No further mutations were needed.

## 4. Post-mutation queue verification

`apis.spotify.show_song_queue(access_token=tok)` returned **10** entries (positions 0..9):

| position | song_id | title | artists |
|---|---|---|---|
| 0 | 102 | Autumn's Lament | Marigold Muse |
| 1 | 108 | Cold Embrace | Ava Morgan |
| 2 | 214 | The Sweet Pain of Reminiscence | Oceanic Odyssey |
| 3 | 202 | Summer's End | Ethan Wallace |
| 4 | 114 | When All Hope Seems Lost | Seraphina Dawn |
| 5 | 10 | The Curse of Loving You | Lucas Grey |
| 6 | 287 | Painted Skies | Hazel Winter |
| 7 | 285 | Wading Through the Ashes of Love | Marcus Lane |
| 8 | 38 | Destiny's Game | Aria Sterling |
| **9** | **311** | **Infinite Dreams** | **Lily Moon** |

Queue id list: `[102, 108, 214, 202, 114, 10, 287, 285, 38, 311]`.
- `COUNT_311 = 1` (no duplicates)
- `HAS_311 = True`

The selected track (`song_id 311`) is now present exactly once in the queue.

## 5. Summary of actions

1. `apis.spotify.login(...)` -> access_token.
2. `apis.spotify.show_song_queue(access_token)` -> 9 entries, no 311.
3. `apis.spotify.search_songs(artist_id=34, page_limit=20)` + `apis.spotify.show_song(song_id=311)` -> re-confirmed 311 "Infinite Dreams" play_count 990.
4. `apis.api_docs.show_api_doc("spotify","add_to_queue")` -> confirmed parameters.
5. `apis.spotify.add_to_queue(access_token, song_id=311)` -> "Song added to the queue."
6. `apis.spotify.show_song_queue(access_token)` -> 10 entries, contains 311 exactly once.

## 6. Result

All songs selected in `SELECTION.md` (exactly one: song_id 311, "Infinite Dreams" by
Lily Moon, play_count 990 > 980) have been added to the user's Spotify player queue, and
the queue was verified to contain the selected track exactly once.

## 7. Limitations / notes

- Only one song qualified, so exactly one queue addition was needed.
- Queue state reflects the shared AppWorld Spotify app at mutation time; other agents
  could mutate it afterwards.
- "Over 980" was treated as strictly greater than 980.
