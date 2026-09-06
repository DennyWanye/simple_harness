"""C05 runner-only schedule contract; never a Provider or setup-authority input.

The four supported schedules are copied from the frozen 05-task script tables,
not their gold. Runtime event/phase wiring consumes Carver's public fact reader.
"""
from dataclasses import dataclass


SUPPORTED_CASES = frozenset({'C05-04', 'C05-09', 'C05-14', 'C05-20'})
_PREVIEW = 'candidate_preview_then_turn_terminal'
_UNMET = 'record_unmet_and_stop_no_rescue'
# Exact authored scripts. No target ID, expected answer, or selected label.
_SCRIPTS = {
    'C05-04': ((_PREVIEW, '选社区物品登记那个。'),),
    'C05-09': ((_PREVIEW, '选等待字体授权的家谱任务，先告诉我卡点。'),),
    'C05-14': ((_PREVIEW, '选需要核对座位的那项。'),),
    'C05-20': ((_PREVIEW, '我原想选排版，先别切换。'),
               ('assistant_turn_terminal_after_f1', '改选封面校对，以这条为准。')),
}


@dataclass(frozen=True)
class ScheduledFollowup:
    followup_id: str
    after_event: str
    fixture_action: str
    user_message: str
    on_unmet: str


def validate_schedule(case_id, raw):
    """Validate the separate compiler scheduler row before any setup dispatch.

    The compiler's NOT_IMPLEMENTED marker remains a source-era fact; this
    function neither changes it nor treats it as proof of runtime readiness.
    """
    if case_id not in SUPPORTED_CASES:
        raise ValueError('c05_session_case_requirements_not_implemented')
    if (type(raw) is not dict or set(raw) != {'scripted_followup', 'scheduler_state'}
            or raw['scheduler_state'] != 'NOT_IMPLEMENTED'
            or type(raw['scripted_followup']) is not list):
        raise ValueError('c05_exact_compiler_schedule_required')
    expected = tuple(ScheduledFollowup('f' + str(index), event, 'none', text, _UNMET)
        for index, (event, text) in enumerate(_SCRIPTS[case_id], 1))
    fields = set(ScheduledFollowup.__dataclass_fields__)
    rows = raw['scripted_followup']
    if (any(type(row) is not dict or set(row) != fields
            or any(type(value) is not str for value in row.values()) for row in rows)
            or tuple(ScheduledFollowup(**row) for row in rows) != expected):
        raise ValueError('c05_authored_followup_schedule_differs')
    return expected
