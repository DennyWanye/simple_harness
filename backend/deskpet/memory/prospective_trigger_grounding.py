# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AE：Prospective 的 ``time`` 触发必须由引文里的时间表达接地。

HM-TO-A6 第 11 次 T14（负控 NC-3）「以后有机会我想学画画。」被分析车道（协议 v9）
提成了一条 ``prospective``，``trigger_at=1788920760.0``（Asia/Shanghai
2026-09-09 10:26:00，实测早于本轮 plan 落库时刻 1788920762.9），注册成功两条，
并在旅程内 **真的触发** 了（``prospective_trigger_events`` 一条 ``time_due/matched``，
``cognitive_memory_revisions`` revision 2 ``lifecycle=triggered``）。

引文只有「以后有机会我想学画画。」一句，里面 **没有任何时间表达**。v3..v9 的编译器对
prospective 只做一件事：``datetime.fromisoformat(trigger_at_iso)`` 且要求带时区偏移
（``analysis_proposal.compile_operation`` 的 else 分支）。逐字引文规则约束的是
``exact_quote``，从不约束 ``trigger_at_iso`` —— 于是「首次到期时间」是整个分析车道里
**唯一一个模型可以凭空写、Host 完全不核对** 的字段。这就是缺陷的根因。

本模块是 ``host-analysis-validator/v6`` 的接地判定：把引文里的时间表达确定性地解析成
若干「可接受区间」，再看模型给的 ``trigger_at`` 落不落在其中。两条硬规则：

``analysis_prospective_vague_wish``
    引文里出现模糊将来标记（有机会 / 以后 / 将来 / 今后 / 哪天 / 有空 / 改天 / 找时间
    / 抽空 / 迟早 / 某天 / 总有一天）而 **没有** 任何具体时间表达 —— 这是一个愿望，不是
    提醒。产品决定（``plans/2026-09-08-hm-to-a6/00-PLAN.md`` T14 行「只进 Semantic
    Goal」）：它应当成为 semantic interest/goal 或 episode，绝不是被调度的 Prospective。

``analysis_prospective_trigger_not_grounded``
    引文里有时间表达，但没有一个能解析到模型给的 ``trigger_at``（含容差）；或者引文里
    根本没有时间表达也没有模糊标记。

设计取舍写在这里，免得下一个人重新推一遍：

* **为什么必须自己写解析器。** 任务书假设「复用 Host 现成的、C04 oracle / ``trigger_local``
  用的那个时间表达解析器」。实测不存在：``deskpet/quality/corpus_c04.py`` 的时间来自
  ``SPECS`` 里手写的 anchor 字面量（``'2026-09-07T09:00'``），``precision_oracle`` 只是
  把它们抄进评分记录；``human_memory_v7._prospective_trigger_local`` 是 **渲染** 方向
  （epoch + IANA → 本地字符串），不是解析方向。整个仓库没有任何「中文时间短语 → 时间戳」
  的代码（``companion/*`` 的 ``_parse_time`` 系列都只吃 ISO/HH:MM 配置值）。所以接地判定
  必须新写，而 C04 的 anchor 文本（``9月7日09:00``、``2027年1月2日10:00``、
  ``9月8日00:30``）正好成了这个解析器的正样本语料。
* **只做保守判定。** 解析不出来 = 拒收，不是放行。少认一个真实提醒的代价是模型下一轮
  重提；多认一个凭空时间的代价是用户被一个他从没约过的日程叫醒（本次事件）。
* **容差有界。** 带钟点的表达给 ±5 分钟（模型偶尔把「九点半」写成 09:30:00 或 09:29:xx）；
  只有日期没有钟点的表达给「那一整个本地日」——「下周三提醒我」没说几点，模型选 09:00
  还是 10:00 都是诚实的。
* **时区取触发器自己声明的 IANA 名。** 模型声明了哪个区，就在哪个区里解析相对表达；声明
  一个解析不了的区，接地自然失败，方向是安全的。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VAGUE_WISH = "analysis_prospective_vague_wish"
TRIGGER_NOT_GROUNDED = "analysis_prospective_trigger_not_grounded"

# 「模糊将来」标记。出现其中之一而引文里没有具体时间表达时，这句话是愿望不是提醒。
# 注意 ``以后`` 也出现在「以后整理文件就按这两步」这种 **采用** 句里 —— 那是 procedure，
# 不是 prospective，本规则只在 prospective 分支上生效；而「以后每周三提醒我」有具体时间
# 表达，走的是接地分支，模糊标记不生效。
VAGUE_WISH_MARKERS: tuple[str, ...] = (
    "有机会", "以后", "今后", "将来", "未来某", "哪天", "某天", "改天", "有空",
    "找时间", "抽空", "迟早", "总有一天", "有朝一日", "得空", "someday",
)

# 带钟点的表达：模型写成秒级零点、或把「九点半」落在 09:29:5x，都算同一个时刻。
CLOCK_TOLERANCE_SECONDS = 300.0

_CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_WEEKDAY_NAMES = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
                  "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}

_NUM = r"(?:\d{1,4}|[〇零一二三四五六七八九十两]{1,4})"


@dataclass(frozen=True, slots=True)
class TimeExpression:
    """引文里一个时间表达，以及它允许 ``trigger_at`` 落在的区间 ``[start, end)``。"""

    surface: str
    start: float
    end: float
    precision: str  # "minute" | "day"

    def admits(self, moment: float) -> bool:
        return self.start <= float(moment) < self.end


def _cn_number(token: str) -> int | None:
    """``23`` / ``二十三`` / ``十`` / ``两`` → int；解析不了返回 None。"""

    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if any(char not in _CN_DIGITS and char != "十" for char in token):
        return None
    if "十" not in token:
        value = 0
        for char in token:
            value = value * 10 + _CN_DIGITS[char]
        return value
    head, _, tail = token.partition("十")
    tens = _CN_DIGITS[head] if head else 1
    ones = 0
    for char in tail:
        ones = ones * 10 + _CN_DIGITS[char]
    return tens * 10 + ones


def _zone(name: Any) -> Any:
    from datetime import timezone as _utc

    if isinstance(name, str) and name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
    return _utc.utc


def _epoch(day: date, hour: int, minute: int, zone: Any) -> float | None:
    try:
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _day_window(day: date, zone: Any) -> tuple[float, float] | None:
    start = _epoch(day, 0, 0, zone)
    end = _epoch(day + timedelta(days=1), 0, 0, zone)
    if start is None or end is None:
        return None
    return start, end


# ------------------------------------------------------------------ 钟点

_PERIODS = (
    (("凌晨",), 0), (("早上", "早晨", "清晨", "上午"), 0), (("中午", "正午"), 12),
    (("下午", "午后"), 12), (("傍晚", "晚上", "晚间", "夜里", "夜晚", "今晚", "明晚"), 12),
)
_CLOCK_RE = re.compile(
    rf"(?P<period>凌晨|早上|早晨|清晨|上午|中午|正午|下午|午后|傍晚|晚上|晚间|夜里|夜晚|今晚|明晚)?"
    rf"(?:(?P<h1>\d{{1,2}}):(?P<m1>\d{{2}})"
    rf"|(?P<h2>{_NUM})[点時时](?P<m2>半|一刻|三刻|整|{_NUM}分?)?)"
)


def _clocks(text: str) -> list[tuple[int, int, str]]:
    """引文里的钟点，返回 ``(hour, minute, surface)``。24 小时制已归一。"""

    found: list[tuple[int, int, str]] = []
    for match in _CLOCK_RE.finditer(text):
        if match.group("h1") is not None:
            hour, minute = int(match.group("h1")), int(match.group("m1"))
        else:
            raw_hour = _cn_number(match.group("h2") or "")
            if raw_hour is None:
                continue
            hour = raw_hour
            tail = match.group("m2")
            if tail in (None, "整"):
                minute = 0
            elif tail == "半":
                minute = 30
            elif tail == "一刻":
                minute = 15
            elif tail == "三刻":
                minute = 45
            else:
                parsed = _cn_number(tail.rstrip("分"))
                if parsed is None:
                    continue
                minute = parsed
        period = match.group("period")
        if period is not None:
            offset = next(value for names, value in _PERIODS if period in names)
            if offset and hour < 12:
                hour += offset
            elif period in ("中午", "正午") and hour == 12:
                hour = 12
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        found.append((hour, minute, match.group(0)))
    return found


# ------------------------------------------------------------------ 日期

_YMD_RE = re.compile(rf"(?:(?P<y>\d{{4}})年)?(?P<m>{_NUM})月(?P<d>{_NUM})[日号]")
_ISO_RE = re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})")
_RELATIVE_DAYS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "明早": 1, "明晚": 1,
                  "后天": 2, "大后天": 3}
_WEEK_RE = re.compile(r"(?P<which>本|这|下下|下)?(?:个)?(?:周|星期|礼拜)(?P<day>[一二三四五六日天]|[1-7])")
_OFFSET_DAY_RE = re.compile(rf"(?P<n>{_NUM})\s*(?P<unit>天|日|周|星期|个月)(?:之)?后")
_OFFSET_CLOCK_RE = re.compile(rf"(?:(?P<n>{_NUM})\s*(?:个)?(?P<unit>分钟|小时|钟头)|(?P<half>半\s*(?:个)?(?:小时|钟头)))(?:之)?后")


def _dates(text: str, today: date) -> list[tuple[date, str]]:
    """引文里的日历日，返回 ``(date, surface)``；一个表达可给出多个候选日。"""

    found: list[tuple[date, str]] = []
    for match in _ISO_RE.finditer(text):
        try:
            found.append((date(int(match.group("y")), int(match.group("m")), int(match.group("d"))),
                          match.group(0)))
        except ValueError:
            continue
    for match in _YMD_RE.finditer(text):
        month, day = _cn_number(match.group("m") or ""), _cn_number(match.group("d") or "")
        if month is None or day is None:
            continue
        years = [int(match.group("y"))] if match.group("y") else [today.year, today.year + 1]
        for year in years:
            try:
                found.append((date(year, month, day), match.group(0)))
            except ValueError:
                continue
    for word, offset in _RELATIVE_DAYS.items():
        if word in text:
            found.append((today + timedelta(days=offset), word))
    for match in _WEEK_RE.finditer(text):
        index = _WEEKDAY_NAMES.get(match.group("day"))
        if index is None:
            continue
        monday = today - timedelta(days=today.weekday())
        which = match.group("which")
        weeks = {"本": (0,), "这": (0,), "下": (1,), "下下": (2,)}.get(which, (0, 1))
        for week in weeks:
            found.append((monday + timedelta(days=index, weeks=week), match.group(0)))
    for match in _OFFSET_DAY_RE.finditer(text):
        count = _cn_number(match.group("n") or "")
        if count is None or count > 3650:
            continue
        unit = match.group("unit")
        days = count * (1 if unit in ("天", "日") else 7 if unit in ("周", "星期") else 30)
        found.append((today + timedelta(days=days), match.group(0)))
    return found


def time_expressions(text: str, *, reference: float, timezone: Any) -> tuple[TimeExpression, ...]:
    """把 ``text`` 里的时间表达解析成可接受区间。``reference`` 是本轮证据的采纳时刻。

    确定性：同样的 ``(text, reference, timezone)`` 永远给同样的结果，不读系统时钟。
    """

    zone = _zone(timezone)
    if zone is None or not isinstance(text, str) or not text:
        return ()
    try:
        anchor = datetime.fromtimestamp(float(reference), zone)
    except (OverflowError, OSError, ValueError):
        return ()
    today = anchor.date()
    clocks = _clocks(text)
    dates = _dates(text, today)
    out: list[TimeExpression] = []

    for day, surface in dates:
        if clocks:
            for hour, minute, clock_surface in clocks:
                moment = _epoch(day, hour, minute, zone)
                if moment is not None:
                    out.append(TimeExpression(f"{surface}{clock_surface}",
                                              moment - CLOCK_TOLERANCE_SECONDS,
                                              moment + CLOCK_TOLERANCE_SECONDS, "minute"))
        window = _day_window(day, zone)
        if window is not None:
            out.append(TimeExpression(surface, window[0], window[1], "day"))

    if not dates:
        # 只有钟点：本轮当天的那个点，以及（若已过）第二天的同一个点。
        for hour, minute, clock_surface in clocks:
            for offset in (0, 1):
                moment = _epoch(today + timedelta(days=offset), hour, minute, zone)
                if moment is None:
                    continue
                if offset == 1 and moment <= float(reference):
                    continue
                out.append(TimeExpression(clock_surface, moment - CLOCK_TOLERANCE_SECONDS,
                                          moment + CLOCK_TOLERANCE_SECONDS, "minute"))

    for match in _OFFSET_CLOCK_RE.finditer(text):
        if match.group("half"):
            seconds = 1800.0
        else:
            count = _cn_number(match.group("n") or "")
            if count is None or count > 100000:
                continue
            seconds = float(count) * (60.0 if match.group("unit") == "分钟" else 3600.0)
        moment = float(reference) + seconds
        out.append(TimeExpression(match.group(0), moment - CLOCK_TOLERANCE_SECONDS,
                                  moment + CLOCK_TOLERANCE_SECONDS, "minute"))
    return tuple(out)


def vague_wish_markers(text: str) -> tuple[str, ...]:
    if not isinstance(text, str):
        return ()
    return tuple(marker for marker in VAGUE_WISH_MARKERS if marker in text)


def check_time_trigger(*, quote: str, trigger_at: float, timezone: Any, reference: float):
    """接地判定；不合格时抛 ``AnalysisProposalRejected``，合格时返回命中的表达。

    ``quote`` 是这条 operation 自己引用的证据跨度原文（Host 已逐字节核对过它确实是证据
    文本的子串），所以「引文里有没有时间表达」是一个 Host 可独立复算的事实。
    """
    from deskpet.memory.analysis_proposal import AnalysisProposalRejected

    expressions = time_expressions(quote, reference=reference, timezone=timezone)
    for expression in expressions:
        if expression.admits(trigger_at):
            return expression
    markers = vague_wish_markers(quote)
    if not expressions and markers:
        raise AnalysisProposalRejected(VAGUE_WISH, markers=markers[:4])
    raise AnalysisProposalRejected(
        TRIGGER_NOT_GROUNDED,
        trigger_at=float(trigger_at),
        expressions=tuple(expression.surface for expression in expressions)[:4],
    )
