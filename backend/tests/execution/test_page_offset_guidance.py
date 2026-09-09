"""事件 AF：offset 拒绝必须自带可执行的下一步。

原生证据 `.local-test-evidence/2026-09-09/native-a6-run12b/primary-ui-9iyw1map/`
（`execution-v6.sqlite3`，24 次 provider 调用）第 13 轮：同一个 47 KB 的 read_file
结果被 ``context_page_in`` 读了 16 次，9 成 7 败，7 次全是
``primary_page_offset_invalid``——没有一次越界，也没有一次是引用失效。

    轮/序   offset    在范围内   落在码点边界   结果
    22/3    24576     是         否            失败
    22/4    32768     是         否            失败
    23/0     2048     是         否            失败
    23/1     3072     是         否            失败
    23/5    20480     是         否            失败
    23/6    24576     是         否            失败
    23/8    32768     是         否            失败

正文 48 272 B 全是中文，只有 21 227 个字节位置（44 %）落在 UTF-8 码点边界上；
模型按 1024 / 8192 的等距步长猜 offset，于是 56 % 的猜测必然被拒。而拒绝只回
"Requested primary page is unavailable."：没有页大小、没有页起点、没有下一页。

这一组测试钉住修复后的形态：描述符讲清 offset 的单位与页数，拒绝带上页起点
清单与本 Run 的下一个未读 offset，引用真的不在本请求里时给一个不同的稳定码。
"""
import json
import sqlite3
from types import SimpleNamespace

import aiosqlite
import pytest
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness import CallId

from deskpet.execution.current_tool_pages import (
    DESCRIPTOR_OFFSET_LIMIT, MARKER, MAX_LISTED_OFFSETS, PAGE_SIZE, PREFIX,
    REFERENCE_UNAVAILABLE_CODE,
    REJECTION_CODE_ALIASES, PrimaryContextPageUnavailable, _listed_offsets,
    admitted_current_page, legacy_bounded_summary, legacy_summary, page_starts,
    legacy_af_summary, reference, rejection_code_matches, source_content, summary,
    verify_request, LEGACY_PAGE_SIZE,
)
from deskpet.execution.primary_context_pages import PAGE_BYTES, _excerpt
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from deskpet.tools.context_page_in_tools import (
    CONTEXT_PAGE_IN_SCHEMA, PRIMARY_PAGE_PUBLIC_MESSAGE, _primary_page_message,
)

from tests.execution.test_control_result_bound import (
    HOST_RUN_ID, RUN_ID, FakeEffect, public_body,
)

# 事件 AF-2 之前 ``CONTEXT_PAGE_IN_SCHEMA["description"]`` 的 token 数（875 B / 219）。
# 说明可以改措辞，但不许变贵——8192 档的受保护预算余量是 0。
DESCRIPTION_TOKENS_BEFORE_AF2 = 219

# 证据里 47 KB 参照件 B 的形状：整段中文，页起点几乎都不是 1024 的整数倍。
CJK_LINE = "B-%05d 条目：秋分资料整理清单第 %d 行，校对状态待定，责任人未指派。\n"
SOURCE_EFFECT = "effect-src" + "c" * 58
PAGE_EFFECT = "effect-page" + "c" * 57


def cjk_value(lines):
    return {"content": "".join(CJK_LINE % (i, i) for i in range(lines))}


class PageEffect(FakeEffect):
    """一个 ``context_page_in`` 效果：额外带上它自己的实参（拒绝提示要读）。"""

    def __init__(self, *, effect_id, reference_id, source_hash, state="succeeded"):
        super().__init__(effect_id=effect_id, tool_name="context_page_in",
                         raw_call_id="call_pg_" + effect_id[-24:], value={"ok": True},
                         state=state)
        self.arguments = {"reference_id": reference_id, "source_hash": source_hash}


class Stack:
    """公共效果/审计事实 + 已持久化的父请求，正是 ``admitted_current_page`` 读的面。"""

    def __init__(self, effects, request_messages):
        self.effects = effects
        self.request_messages = tuple(request_messages)

    def read_primary_effect_page_facts(self, run_id, effect_id):
        effect = self.effects.get(effect_id)
        if effect is None or run_id != RUN_ID:
            raise PrimaryContextPageUnavailable("primary_effect_page_effect_missing")
        head = SimpleNamespace(result_hash="a1" * 32,
                               provider_invocation_id="inv-" + str(effect_id)[-8:])
        parent = SimpleNamespace(invocation_id="inv-" + str(effect_id)[-8:],
                                 response_json={"id": effect_id})
        return effect, head, parent, SimpleNamespace(messages=self.request_messages)


async def _identities(path, rows):
    """Host 的不可变顺序索引；``admitted_current_page`` 只从这里取先后关系。"""
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS primary_effect_identities (sequence INTEGER PRIMARY KEY, "
                   "host_run_id TEXT, sdk_run_id TEXT, effect_id TEXT, tool_name TEXT, "
                   "identity_hash TEXT, identity_json TEXT)")
        for sequence, (effect_id, tool_name) in enumerate(rows, start=1):
            identity = dict(host_run_id=HOST_RUN_ID, sdk_run_id=RUN_ID,
                            effect_id=effect_id, tool_name=tool_name)
            db.execute("INSERT OR REPLACE INTO primary_effect_identities VALUES (?,?,?,?,?,?,?)",
                       (sequence, HOST_RUN_ID, RUN_ID, effect_id, tool_name,
                        canonical_hash(identity), canonical_json(identity)))


def _scenario(tmp_path, *, lines=520, prior_pages=(), wire=summary):
    """一个真实形状的场景：CJK 正文 + 已分页的父请求 + 若干已准入的历史页。"""
    value = cjk_value(lines)
    source = FakeEffect(effect_id=SOURCE_EFFECT, tool_name="read_file",
                        raw_call_id="call_00_" + "d" * 24, value=value)
    effects = {SOURCE_EFFECT: source}
    descriptor, content = source_content(
        Stack(effects, ()).read_primary_effect_page_facts(RUN_ID, SOURCE_EFFECT), min_bytes=0)
    body = wire(descriptor, content)
    messages = [Message(MessageRole.TOOL, body, name="read_file",
                        call_id=CallId(source.raw_call_id), metadata={"source": MARKER})]
    rows = [(SOURCE_EFFECT, "read_file")]
    for index, offset in enumerate(prior_pages):
        prior_id = "effect-prior%02d%s" % (index, "c" * 54)
        effects[prior_id] = PageEffect(effect_id=prior_id,
                                       reference_id=reference(descriptor, offset),
                                       source_hash=descriptor["content_hash"])
        rows.append((prior_id, "context_page_in"))
    caller = PageEffect(effect_id=PAGE_EFFECT, reference_id="", source_hash="")
    effects[PAGE_EFFECT] = caller
    rows.append((PAGE_EFFECT, "context_page_in"))
    path = str(tmp_path / "state.db")
    return SimpleNamespace(path=path, rows=rows, stack=Stack(effects, messages),
                           descriptor=descriptor, content=content, caller=caller,
                           run={"host_run_id": HOST_RUN_ID})


def mid_codepoint(content, low, high):
    """``low`` 之后第一个落在码点中间的字节位置——事故里模型撞上的那种 offset。"""
    raw = content.encode("utf-8")
    return next(o for o in range(low, high) if (raw[o] & 0xC0) == 0x80)


async def _page(scenario, offset, *, digest=None):
    await _identities(scenario.path, scenario.rows)
    ref = (PREFIX + digest + ":" + str(offset)) if digest else reference(scenario.descriptor, offset)
    async with aiosqlite.connect(scenario.path) as db:
        db.row_factory = aiosqlite.Row
        return await admitted_current_page(
            db=db, stack=scenario.stack, run=scenario.run, sdk_run_id=RUN_ID,
            page_effect=scenario.caller,
            arguments={"reference_id": ref, "source_hash": scenario.descriptor["content_hash"]})


# --------------------------------------------------------------------------
# 1. 事故的形状：等距 offset 在中文正文上必然撞死
# --------------------------------------------------------------------------

def test_the_incident_shape_is_reproduced_by_construction():
    """证据里的 7 次失败全部是「在范围内、但不在码点边界上」。"""
    content = public_body(cjk_value(520))
    raw = content.encode("utf-8")
    assert len(raw) > 40_000
    boundaries = sum(1 for i in range(len(raw)) if (raw[i] & 0xC0) != 0x80)
    # 证据实测 21 227 / 48 272 = 44 %；这里只钉住「远不到一半」这个性质。
    assert boundaries * 2 < len(raw)
    starts = page_starts(content)
    # 页起点几乎都不是 page_size 的整数倍——正是模型猜错的原因。
    assert sum(1 for s in starts if s % PAGE_SIZE) > len(starts) // 2
    # 而每一个页起点都真的可读，且首尾相接覆盖整份正文，不重不漏。
    rebuilt = "".join(_excerpt(content, s) for s in starts)
    assert rebuilt == content


# --------------------------------------------------------------------------
# 2. 描述符：offset 的单位与页数由描述符自己讲清楚
# --------------------------------------------------------------------------

def test_descriptor_publishes_the_offset_unit_and_the_exact_page_count():
    # 2026-09-09 事件 AG：页大小 1024 -> 4096，同一份正文的页数掉到四分之一，
    # 所以这里的取样点也跟着走——60 行（2 页、页起点恰好是页大小的整数倍，清单
    # 没信息量所以不印）、150 行（4 页、页起点不是整数倍，清单印全）、520 行
    # （事故里那份 47 KB 参照件，13 页 > 上限，不印）。三种分支都要走到。
    listed_branches = set()
    for lines in (60, 150, 520):
        value = cjk_value(lines)
        effect = FakeEffect(effect_id=SOURCE_EFFECT, tool_name="read_file",
                            raw_call_id="call_00_" + "d" * 24, value=value)
        descriptor, content = source_content(
            Stack({SOURCE_EFFECT: effect}, ()).read_primary_effect_page_facts(RUN_ID, SOURCE_EFFECT),
            min_bytes=0)
        wire = json.loads(summary(descriptor, content))
        starts = page_starts(content)
        assert wire["page_size"] == PAGE_SIZE == PAGE_BYTES
        assert wire["page_count"] == len(starts)
        assert "pages" not in wire
        # 印清单的判据与产线完全一致：页数在上限内**且**清单带信息
        # （页起点不是页大小的整数倍，那正是多字节正文推不出来的那一种）。
        listed = (len(starts) <= DESCRIPTOR_OFFSET_LIMIT
                  and starts != list(range(0, descriptor["content_bytes"], PAGE_SIZE)))
        listed_branches.add(listed)
        if listed:
            assert wire["valid_offsets"] == starts
        else:
            assert "valid_offsets" not in wire
    assert listed_branches == {True, False}, "两个分支都要被取样点走到"


def test_descriptor_omits_offsets_that_page_size_already_determines():
    """单字节正文的页起点就是 page_size 的整数倍，重复印是白花字节。"""
    effect = FakeEffect(effect_id=SOURCE_EFFECT, tool_name="tool_search",
                        raw_call_id="call_00_" + "d" * 24, value={"filler": "x" * 3000})
    descriptor, content = source_content(
        Stack({SOURCE_EFFECT: effect}, ()).read_primary_effect_page_facts(RUN_ID, SOURCE_EFFECT),
        min_bytes=0)
    wire = json.loads(summary(descriptor, content))
    assert wire["page_count"] <= DESCRIPTOR_OFFSET_LIMIT and "valid_offsets" not in wire
    assert page_starts(content) == list(range(0, descriptor["content_bytes"], PAGE_SIZE))


def test_the_three_earlier_wire_shapes_are_still_verifiable(tmp_path):
    """升级不得把还在飞的 Run 判死：升级前的每一种线上形态都仍然过。

    2026-09-09 事件 AG 追加了第三种——``legacy_af_summary``，也就是「页大小 1024、
    没有 text_stats」的那一版。它必须继续被 ``verify_request`` 接受，否则一次纯粹
    的页容量升级会把升级瞬间还在飞的 Run 全部判成 ``summary_mismatch``。
    """
    for wire in (summary, legacy_af_summary, legacy_bounded_summary, legacy_summary):
        scenario = _scenario(tmp_path, lines=60, wire=wire)
        found = verify_request(scenario.stack, RUN_ID, scenario.stack.request_messages)
        assert list(found) == [canonical_hash(scenario.descriptor)]


# --------------------------------------------------------------------------
# 3. 拒绝详情：三种形态各自的稳定码与可执行下一步
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offset_off_a_character_boundary_is_rejected_with_the_page_starts(tmp_path):
    # 事件 AG：页大小提到 4096 之后，事故那份 47 KB 正文只剩 13 页，装不满
    # 「前 16 个 + 最后一个」这条界。要钉住有界性就得给一份真的超过 17 页的正文
    # （800 行 ≈ 79 KB / 20 页），否则这条断言会退化成恒真。
    scenario = _scenario(tmp_path, lines=800)
    starts = page_starts(scenario.content)
    assert len(starts) > MAX_LISTED_OFFSETS + 1
    bad = mid_codepoint(scenario.content, 2048, 4096)
    with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
        await _page(scenario, bad)
    # 稳定码一个字都没变——旧 Run 的重放比对原样成立。
    assert str(excinfo.value) == "primary_page_offset_invalid"
    detail = excinfo.value.detail
    assert detail["reason"] == "offset_not_on_character_boundary"
    assert detail["requested_offset"] == bad
    assert detail["page_size"] == PAGE_SIZE
    assert detail["page_count"] == len(starts)
    assert detail["content_bytes"] == len(scenario.content.encode("utf-8"))
    # 有界：前 16 个 + 最后一个。
    assert detail["valid_offsets"] == _listed_offsets(starts)
    assert len(detail["valid_offsets"]) == MAX_LISTED_OFFSETS + 1 == 17 < len(starts)
    assert detail["valid_offsets"][-1] == starts[-1]
    # 没有任何一页读过时，下一步就是从头翻。
    assert detail["next_offset"] == 0
    assert detail["retry_reference_id"] == reference(scenario.descriptor, 0)
    assert "BYTE offset" in detail["next_step"]


@pytest.mark.asyncio
async def test_next_offset_follows_the_highest_page_already_admitted_in_this_run(tmp_path):
    """事件 B 口径：下一步来自权威回执，不是从模型写的请求文本里读的。"""
    scenario = _scenario(tmp_path, lines=520)
    starts = page_starts(scenario.content)
    admitted = [starts[1], starts[4], starts[2]]  # 乱序，取最高的那个
    scenario = _scenario(tmp_path, lines=520, prior_pages=admitted)
    bad = mid_codepoint(scenario.content, 20480, 24576)
    with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
        await _page(scenario, bad)
    detail = excinfo.value.detail
    expected = starts[4] + len(_excerpt(scenario.content, starts[4]).encode("utf-8"))
    assert detail["next_offset"] == expected == starts[5]
    assert detail["retry_reference_id"] == reference(scenario.descriptor, expected)
    # 而那个 offset 真的能读。
    page = await _page(scenario, expected)
    assert page["ok"] and page["offset"] == expected


@pytest.mark.asyncio
async def test_offset_past_the_end_says_so_and_still_lists_the_page_starts(tmp_path):
    scenario = _scenario(tmp_path, lines=520)
    beyond = len(scenario.content.encode("utf-8")) + 1024
    with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
        await _page(scenario, beyond)
    detail = excinfo.value.detail
    assert str(excinfo.value) == "primary_page_offset_invalid"
    assert detail["reason"] == "offset_past_end"
    assert detail["requested_offset"] == beyond
    assert detail["valid_offsets"][0] == 0 and detail["page_size"] == PAGE_SIZE


@pytest.mark.asyncio
async def test_a_reference_that_is_not_in_this_request_gets_its_own_stable_code(tmp_path):
    """引用被逐出/这轮走了原文：换 offset 重试永远不会成功，必须换一步走。"""
    scenario = _scenario(tmp_path, lines=520)
    with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
        await _page(scenario, 0, digest="f" * 64)
    assert str(excinfo.value) == REFERENCE_UNAVAILABLE_CODE == "primary_page_reference_unavailable"
    detail = excinfo.value.detail
    # 事件 AF-2：原因码并入三个一致的引用原因（`page_reference_unknown` /
    # `page_reference_stale` / `page_reference_missing_offset`）。稳定**码**没变，
    # 变的只是 detail 里的这个词，它不参与任何重放比对。
    assert detail["reason"] == "page_reference_stale"
    assert "re-run the tool" in detail["next_step"]
    # 而且必须给得出替代品：本请求现在真正可用的那条引用。
    assert detail["available_reference_ids"] == [reference(scenario.descriptor, 0)]
    assert detail["references_available"] == 1
    # 升级前记录的旧码仍然与它重放一致（review M1 同款兼容）。
    assert REJECTION_CODE_ALIASES[REFERENCE_UNAVAILABLE_CODE] == "primary_effect_page_not_admitted"
    assert rejection_code_matches("primary_effect_page_not_admitted", REFERENCE_UNAVAILABLE_CODE)
    assert rejection_code_matches(REFERENCE_UNAVAILABLE_CODE, REFERENCE_UNAVAILABLE_CODE)
    # 只认这一个方向：别的码不得互相顶替。
    assert not rejection_code_matches(REFERENCE_UNAVAILABLE_CODE, "primary_page_offset_invalid")


@pytest.mark.asyncio
async def test_every_published_page_start_is_actually_readable(tmp_path):
    """清单必须是可执行的：印出来的每一个 offset 都真的能读回字节。"""
    scenario = _scenario(tmp_path, lines=520)
    starts = page_starts(scenario.content)
    for offset in _listed_offsets(starts):
        page = await _page(scenario, offset)
        assert page["ok"] and page["offset"] == offset
        assert scenario.content.encode("utf-8")[offset:].startswith(page["content"].encode("utf-8"))


@pytest.mark.asyncio
async def test_rejection_detail_is_deterministic(tmp_path):
    scenario = _scenario(tmp_path, lines=520)
    bad = mid_codepoint(scenario.content, 2048, 4096)
    details = []
    for _ in range(3):
        with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
            await _page(scenario, bad)
        details.append(canonical_json(excinfo.value.detail))
    assert len(set(details)) == 1


# --------------------------------------------------------------------------
# 4. 工具层：详情真的抵达模型，前缀逐字不变
# --------------------------------------------------------------------------

def test_public_message_keeps_the_frozen_prefix_and_carries_the_next_step():
    detail = dict(reason="offset_not_on_character_boundary", requested_offset=2048,
                  page_size=1024, page_count=48, content_bytes=48272,
                  valid_offsets=[0, 1023, 2046], offsets_listed=3, next_offset=41984,
                  retry_reference_id=PREFIX + "b" * 64 + ":41984", next_step="x")
    message = _primary_page_message(detail)
    # ``primary_dependencies`` 靠这个前缀认出主页面的确定性拒绝。
    assert message.startswith(PRIMARY_PAGE_PUBLIC_MESSAGE)
    assert json.loads(message[len(PRIMARY_PAGE_PUBLIC_MESSAGE) + 1:]) == detail
    # ``sdk_adapters.tools._MAX_HANDLER_PUBLIC_MESSAGE`` 是 2048，必须留在里面。
    assert len(message) <= 2048
    # 没有详情时逐字回到升级前的那一句。
    assert _primary_page_message(None) == PRIMARY_PAGE_PUBLIC_MESSAGE
    assert _primary_page_message({}) == PRIMARY_PAGE_PUBLIC_MESSAGE
    # 详情大到会被上游截断时，宁可不带也不带半条 JSON。
    assert _primary_page_message(dict(detail, next_step="y" * 4096)) == PRIMARY_PAGE_PUBLIC_MESSAGE


def test_tool_description_states_the_offset_unit():
    description = CONTEXT_PAGE_IN_SCHEMA["description"]
    assert "BYTE offset" in description
    assert "not a page index" in description
    assert "next_reference_id" in description and "retry_reference_id" in description
    assert "valid_offsets" in description
    # 事件 AF-2：事故的形态是把 reference_id 抄成「前缀 + 摘要」，尾巴整段丢掉，
    # 所以说明必须同时禁掉「发明」与「丢弃」。
    assert "never invent or drop it" in description
    assert 'always ends in ":<offset>"' in description
    # 而且这句话是**换来的**不是加上去的：整段说明的 token 数不得超过事件 AF-2
    # 之前的量（8192 档受保护预算余量为 0，见
    # ``test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn``）。
    from deskpet.sdk_adapters.context_partitions import text_tokens
    assert text_tokens(description) <= DESCRIPTION_TOKENS_BEFORE_AF2
    # 参数说明不加字：它进 ``tool_schema_tokens``，而 8192 档的受保护预算没有余量。
    parameter = CONTEXT_PAGE_IN_SCHEMA["parameters"]["properties"]["reference_id"]["description"]
    assert "offset" not in parameter


# --------------------------------------------------------------------------
# 5. 事件 AG：页大小 1024 -> 4096，准入身份与重放一个字节都不许变
# --------------------------------------------------------------------------


def test_the_page_size_upgrade_does_not_touch_which_offsets_are_admitted():
    """页大小只决定「一页返回多少字节」，绝不决定「哪个 offset 可以受理」。

    事故里已记录的成功页（8192 / 16384 / 40960 之类）都不在新页链上；受理口径若
    随页大小变，这些页就永远读不回来了。
    """
    content = public_body(cjk_value(520))
    raw = content.encode("utf-8")
    boundaries = [o for o in range(len(raw)) if (raw[o] & 0xC0) != 0x80]
    assert page_starts(content) != page_starts(content, LEGACY_PAGE_SIZE)
    for offset in (boundaries[0], boundaries[len(boundaries) // 2], boundaries[-1]):
        # 旧页链上的起点、以及任何一个码点边界，两种页大小下都照样读得出来。
        assert _excerpt(content, offset) == raw[offset:offset + PAGE_BYTES].decode(
            "utf-8", errors="ignore")
        assert _excerpt(content, offset, LEGACY_PAGE_SIZE) == raw[
            offset:offset + LEGACY_PAGE_SIZE].decode("utf-8", errors="ignore")
    # 旧页大小算出来的每一个页起点，在新页大小下仍然是合法 offset。
    for offset in page_starts(content, LEGACY_PAGE_SIZE):
        assert _excerpt(content, offset)


def test_the_reference_and_the_admission_digest_are_page_size_free():
    """引用 = ``canonical_hash(descriptor)`` + 字节 offset，描述符里没有页大小。"""
    effect = FakeEffect(effect_id=SOURCE_EFFECT, tool_name="read_file",
                        raw_call_id="call_00_" + "d" * 24, value=cjk_value(520))
    descriptor, content = source_content(
        Stack({SOURCE_EFFECT: effect}, ()).read_primary_effect_page_facts(RUN_ID, SOURCE_EFFECT),
        min_bytes=0)
    assert "page_size" not in descriptor and "page_count" not in descriptor
    # 升级前后同一个 offset 的引用逐字节相同——已持久化的引用全部照样解析。
    for offset in (0, 1024, 2046, 12278):
        assert reference(descriptor, offset) == PREFIX + canonical_hash(descriptor) + ":" + str(offset)
    assert canonical_hash(descriptor) == json.loads(
        legacy_af_summary(descriptor, content))["reference_id"].removeprefix(PREFIX).split(":")[0]


@pytest.mark.asyncio
async def test_a_page_recorded_before_the_upgrade_replays_byte_for_byte(tmp_path):
    """事件 AG 的重放兼容：``page_bytes=LEGACY_PAGE_SIZE`` 重算出升级前那一页。"""
    scenario = _scenario(tmp_path, lines=520)
    await _identities(scenario.path, scenario.rows)
    ref = reference(scenario.descriptor, 0)
    async with aiosqlite.connect(scenario.path) as db:
        db.row_factory = aiosqlite.Row
        args = {"reference_id": ref, "source_hash": scenario.descriptor["content_hash"]}
        fresh = await admitted_current_page(db=db, stack=scenario.stack, run=scenario.run,
            sdk_run_id=RUN_ID, page_effect=scenario.caller, arguments=args)
        old = await admitted_current_page(db=db, stack=scenario.stack, run=scenario.run,
            sdk_run_id=RUN_ID, page_effect=scenario.caller, arguments=args,
            page_bytes=LEGACY_PAGE_SIZE)
    assert len(fresh["content"].encode("utf-8")) == PAGE_BYTES == 4096
    assert len(old["content"].encode("utf-8")) == LEGACY_PAGE_SIZE == 1024
    # 准入身份完全一致；不同的只有「这一页多少字节」和由它决定的下一页。
    assert fresh["reference_id"] == old["reference_id"] == ref
    assert fresh["source"] == old["source"] and fresh["source_hash"] == old["source_hash"]
    assert fresh["next_reference_id"] != old["next_reference_id"]
    assert fresh["content"].startswith(old["content"])


def test_the_upgrade_cuts_the_incident_turn_from_forty_pages_to_ten():
    """事故的算术：40 003 B 的正文按 1 KiB 要翻 ~40 次，按 4 KiB 只要 10 次。"""
    content = public_body(cjk_value(405))
    assert 40_000 < len(content.encode("utf-8")) < 41_000
    before, after = page_starts(content, LEGACY_PAGE_SIZE), page_starts(content)
    assert len(before) >= 40 and len(after) <= 11
    assert len(before) >= 3 * len(after)
