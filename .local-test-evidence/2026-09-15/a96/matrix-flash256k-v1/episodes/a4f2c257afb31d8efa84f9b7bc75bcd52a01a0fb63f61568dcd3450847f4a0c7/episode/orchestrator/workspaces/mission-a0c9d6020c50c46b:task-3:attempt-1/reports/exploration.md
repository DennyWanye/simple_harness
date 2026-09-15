# Reconnaissance Report — Lily Moon songs with play_count > 980

Task: `mission-a0c9d6020c50c46b:task-1` (work, read-only reconnaissance).
Goal: 查明 API 合同、当前用户身份、Lily Moon 全部歌曲的播放次数，并给出唯一候选清单，供下游写入与验收使用。
Scope note: 本任务**只做只读侦察**，未调用任何队列写操作（未调用 `add_to_queue` / `play_music` / `clear_song_queue` 等）。所有数值均来自实际 API 返回，无推测值。

---

## 1. API 合同（公开接口、必填参数、返回结构）

### 1.1 API 文档发现接口（app: `api_docs`）
| API | 说明 | 参数 |
|---|---|---|
| `show_app_descriptions()` | 列出所有应用 | 无 |
| `show_api_descriptions(app_name='...')` | 列出某应用所有 API 名称与说明 | `app_name` (必填) |
| `show_api_doc(app_name='...', api_name='...')` | 返回某 API 完整文档（path、method、parameters、response_schemas） | `app_name`, `api_name` (必填) |

`show_app_descriptions()` 实际返回的应用：`api_docs, supervisor, amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist`。

### 1.2 Spotify 关键 API（app: `spotify`）
| API | Method + Path | 必填参数 | 关键返回字段 |
|---|---|---|---|
| `login` | POST `/auth/token` | `username`（账号邮箱）, `password` | `access_token`, `token_type` |
| `show_account` | GET `/account` | `access_token` | `first_name,last_name,email,registered_at,last_logged_in,verified,is_premium` |
| `search_artists` | GET `/artists` | 无（可选 `query`） | `artist_id,name,genre,follower_count,created_at` |
| `show_artist` | GET `/artists/{artist_id}` | `artist_id` | 同上 |
| `search_songs` | GET `/songs` | 无（可选 `query,artist_id,album_id,genre,min_play_count,max_play_count,sort_by,page_index,page_limit,...`） | `song_id,title,album_id,album_title,duration,artists[],release_date,genre,play_count,rating,like_count,review_count,shareable_link` |
| `show_song` | GET `/songs/{song_id}` | `song_id` | 同上，含 **`play_count`** |
| `show_song_privates` | GET `/songs/{song_id}/privates` | `song_id`, `access_token` | `liked,reviewed,in_song_library,downloaded`（**不含 play_count**） |
| `show_song_queue` | GET `/music_player/song_queue` | `access_token` | 队列歌曲 `song_id,title,album_id,album_title,duration,artists[],position,is_playing,is_current` |
| `add_to_queue` | POST `/music_player/song_queue` | `access_token`（另 `song_id`/`album_id`/`playlist_id` 三选一，均为可选） | `message` |

**统计口径结论：** 播放次数字段为 `play_count`（数值型），由 `search_songs`（`/songs`）与 `show_song`（`/songs/{song_id}`）返回；`show_song_privates` 仅返回用户私有的 liked/reviewed 等布尔值，**不含** `play_count`。筛选条件 “played over 980 times” 判定为 **`play_count > 980`（严格大于）**。

### 1.3 supervisor API（app: `supervisor`）
| API | Method + Path | 必填参数 | 返回 |
|---|---|---|---|
| `show_profile` | GET `/profile` | 无 | `first_name,last_name,email,phone_number,birthday,sex` |
| `show_account_passwords` | GET `/account_passwords` | 无 | `[{account_name,password}]` |
| `show_active_task` | GET `/active_task` | 无 | `instruction,status,answer` |
| `show_addresses` | GET `/addresses` | 无 | 地址列表 |
| `show_payment_cards` | GET `/payment_cards` | 无 | 支付卡列表 |
| `complete_task` | — | `answer`（结题用，本任务未调用） | — |

---

## 2. 用户身份结论

- supervisor `show_profile()` 实际返回：`{'first_name': 'Susan', 'last_name': 'Burton', 'email': 'susanmiller@gmail.com', 'phone_number': '3296062648', 'birthday': '1994-04-30', 'sex': 'female'}` → 当前模拟用户为 **Susan Burton**。
- `show_account_passwords()` 返回的 spotify 账号口令：`{'account_name': 'spotify', 'password': '%CCvl8v'}`（账号列表含 amazon/file_system/gmail/phone/simple_note/splitwise/spotify/todoist/venmo）。
- `show_active_task()` 实际返回：`instruction = 'Add all the songs from Lily Moon that have been played over 980 times to my Spotify player queue.'`，与本 Mission 目标一致 → **当前活动用户/任务确认**。
- Spotify 登录：`apis.spotify.login(username='susanmiller@gmail.com', password='%CCvl8v')` 成功，返回 `access_token`（Bearer）。
- `apis.spotify.show_account(access_token=...)` 实际返回：`{'first_name': 'Susan', 'last_name': 'Burton', 'email': 'susanmiller@gmail.com', 'registered_at': '2023-01-20T10:07:19', 'last_logged_in': '2023-01-20T10:07:19', 'verified': True, 'is_premium': False}` → 登录身份与 supervisor 是同一用户 Susan Burton。

---

## 3. 艺术家 Lily Moon 定位

`apis.spotify.search_artists(query='Lily Moon')` 实际返回中命中：`{'artist_id': 34, 'name': 'Lily Moon', 'genre': 'rock', 'follower_count': 25, 'created_at': '2018-12-04T01:37:41'}` → **Lily Moon artist_id = 34**。
`apis.spotify.show_artist(artist_id=34)` 亦可用于确认（同结构）。

---

## 4. Lily Moon 全部歌曲与入选判定（阈值 play_count > 980）

来源调用：
- 主来源：`apis.spotify.search_songs(artist_id=34, page_limit=20)`（返回 11 条，`page_index=1` 再查为 `[]`，确认为全部）。
- 交叉核对：对每首 `apis.spotify.show_song(song_id=...)`，两者 `play_count` 完全一致。

| # | song_id | title | artists | play_count（API 返回） | 与 980 比较 | 入选 (play_count>980) |
|---|---|---|---|---|---|---|
| 1 | 67 | The Echoes of a Silent Heart | Lily Moon, Zoey James | 916 | 916 ≤ 980 | 否 |
| 2 | 68 | Lost in the Wilderness of Love | Lily Moon, Zoey James | 156 | 156 ≤ 980 | 否 |
| 3 | 69 | Whispers of a Forgotten Love | Lily Moon, Zoey James | 645 | 645 ≤ 980 | 否 |
| 4 | 74 | On the Border of Reality | Lily Moon, Zoey James | 846 | 846 ≤ 980 | 否 |
| 5 | 75 | Walking Through the Valley of Shadows | Lily Moon, Zoey James | 806 | 806 ≤ 980 | 否 |
| 6 | 76 | Whispers of the Heart | Lily Moon, Zoey James | 330 | 330 ≤ 980 | 否 |
| 7 | 309 | Final Act | Lily Moon | 520 | 520 ≤ 980 | 否 |
| 8 | 310 | Harmony of the Distant Stars | Lily Moon | 715 | 715 ≤ 980 | 否 |
| 9 | 311 | Infinite Dreams | Lily Moon | **990** | 990 > 980 | **是** |
| 10 | 312 | Eternal Tears | Lily Moon | 562 | 562 ≤ 980 | 否 |
| 11 | 313 | Mystical Dreamscape | Lily Moon | 864 | 864 ≤ 980 | 否 |

### 唯一候选（入选）
- **song_id 311 — 「Infinite Dreams」 — 艺术家 Lily Moon — play_count = 990 > 980。**

其余 10 首均 ≤ 980，不入选（对应比较值见上表）。

---

## 5. 供下游使用的候选清单（唯一依据）

**入选歌曲（write queue target）：**
- `song_id = 311`, title `Infinite Dreams`, artist `Lily Moon`, `play_count = 990`.

**未入选歌曲（不得加入）：** 67, 68, 69, 74, 75, 76, 309, 310, 312, 313。

---

## 6. 当前队列快照（只读，供核对）

`apis.spotify.show_song_queue(access_token=...)` 返回 9 首（position 0–8），均为非 Lily Moon 歌曲，当前正在播放 `song_id 114『When All Hope Seems Lost』`（`is_current=True`）。快照中**不含**任何 Lily Moon 歌曲，即目标歌曲 311 尚未在队列中。

| position | song_id | title | artists | is_current |
|---|---|---|---|---|
| 0 | 102 | Autumn's Lament | Marigold Muse | False |
| 1 | 108 | Cold Embrace | Ava Morgan | False |
| 2 | 214 | The Sweet Pain of Reminiscence | Oceanic Odyssey | False |
| 3 | 202 | Summer's End | Ethan Wallace | False |
| 4 | 114 | When All Hope Seems Lost | Seraphina Dawn | True |
| 5 | 10 | The Curse of Loving You | Lucas Grey | False |
| 6 | 287 | Painted Skies | Hazel Winter | False |
| 7 | 285 | Wading Through the Ashes of Love | Marcus Lane | False |
| 8 | 38 | Destiny's Game | Aria Sterling | False |

---

## 7. 限制与说明
- 本任务未执行任何写操作；队列状态未被本任务改变。
- 全部 `play_count` 值来自 `search_songs` 与 `show_song` 的实际返回，未使用任何推测或外部数值。
- `show_song_privates` 不提供 `play_count`，故不作为播放次数来源。
- 无知识库条目（`knowledge_list` 返回 total=0）可供引用。
