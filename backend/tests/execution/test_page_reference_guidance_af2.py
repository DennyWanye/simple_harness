"""事件 AF-2：引用**解析不了**时，拒绝必须说清是哪一种，并给得出替代品。

原生证据 `.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`
（`userdata/data/simple-harness-sdk/execution-v6.sqlite3`，Run
`product-sdk-71b4ca76…` 的两次 `context_page_in`）：

    {"reference_id":"primary-effect-page:v1:59f03e516e650241a6c151db6722aa8f…",
     "source_hash":"c9773807553dc8174a16b0a663d25f7f48c4a7b052e7661e88a6850f3e2b5430"}
    → failed  error_code=primary_effect_page_reference
              public_message="Requested primary page is unavailable."

引用少了 `":<offset>"` 尾巴，`split(":")` 只解出一段。摘要里印的
`reference_id` 本来就带 `:0`，模型却是**重构**而不是照抄。回执两个字段都没说少了
什么、也没说现在能用哪条引用，模型于是转去 `tool_search` 空转，T17 的预算就此打光
（见 `plans/2026-09-08-hm-to-a6/RUN-12-ATTEMPT.md` 的 AH-2 / AF-2）。

这一组用例钉住修复后的形态：三条引用解析失败分支各有结构化原因
（`page_reference_missing_offset` / `page_reference_unknown` /
`page_reference_stale`）与可执行的下一步，offset 拒绝另外公布有效范围，而稳定
**码**一个字符都没变（`primary_dependencies` 的重放比对因此原样成立）。
"""
import json
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.execution.current_tool_pages import (
    MAX_LISTED_REFERENCES, PREFIX, REFERENCE_UNAVAILABLE_CODE,
    PrimaryContextPageUnavailable, admitted_current_page, page_starts,
    reference, rejection_code_matches,
)
from deskpet.execution.primary_context_pages import (
    PREFIX as HISTORY_PREFIX, admitted_page,
)
from deskpet.task_scope.protocol import canonical_json
from deskpet.tools.context_page_in_tools import (
    PRIMARY_PAGE_PUBLIC_MESSAGE, ContextPageInStore, _primary_page_message,
    build_context_page_in_handler,
)

from tests.execution.test_control_result_bound import RUN_ID
from tests.execution.test_page_offset_guidance import _identities, _scenario, mid_codepoint

# 证据里那条引用的确切形态：前缀 + 64 位摘要，**没有** ":<offset>" 尾巴。
INCIDENT_DIGEST = "59f03e516e650241a6c151db6722aa8f4bb2251c4758ab0d8f5be21b2d757d64"


async def _call(scenario, reference_id, *, source_hash=None):
    """用一条任意的 ``reference_id`` 走真的准入路径（真 db、真权威事实）。"""
    await _identities(scenario.path, scenario.rows)
    async with aiosqlite.connect(scenario.path) as db:
        db.row_factory = aiosqlite.Row
        return await admitted_current_page(
            db=db, stack=scenario.stack, run=scenario.run, sdk_run_id=RUN_ID,
            page_effect=scenario.caller,
            arguments={"reference_id": reference_id,
                       "source_hash": source_hash or scenario.descriptor["content_hash"]})


async def _rejected(scenario, reference_id, *, source_hash=None):
    with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
        await _call(scenario, reference_id, source_hash=source_hash)
    return str(excinfo.value), excinfo.value.detail


# --------------------------------------------------------------------------
# 1. 事故的确切形态：丢掉 offset 尾巴
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_reference_without_its_offset_tail_says_what_is_missing(tmp_path):
    """T17 的那两次调用：摘要认得这条来源，缺的只是 ``":<offset>"``。"""
    scenario = _scenario(tmp_path, lines=520)
    digest = reference(scenario.descriptor, 0).removeprefix(PREFIX).split(":")[0]
    code, detail = await _rejected(scenario, PREFIX + digest)
    # 稳定码逐字不变——升级前记录的拒绝照样重放。
    assert code == "primary_effect_page_reference"
    assert detail["reason"] == "page_reference_missing_offset"
    assert "byte offset" in detail["next_step"]
    # 下一步必须是**可执行**的：给出来的那条引用真的读得回字节。
    assert detail["retry_reference_id"] == reference(scenario.descriptor, 0)
    page = await _call(scenario, detail["retry_reference_id"])
    assert page["ok"] and page["offset"] == 0


@pytest.mark.asyncio
async def test_an_unparseable_offset_tail_takes_the_same_branch(tmp_path):
    """``:abc`` / 多一个冒号 / 带正号：都解析不了，都给同一条可执行的下一步。"""
    scenario = _scenario(tmp_path, lines=520)
    digest = reference(scenario.descriptor, 0).removeprefix(PREFIX).split(":")[0]
    for tail in (":abc", ":0:0", ":+0", ":", ": 0"):
        code, detail = await _rejected(scenario, PREFIX + digest + tail)
        assert code == "primary_effect_page_reference", tail
        assert detail["reason"] == "page_reference_missing_offset", tail
        assert detail["retry_reference_id"] == reference(scenario.descriptor, 0), tail


# --------------------------------------------------------------------------
# 2. 引用根本不认得：列出本请求现在真正可用的那几条
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unknown_reference_lists_what_this_request_really_offers(tmp_path):
    scenario = _scenario(tmp_path, lines=520)
    code, detail = await _rejected(scenario, PREFIX + INCIDENT_DIGEST)
    assert code == "primary_effect_page_reference"
    assert detail["reason"] == "page_reference_unknown"
    # 清单来自权威重建的请求投影，不是从模型写的文本里抄回去的。
    assert detail["available_reference_ids"] == [reference(scenario.descriptor, 0)]
    assert detail["references_available"] == 1
    page = await _call(scenario, detail["available_reference_ids"][0])
    assert page["ok"]


@pytest.mark.asyncio
async def test_the_listed_references_are_bounded(tmp_path):
    """可用引用再多，也只列前 ``MAX_LISTED_REFERENCES`` 条，且计数说出全量。"""
    scenario = _scenario(tmp_path, lines=520)
    available = {"d%02d" % i: PREFIX + ("%02d" % i) * 32 + ":0"
                 for i in range(MAX_LISTED_REFERENCES + 4)}
    from deskpet.execution.current_tool_pages import _listed_references
    listed = _listed_references(available)
    assert len(listed) == MAX_LISTED_REFERENCES == 3
    # 最新的在前：``verify_request`` 按消息顺序重建，尾部就是本请求最新那份分页结果。
    assert listed == list(available.values())[-3:][::-1]
    assert _listed_references({}) == []


# --------------------------------------------------------------------------
# 3. 引用形态合法但这条页已经不在了：给替代的最新页引用
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_stale_reference_carries_the_replacement_reference(tmp_path):
    scenario = _scenario(tmp_path, lines=520)
    code, detail = await _rejected(scenario, PREFIX + "f" * 64 + ":0")
    assert code == REFERENCE_UNAVAILABLE_CODE == "primary_page_reference_unavailable"
    assert detail["reason"] == "page_reference_stale"
    assert detail["available_reference_ids"] == [reference(scenario.descriptor, 0)]
    # 换一步走真的走得通。
    assert (await _call(scenario, detail["available_reference_ids"][0]))["ok"]
    # 升级前记录的旧码仍与它重放一致（事件 AF 的兼容口径没有被这次改动动过）。
    assert rejection_code_matches("primary_effect_page_not_admitted", REFERENCE_UNAVAILABLE_CODE)


# --------------------------------------------------------------------------
# 4. offset 越界：公布有效范围
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_out_of_range_offset_publishes_the_valid_range(tmp_path):
    scenario = _scenario(tmp_path, lines=520)
    content_bytes = len(scenario.content.encode("utf-8"))
    for offset, reason in ((content_bytes + 1024, "offset_past_end"),
                           (content_bytes, "offset_past_end")):
        code, detail = await _rejected(scenario, reference(scenario.descriptor, offset))
        assert code == "primary_page_offset_invalid"
        assert detail["reason"] == reason
        # 有效范围是**受理口径**的完整边界；``valid_offsets`` 是它里面那个可执行子集。
        assert detail["valid_offset_range"] == [0, content_bytes - 1]
        assert detail["content_bytes"] == content_bytes
        assert detail["valid_offsets"][0] == 0
        assert max(detail["valid_offsets"]) <= detail["valid_offset_range"][1]
    # 码点中间的 offset 在范围内，所以范围本身不解释它——原因码仍然分得清两者。
    bad = mid_codepoint(scenario.content, 2048, 4096)
    code, detail = await _rejected(scenario, reference(scenario.descriptor, bad))
    assert code == "primary_page_offset_invalid"
    assert detail["reason"] == "offset_not_on_character_boundary"
    assert detail["valid_offset_range"][0] <= bad <= detail["valid_offset_range"][1]
    # 每一个印出来的页起点都真的读得回来。
    for offset in detail["valid_offsets"]:
        assert (await _call(scenario, reference(scenario.descriptor, offset)))["ok"]
    assert detail["page_count"] == len(page_starts(scenario.content))


# --------------------------------------------------------------------------
# 5. 提示的纪律：不回显模型文本、不改变结果、有界、确定
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_detail_never_echoes_the_model_written_reference(tmp_path):
    """``detail`` 只放权威推导出的纯数据——模型写的字符串一个字都不许回显。"""
    scenario = _scenario(tmp_path, lines=520)
    marker = "MODEL-WRITTEN-CANARY"
    code, detail = await _rejected(scenario, PREFIX + marker)
    assert code == "primary_effect_page_reference"
    assert marker not in canonical_json(detail)
    assert marker not in _primary_page_message(detail)


@pytest.mark.asyncio
async def test_an_unreadable_authority_never_changes_the_outcome(tmp_path):
    """提示读不出来时退回空清单，绝不把这次调用变成别的错（事件 B 的纪律）。"""
    scenario = _scenario(tmp_path, lines=520)

    class Blind:
        def read_primary_effect_page_facts(self, *args, **kwargs):
            raise RuntimeError("authority unavailable")

    blind = SimpleNamespace(path=scenario.path, rows=scenario.rows, stack=Blind(),
                            descriptor=scenario.descriptor, content=scenario.content,
                            caller=scenario.caller, run=scenario.run)
    code, detail = await _rejected(blind, PREFIX + INCIDENT_DIGEST)
    assert code == "primary_effect_page_reference"
    assert detail["reason"] == "page_reference_unknown"
    assert detail["available_reference_ids"] == [] and detail["references_available"] == 0


@pytest.mark.asyncio
async def test_reference_rejection_detail_is_deterministic_and_fits_the_public_message(tmp_path):
    scenario = _scenario(tmp_path, lines=520)
    rendered = set()
    for _ in range(3):
        _, detail = await _rejected(scenario, PREFIX + INCIDENT_DIGEST)
        message = _primary_page_message(detail)
        # ``primary_dependencies`` 靠这个前缀认出主页面的确定性拒绝。
        assert message.startswith(PRIMARY_PAGE_PUBLIC_MESSAGE)
        # 上游 ``_MAX_HANDLER_PUBLIC_MESSAGE`` 是 2048：超了就整条详情被丢掉。
        assert len(message) <= 2048
        assert json.loads(message[len(PRIMARY_PAGE_PUBLIC_MESSAGE) + 1:]) == detail
        rendered.add(message)
    assert len(rendered) == 1


@pytest.mark.asyncio
async def test_the_worst_case_offset_detail_still_fits_the_public_message(tmp_path):
    """页起点清单最长的那种正文，加上新增的有效范围，仍然带得动详情。"""
    scenario = _scenario(tmp_path, lines=4000)
    starts = page_starts(scenario.content)
    assert len(starts) > 16, "取样点必须走到「清单被截断」的分支"
    _, detail = await _rejected(
        scenario, reference(scenario.descriptor, len(scenario.content.encode("utf-8")) + 1))
    message = _primary_page_message(detail)
    assert message.startswith(PRIMARY_PAGE_PUBLIC_MESSAGE) and len(message) <= 2048
    assert json.loads(message[len(PRIMARY_PAGE_PUBLIC_MESSAGE) + 1:])["valid_offset_range"] == [
        0, len(scenario.content.encode("utf-8")) - 1]


# --------------------------------------------------------------------------
# 6. 历史页引用（另一个前缀）：同一套原因词汇
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_history_page_reference_without_an_offset_tail_is_explained():
    """解析在碰任何权威面之前完成，所以这条分支不需要 db/stack。"""
    for bad in (HISTORY_PREFIX + "a" * 64, HISTORY_PREFIX + "a" * 64 + ":x",
                HISTORY_PREFIX + "short:0", "recall-item:m1:1"):
        with pytest.raises(PrimaryContextPageUnavailable) as excinfo:
            await admitted_page(db=None, stack=None, run=None, sdk_run_id=None, start=None,
                                arguments={"reference_id": bad, "source_hash": "0" * 64})
        assert str(excinfo.value) == "primary_page_reference_invalid", bad
        detail = excinfo.value.detail
        assert detail["reason"] == "page_reference_unknown", bad
        assert "recall-item" in detail["next_step"] and HISTORY_PREFIX in detail["next_step"]
        # 同一条纪律：模型写的引用一个字都不回显。
        assert bad not in canonical_json(detail)


# --------------------------------------------------------------------------
# 7. 临时引用表（第三条解析失败分支）：过期 / 召回片段 / 压根不是引用
# --------------------------------------------------------------------------

def _handler(store, runtime):
    return build_context_page_in_handler(store, execution_context_getter=lambda: runtime)


@pytest.mark.asyncio
async def test_an_expired_request_scoped_reference_says_so_and_offers_a_live_one():
    store = ContextPageInStore()
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    live = store.put(kind="memory_l3", source="memory:42", content="live body",
                     session_id="s1", request_id="r1", scope_id="p1")
    result = json.loads(await _handler(store, runtime)(
        {"reference_id": "a" * 32, "source_hash": "0" * 64}, "task"))
    # 稳定码逐字不变，可重试性也不变。
    assert result["ok"] is False and result["error"] == "reference_stale"
    assert result["retriable"] is True
    assert result["detail"]["reason"] == "page_reference_stale"
    assert result["detail"]["available_reference_ids"] == [live.reference_id]


@pytest.mark.asyncio
async def test_a_recall_fragment_ref_is_named_instead_of_being_called_stale():
    """工具说明点过名的那种误用（F07 的老形态）现在被直接说破。"""
    store = ContextPageInStore()
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    result = json.loads(await _handler(store, runtime)(
        {"reference_id": "recall-item:mem-7:1", "source_hash": "0" * 64}, "task"))
    assert result["error"] == "reference_stale"
    assert result["detail"]["reason"] == "page_reference_unknown"
    assert "recall fragment ref" in result["detail"]["next_step"]
    assert result["detail"]["available_reference_ids"] == []


@pytest.mark.asyncio
async def test_a_string_that_was_never_a_reference_says_where_references_come_from():
    store = ContextPageInStore()
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    result = json.loads(await _handler(store, runtime)(
        {"reference_id": "the checklist file", "source_hash": "0" * 64}, "task"))
    assert result["detail"]["reason"] == "page_reference_unknown"
    assert "copied verbatim" in result["detail"]["next_step"]


@pytest.mark.asyncio
async def test_the_offered_references_never_cross_a_session_request_or_scope():
    """清单的过滤口径与 ``is_active`` 的准入判据完全相同。"""
    store = ContextPageInStore()
    mine = store.put(kind="memory_l3", source="m", content="mine",
                     session_id="s1", request_id="r1", scope_id="p1")
    for session, request, scope in (("s2", "r1", "p1"), ("s1", "r2", "p1"), ("s1", "r1", "p2")):
        store.put(kind="memory_l3", source="m", content="theirs",
                  session_id=session, request_id=request, scope_id=scope)
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    result = json.loads(await _handler(store, runtime)(
        {"reference_id": "b" * 32, "source_hash": "0" * 64}, "task"))
    assert result["detail"]["available_reference_ids"] == [mine.reference_id]
    # 上界与主页面一侧同值；过期的条目在列之前就被清掉。
    extra = [store.put(kind="memory_l3", source="m", content=str(i), session_id="s1",
                       request_id="r1", scope_id="p1").reference_id for i in range(5)]
    listed = store.live_reference_ids(session_id="s1", request_id="r1", scope_id="p1")
    assert len(listed) == MAX_LISTED_REFERENCES == 3
    # 同一口径：最新发布的在前，最旧的（``mine``）被挤出清单。
    assert listed == extra[-3:][::-1]
    assert mine.reference_id not in listed


@pytest.mark.asyncio
async def test_an_unchanged_envelope_is_still_produced_for_the_other_rejections():
    """只有"引用解析不了"这条分支多了 ``detail``，其余信封一个字节没动。"""
    store = ContextPageInStore()
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    ref = store.put(kind="memory_l3", source="m", content="body",
                    session_id="s1", request_id="r1", scope_id="p1")
    forged = json.loads(await _handler(store, runtime)(
        {"reference_id": ref.reference_id, "source_hash": "0" * 64}, "task"))
    assert forged == {"ok": False, "error": "reference_hash_mismatch", "retriable": False}
    runtime.request_id = "other"
    denied = json.loads(await _handler(store, runtime)(
        {"reference_id": ref.reference_id, "source_hash": ref.source_hash}, "task"))
    assert denied == {"ok": False, "error": "reference_scope_denied", "retriable": False}


# --------------------------------------------------------------------------
# 8. 事件 AH-2：同一轮里 write_file 的 tool_failed 已由事件 AH 通用化覆盖
# --------------------------------------------------------------------------

def test_the_same_turns_write_file_failure_is_already_attributable():
    """T17 的 ``write_file`` 拒因（``/ws`` 只读）现在到得了模型与日志。

    证据里那两次调用的实参是 ``{"path":"/ws/Task-488d567e8533/goal_…txt",
    "content":"目标条款 0000：…"}``，回执只有
    ``error_code=tool_failed`` / ``public_message="Tool execution failed."``。
    事件 AH 把 ``sdk_adapters.tools._result`` 的默认分支改成「异常类名 + 净化后的
    自然语言」之后，同一个信封已经能被归因——AH-2 因此就地结清，本次不再改
    ``write_file`` 自己（见 DECISION-AH2-AF2.md 第 2 节）。
    """
    from deskpet.sdk_adapters.tools import NO_REASON_FAILURE_MESSAGE, failure_log_reason
    from deskpet.tools.os_tools.write_file import write_file

    raw = write_file({"path": "/ws/Task-488d567e8533/goal_说明.txt", "content": "x" * 32}, "t")
    envelope = json.loads(raw)
    assert envelope["ok"] is False
    reason = failure_log_reason(raw)
    assert reason not in ("-", "") and reason != NO_REASON_FAILURE_MESSAGE
    # 既定口径：稳定码与日志字段永不含路径。
    assert "/ws/Task-488d567e8533" not in reason and "<path>" in reason
    assert reason.startswith("OSError")
