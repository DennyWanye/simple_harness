#!/usr/bin/env python3
"""TC-WB-17 规模造数：把会话补到 >=30 个，其中一个带 120 字符无空格长标题。

只写 fixture 数据（messages + session_titles），不碰任何产品代码。会话在侧栏
清单里的存在条件是「已有消息」（backend list_sessions_with_preview 按 messages
分组），所以造数必须落到 messages。mtime/created_at 按固定步长错开，保证
"时间倒序"这一条断言可判。

长标题取 120 字符：产品侧 set_session_title 的服务端上限是 80
(SessionDB.MAX_TITLE_LEN)，即通过重命名入口最多只能存到 80。这里直接写 120 是
**更强的布局压力测试**——它覆盖了产品路径能达到的最坏情况并且更严，不会产生
假 PASS。该差异在 r5 证据里显式记录。
"""
import sqlite3
import sys
import uuid

DB = sys.argv[1] if len(sys.argv) > 1 else (
    "/Users/denny/projects/simple_harness/.testenv/cold-A/data/state.db"
)
TARGET = 30
# 固定基准时刻（不用 now()，保证可复现）：2026-08-06 00:00:00Z
BASE = 1786060800.0
LONG_TITLE = ("压力测试标题ABC123壹贰叁肆伍陆柒捌玖拾XYZ789"
              "混排无空格连续长串")
LONG_TITLE = (LONG_TITLE * 6)[:120]
assert len(LONG_TITLE) == 120 and " " not in LONG_TITLE

con = sqlite3.connect(DB)
cur = con.cursor()
existing = [r[0] for r in cur.execute(
    "SELECT session_id FROM messages GROUP BY session_id"
).fetchall()]
need = max(0, TARGET - len(existing))
print(f"existing sessions with messages: {len(existing)}; seeding {need}")

made = []
for i in range(need):
    sid = str(uuid.uuid4())
    ts = BASE + i * 60.0          # 每个会话间隔 1 分钟，越后越新
    cur.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
        (sid, "user", f"规模造数会话#{i + 1:02d} 标记语句", ts),
    )
    cur.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
        (sid, "assistant", f"回执#{i + 1:02d}", ts + 1.0),
    )
    made.append((sid, ts))

if made:
    # 最老的那个（列表最底部）挂 120 字符长标题，顺带验证"滚到底"和"截断"同时成立
    long_sid = made[0][0]
    cur.execute(
        "INSERT OR REPLACE INTO session_titles (session_id, title, updated_at) VALUES (?,?,?)",
        (long_sid, LONG_TITLE, BASE),
    )
    print(f"long-title session (oldest, list bottom): {long_sid}")
    print(f"title len={len(LONG_TITLE)} spaces={LONG_TITLE.count(' ')}")

con.commit()
total = cur.execute(
    "SELECT COUNT(*) FROM (SELECT session_id FROM messages GROUP BY session_id)"
).fetchone()[0]
newest = cur.execute(
    "SELECT session_id, MAX(created_at) FROM messages GROUP BY session_id "
    "ORDER BY MAX(created_at) DESC LIMIT 1"
).fetchone()
oldest = cur.execute(
    "SELECT session_id, MAX(created_at) FROM messages GROUP BY session_id "
    "ORDER BY MAX(created_at) ASC LIMIT 1"
).fetchone()
print(f"total sessions now: {total}")
print(f"newest (list top):    {newest}")
print(f"oldest (list bottom): {oldest}")
con.close()
