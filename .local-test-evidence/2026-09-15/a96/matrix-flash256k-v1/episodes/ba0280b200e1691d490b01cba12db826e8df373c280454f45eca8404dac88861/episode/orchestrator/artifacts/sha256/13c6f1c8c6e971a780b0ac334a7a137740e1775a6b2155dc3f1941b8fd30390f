# Candidate Songs Report — Lily Moon songs played over 980 times

Task: `mission-c3859a9c84d7a5c9:task-2` (read-only candidate discovery — NO queue mutations performed)
Goal: Query Lily Moon's songs and their play counts in the simulated Spotify world; identify every song whose play count is **greater than 980**. Do not enqueue anything yet.

All observations below were produced by running `appworld_execute(code)` against the shared AppWorld shell. Strings in quotes are verbatim API outputs.

## 1. Resolution of "Lily Moon"

`apis.spotify.search_artists(query='Lily Moon')` returned (top match):

```json
{"artist_id": 34, "name": "Lily Moon", "genre": "rock", "follower_count": 25, "created_at": "2018-12-04T01:37:41"}
```

Lily Moon = **artist_id 34**.

## 2. Source API calls used

- `apis.spotify.search_songs(artist_id=34, page_limit=20)` — full listing of songs where Lily Moon is an artist.
- `apis.spotify.search_songs(artist_id=34, page_index=0, page_limit=20)` and `page_index=1` — pagination check (page 0 = 11 rows, page 1 = 0 rows → listing is complete).
- `apis.spotify.search_songs(artist_id=34, min_play_count=981, page_limit=20)` — filter `play_count > 980`.
- `apis.spotify.search_songs(query='Lily Moon', page_limit=20)` — cross-check listing (contains the same 11 Lily Moon songs).
- `apis.spotify.show_song(song_id=<id>)` — per-song confirmation of `play_count`.

`search_songs` endpoint: `GET /songs`; parameters include `query`, `artist_id`, `min_play_count`, `max_play_count`, `sort_by`, `page_index`, `page_limit`. Each result includes `song_id`, `title`, `artists[]`, `play_count`.

## 3. Complete Lily Moon song listing with play counts

From `apis.spotify.search_songs(artist_id=34, page_limit=20)` (11 songs total):

| song_id | title | play_count | artists |
|--------:|-------|-----------:|---------|
| 67 | The Echoes of a Silent Heart | 916 | Lily Moon, Zoey James |
| 68 | Lost in the Wilderness of Love | 156 | Lily Moon, Zoey James |
| 69 | Whispers of a Forgotten Love | 645 | Lily Moon, Zoey James |
| 74 | On the Border of Reality | 846 | Lily Moon, Zoey James |
| 75 | Walking Through the Valley of Shadows | 806 | Lily Moon, Zoey James |
| 76 | Whispers of the Heart | 330 | Lily Moon, Zoey James |
| 309 | Final Act | 520 | Lily Moon |
| 310 | Harmony of the Distant Stars | 715 | Lily Moon |
| 311 | Infinite Dreams | 990 | Lily Moon |
| 312 | Eternal Tears | 562 | Lily Moon |
| 313 | Mystical Dreamscape | 864 | Lily Moon |

Per-song `show_song` confirmations matched the listing (e.g. show_song 311 → "Infinite Dreams" play_count 990; show_song 67 → "The Echoes of a Silent Heart" play_count 916; show_song 74 → "On the Border of Reality" play_count 846).

## 4. Qualification rule

"played over 980 times" ⇒ `play_count > 980` (strictly greater; 980 itself does not qualify). The equivalent Spotify filter is `min_play_count = 981`.

## 5. Qualifying list (`play_count > 980`)

`apis.spotify.search_songs(artist_id=34, min_play_count=981, page_limit=20)` returned exactly **one** song:

```json
{
  "song_id": 311,
  "title": "Infinite Dreams",
  "album_id": null,
  "duration": 258,
  "artists": [{"id": 34, "name": "Lily Moon"}],
  "release_date": "2020-07-26T22:25:50",
  "genre": "rock",
  "play_count": 990,
  "rating": 0.0,
  "like_count": 4,
  "review_count": 0,
  "shareable_link": "https://spotify.com/songs/311"
}
```

**Qualifying songs:**

1. `song_id = 311` — **"Infinite Dreams"** — `play_count = 990` — artist: Lily Moon (id 34)

No other Lily Moon song exceeds 980 (highest non-qualifying: song 67 "The Echoes of a Silent Heart" at 916).

## 6. Queue mutation status

No song was added to, removed from, or otherwise modified in the Spotify player queue during this task. This report is read-only candidate discovery; enqueueing is deferred to a later task.

## 7. Caveats

- `play_count` is read at query time; a later re-read may differ. This snapshot was taken during this task.
- The `artist_id=34` filter also matched collaborative tracks where Lily Moon is a co-artist (songs 67–76, shared with Zoey James). Those were included in the scan; none qualify.
- Listing completeness was verified via pagination (`page_index` 0 → 11 rows; `page_index` 1 → 0 rows).
