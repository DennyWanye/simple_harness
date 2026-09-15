# Album Song Selection — Most-Played Song per Album (Task-2)

Scope: for every album in the user's Spotify **album library**, determine the single
**most-played song**. Evidence: live AppWorld Spotify public API calls executed in the shared
shell (`apis.<app>.<api>(...)`). No hidden answers or evaluator were accessed.

## Method

1. Logged in: `spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')` -> access_token (JWT sub `spotify+de_ritt@gmail.com`).
2. Read album library: `spotify.show_album_library(access_token=tok, page_index=0, page_limit=20)`
   returned 8 albums; `page_index=1` returned `[]`, establishing the complete library of 8 albums.
3. For each album: `spotify.show_album(album_id=aid)` to read its track list; the track ids
   matched the library `song_ids` exactly (verified below).
4. For each song: `spotify.show_song(song_id=sid)` to read `play_count`. The returned
   `album_id` matched the parent album for every song (no cross-album leakage).
5. "Most-played" = the song with the maximum `play_count` within each album.

## Selected most-played song per album (8 albums)

| album_id | album title | selected song_id | selected song title | play_count | runner-up play_count |
|---|---|---|---|---|---|
| 7  | Vibrant Visions    | 36 | A Whisper in the Midnight Air | 974 | 809 |
| 9  | Mystical Crescendo | 45 | Eclipsed                       | 713 | 671 |
| 10 | Dreamscape Delights| 51 | The Silence Between Us         | 991 | 841 |
| 11 | Synaptic Serenity  | 54 | Heartstrings Symphony          | 845 | 437 |
| 13 | Starlight Serenades| 66 | Phantom Pain                   | 975 | 818 |
| 15 | Whispers in the Wind | 73 | When Silence Becomes Deafening | 482 | 313 |
| 16 | Electric Dreamscape| 74 | On the Border of Reality       | 846 | 806 |
| 18 | Echoes of Eternity | 80 | Wandering the Streets Alone    | 863 | 750 |

**Selected song ids (in album-library order):** 36, 45, 51, 54, 66, 73, 74, 80

### Ties
- No album has a tie for the maximum play_count: each album's winner is unique.
- Non-maximum tie (does not affect selection): album 10 "Dreamscape Delights" has songs 52
  ("Bleeding Sun") and 53 ("Unearthed Secrets") both at play_count 648. The album maximum
  (song 51 = 991) is clear of this tie.

### Unsupported / missing data
- None. Every album has >=1 track, and every track returned a numeric `play_count`.
- `show_album_library` itself does not include play counts; play counts were obtained per-song
  from `show_song.play_count`.

## Full per-album evidence (all tracks and play counts)

### Album 7 — "Vibrant Visions" (added 2022-08-14T01:47:52); tracks [33,34,35,36] == library [33,34,35,36]
- 33 "Unveiled" — 809
- 34 "Under the Gaze of a Watchful Moon" — 357
- 35 "Dancing in the Rain of Tears" — 135
- **36 "A Whisper in the Midnight Air" — 974  <- max**

### Album 9 — "Mystical Crescendo" (added 2022-11-13T01:13:07); tracks [44,45,46] == library [44,45,46]
- 44 "The Illusion of Eternal Spring" — 671
- **45 "Eclipsed" — 713  <- max**
- 46 "Symphony of the Twilight Forest" — 134

### Album 10 — "Dreamscape Delights" (added 2023-03-26T01:43:50); tracks [47,48,49,50,51,52,53] == library [47,48,49,50,51,52,53]
- 47 "Time's Hold" — 279
- 48 "Cherry Blossom Tears" — 314
- 49 "Whispers of the Enchanted Forest" — 841
- 50 "Lonely Skies" — 316
- **51 "The Silence Between Us" — 991  <- max**
- 52 "Bleeding Sun" — 648
- 53 "Unearthed Secrets" — 648

### Album 11 — "Synaptic Serenity" (added 2023-01-28T03:45:46); tracks [54,55,56,57] == library [54,55,56,57]
- **54 "Heartstrings Symphony" — 845  <- max**
- 55 "Tangled Lies" — 437
- 56 "Distant Love" — 334
- 57 "Silver Lining" — 421

### Album 13 — "Starlight Serenades" (added 2023-04-20T19:05:42); tracks [63,64,65,66] == library [63,64,65,66]
- 63 "Journey Through the Unknown" — 582
- 64 "Caught in a Web of Lies" — 468
- 65 "Eternal Solitude" — 818
- **66 "Phantom Pain" — 975  <- max**

### Album 15 — "Whispers in the Wind" (added 2023-05-16T00:39:25); tracks [70,71,72,73] == library [70,71,72,73]
- 70 "Serenade of the Forgotten Stars" — 201
- 71 "Eternal Fade" — 313
- 72 "Wandering Through Time's Embrace" — 151
- **73 "When Silence Becomes Deafening" — 482  <- max**

### Album 16 — "Electric Dreamscape" (added 2023-01-17T09:02:24); tracks [74,75,76] == library [74,75,76]
- **74 "On the Border of Reality" — 846  <- max**
- 75 "Walking Through the Valley of Shadows" — 806
- 76 "Whispers of the Heart" — 330

### Album 18 — "Echoes of Eternity" (added 2023-04-17T06:12:04); tracks [80,81,82] == library [80,81,82]
- **80 "Wandering the Streets Alone" — 863  <- max**
- 81 "Echoes of the Whispering Wind" — 645
- 82 "Lost in the Twilight of Hope" — 750

## API calls used (all succeeded unless noted)
- `spotify.login(username, password)`
- `spotify.show_album_library(access_token, page_index, page_limit)`
- `spotify.show_album(album_id)`
- `spotify.show_song(song_id)`

## Limitations / notes for downstream Task-3
- This task performed reads only; **no playlist was created and no songs were added** (Task-3 scope).
- Selection is based solely on the public `show_song.play_count` field. No alternative per-user
  playback-history API was used to cross-check play counts.
- The playlist to build must contain exactly the 8 selected song ids: 36, 45, 51, 54, 66, 73, 74, 80.
