"""Exact setup-only C08 historical document realizations.

These documents are assistant messages in an actual completed conversation,
not filesystem artifacts, TaskScope closures or Procedure adoption. No current
question or oracle is needed to construct them. The retained pipeline binds
their actual terminal ancestry to the original USER and public suppression.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class HistoricalDocument:
    kind: str
    predicate: str
    value: str
    user: str
    assistant: str


# The original setups specify the decision and carrier, but not a verbatim
# earlier conversation. These authored realizations add no date, participant,
# project identity, completion event or success evidence.
DOCUMENTS = MappingProxyType({
    'C08-15': HistoricalDocument(
        'retained-meeting-minutes', 'meeting_decision', '采购蓝色桌布',
        '会议决定：采购蓝色桌布。请整理为会议纪要。',
        '会议纪要\n决定：采购蓝色桌布。'),
    'C08-16': HistoricalDocument(
        'retained-project-checklist', 'project_decision', '省略校对',
        '项目决定：省略校对。请把这个决定列入检查列表。',
        '项目检查列表\n1. 确认按原决定省略校对。'),
})
