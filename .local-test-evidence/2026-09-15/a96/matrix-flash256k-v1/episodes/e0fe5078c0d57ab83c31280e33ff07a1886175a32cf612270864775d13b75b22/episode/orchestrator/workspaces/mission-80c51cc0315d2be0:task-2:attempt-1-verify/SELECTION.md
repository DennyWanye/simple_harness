# SELECTION.md — Lily Moon songs with play count > 980 (Task 2)

Scope: Identify every song by the artist **Lily Moon** whose play count is strictly
greater than 980 ("played over 980 times"). This file records only data produced by real
AppWorld Spotify API calls in the shared shell. No queue mutation was performed here.

## 1. Method / API calls used

- `apis.spotify.login(username="susanmiller@gmail.com", password="%CCvl8v")` -> access_token (Bearer).
- `apis.spotify.search_artists(query="Lily Moon")` -> resolved the target artist.
- `apis.spotify.search_songs(artist_id=34, page_limit=20)` -> full Lily Moon song list (11 items; 11 < 20 so no further pages). `page_index=1` returned an empty list, confirming completeness.
- `apis.spotify.search_songs(artist_id=34, min_play_count=981, page_limit=20)` -> server-side filter confirmation.
- `apis.spotify.show_song(song_id=311)` -> per-song detail confirmation.

## 2. Artist identity

- artist_id: **34**
- name: **Lily Moon**
- genre: rock
- follower_count: 25
- created_at: 2018-12-04T01:37:41

## 3. Threshold

"Played over 980 times" is interpreted as **play_count > 980** (strictly greater than 980;
a song with exactly 980 would not qualify). Only one Lily Moon song satisfies this.

## 4. Selection — songs to add to the Spotify queue

| # | song_id | title | artist | artist_id | exact play_count | evidence |
|---|---|---|---|---|---|---|
| 1 | 311 | Infinite Dreams | Lily Moon | 34 | 990 | search_songs(artist_id=34) and show_song(311) both report play_count=990 |

### Specifying data for song 311 (from `show_song(song_id=311)`)

- song_id: 311
- title: Infinite Dreams
- album_id: null
- duration: 258 (seconds)
- artists: [{"id": 34, "name": "Lily Moon"}]
- release_date: 2020-07-26T22:25:50
- genre: rock
- play_count: 990
- rating: 0.0
- like_count: 4
- review_count: 0
- shareable_link: https://spotify.com/songs/311

## 5. Full Lily Moon song set observed (for completeness / audit)

`apis.spotify.search_songs(artist_id=34, page_limit=20)` returned all 11 songs:

| song_id | title | artists | play_count | > 980? |
|---|---|---|---|---|
| 67 | The Echoes of a Silent Heart | Lily Moon, Zoey James | 916 | no |
| 68 | Lost in the Wilderness of Love | Lily Moon, Zoey James | 156 | no |
| 69 | Whispers of a Forgotten Love | Lily Moon, Zoey James | 645 | no |
| 74 | On the Border of Reality | Lily Moon, Zoey James | 846 | no |
| 75 | Walking Through the Valley of Shadows | Lily Moon, Zoey James | 806 | no |
| 76 | Whispers of the Heart | Lily Moon, Zoey James | 330 | no |
| 309 | Final Act | Lily Moon | 520 | no |
| 310 | Harmony of the Distant Stars | Lily Moon | 715 | no |
| 311 | Infinite Dreams | Lily Moon | 990 | **yes** |
| 312 | Eternal Tears | Lily Moon | 562 | no |
| 313 | Mystical Dreamscape | Lily Moon | 864 | no |

Server-side filter confirmation: `search_songs(artist_id=34, min_play_count=981, page_limit=20)`
returned exactly `[(311, 'Infinite Dreams', 990)]`.

## 6. Result

Exactly **one** song qualifies: **song_id 311, "Infinite Dreams" by Lily Moon (artist_id 34),
play_count 990**. This is the song set to be added to the Spotify player queue by Task 3.

## 7. Limitations / notes

- The play-count threshold 980 is from the user's instruction; "over 980" was treated as strict >.
- Songs 67–76 are collaborations where Lily Moon appears alongside Zoey James; all are counted
  as "songs from Lily Moon" and all fall below the threshold, so this does not change the result.
- Data source is the shared AppWorld Spotify app state at query time (world version may change
  if other agents mutate state); these were the observed values during this task.
