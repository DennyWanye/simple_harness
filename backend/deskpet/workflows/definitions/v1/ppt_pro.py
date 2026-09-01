# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Version 1 durable PPT Pro workflow graph."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from ....tools import ppt_outline_store, ppt_tools
from ...contracts import (
    ChannelSpec,
    JsonType,
    JsonValue,
    ReducerKind,
    StatePatch,
    WorkflowContext,
    WorkflowState,
    validate_json_value,
)
from ...control import workflow_interrupt
from ...definition import (
    END_NODE,
    CompiledWorkflow,
    ConditionalEdge,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from .. import ppt_pro_nodes as nodes
from . import deep_research as research_graph

WORKFLOW_NAME = "ppt_pro"
WORKFLOW_VERSION = "v1"
STATE_SCHEMA_VERSION = 1
MAX_OUTLINE_REVISIONS = 2
MAX_VISUAL_REVISIONS = 2
MAX_FULL_PAGE_PROVIDER_ATTEMPTS = 3
MAX_IMAGE_ITERATIONS = 20 * (
    MAX_FULL_PAGE_PROVIDER_ATTEMPTS + MAX_VISUAL_REVISIONS + 1
)
RESEARCH_CORE_NAMESPACE = "ppt_pro/research_core"


_GLOBAL_PAGE_SCOPE_RE = re.compile(
    r"(?:逐页|每(?:一|个|张)?(?:页|页面|幻灯片)|"
    r"所有(?:页|页面|幻灯片)|全部(?:页|页面|幻灯片)|"
    r"各(?:页|页面|幻灯片)|"
    r"(?<![a-z0-9_])(?:all|each|every)\s+(?:the\s+)?"
    r"(?:slides?|pages?)(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_GLOBAL_VALIDATION_ACTION_RE = re.compile(
    r"(?:检查|验证|验收|校验|核对|审查|确保|保证|"
    r"(?<![a-z0-9_])(?:validat|check|inspect|verif|review|audit|ensure)"
    r"[a-z]*(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_GLOBAL_DECK_ELEMENT_SCOPE_RE = re.compile(
    r"(?:所有|全部|各|每个)(?:的)?元素|"
    r"(?:所有|全部|各|每个)(?:的)?(?:标题|正文|文字|文本)"
    r"(?:[\s、，,和与及/]*(?:标题|正文|文字|文本|表格|图表|元素))*|"
    r"(?:标题|正文|文字|文本)"
    r"(?:[\s、，,和与及/]*(?:标题|正文|文字|文本|表格|图表|元素))*"
    r"\s*(?:所有|全部|均|都)|"
    r"(?<![a-z0-9_])(?:all|every|each)\s+(?:the\s+)?"
    r"(?:titles?|body\s+text|text|elements?)"
    r"(?:[\s,]*(?:and\s+)?"
    r"(?:titles?|body\s+text|text|tables?|charts?|elements?))*"
    r"(?![a-z0-9_])",
    flags=re.IGNORECASE,
)
_GLOBAL_EDITABILITY_ACTION_RE = re.compile(
    r"(?:可|能)编辑|(?<![a-z0-9_])editable(?![a-z0-9_])",
    flags=re.IGNORECASE,
)
_EDITABILITY_ONLY_MARKER_RE = re.compile(
    r"(?:保持|维持|现有|已有|当前|所有|全部|每个|均|都)"
    r"[\s\S]{0,64}(?:(?:可|能)编辑)|"
    r"(?:表格|图表|(?<![a-z0-9_])(?:tables?|charts?)(?![a-z0-9_]))"
    r"[\s\S]{0,32}(?:保持|维持|均|都|需要|必须|must)?"
    r"[\s\S]{0,16}(?:(?:可|能)编辑|"
    r"(?<![a-z0-9_])editable(?![a-z0-9_]))|"
    r"(?<![a-z0-9_])(?:keep|remain|stay|existing|current|all|every|each)"
    r"[\s\S]{0,64}(?:editable)(?![a-z0-9_])",
    flags=re.IGNORECASE,
)
_CLAUSE_BOUNDARY_RE = re.compile(
    r"\r?\n|[；;。.!！？，,?:：]|"
    r"\s+(?=(?:后续(?:还)?(?:需要)?|接下来|然后)\s*|"
    r"(?:then|afterwards)(?:\s+|$))",
    flags=re.IGNORECASE,
)
_OBJECT_ACTION_BOUNDARY_RE = re.compile(
    r"\s+(?=(?:and\s+)?"
    r"(?:add|insert|include|create|use|render|show|display|contain)"
    r"(?:\s+|$))",
    flags=re.IGNORECASE,
)
_CONTENT_LABEL_BEFORE_COLON_RE = re.compile(
    r"(?:(?:主题|标题|内容)(?:为|说明)?|topic|title|content)\s*$",
    flags=re.IGNORECASE,
)
_PRESERVED_COPY_MARKER_RE = re.compile(
    r"(?:必须|需要|请)?\s*(?:完整|原样|逐字)?保留(?:以下)?\s*\d*\s*条?\s*"
    r"(?:要点|文字|文案|内容)|"
    r"(?:原样|完整|逐字)保留\s*(?=[:：\-–—])|"
    r"(?:正文|文案|内容)(?:中)?(?:写明|写出|包含)\s*|"
    r"(?<![a-z0-9_])(?:must|required\s+to|please)?\s*"
    r"(?:preserve|retain|keep)\s+(?:exactly\s+)?(?:the\s+)?"
    r"(?:following(?:\s+(?:copy|text|wording|content))?|"
    r"(?:this\s+)?exact\s+(?:sentence|copy|text|wording|content)|"
    r"(?:copy|text|wording|content)\s*(?=[:：\-–—])|"
    r"(?=[:：\-–—]))"
    r"(?![a-z0-9_])",
    flags=re.IGNORECASE,
)
_QUOTED_TEXT_RE = re.compile(
    r"“[^”]*”|‘[^’]*’|【[^】]*】|「[^」]*」|"
    r"\"[^\"]*\"|'[^']*'",
    flags=re.IGNORECASE,
)
_QUOTED_REQUIREMENT_CONTEXT_RE = re.compile(
    r"(?:要求|规则|规定|约束|限制条件|务必遵守|"
    r"(?<![a-z0-9_])(?:requirement|rule|constraint|"
    r"follow\s+(?:this|the)\s+rule)(?![a-z0-9_]))"
    r"(?:\s*(?:is|为|是))?[\s:：\-‐‑‒–—―−]*$",
    flags=re.IGNORECASE,
)
_AMBIGUITY_NOTE_RE = re.compile(
    r"(?:混淆|误判|误识别|错判|"
    r"(?<![a-z0-9_])(?:classif|misidentif)[a-z]*"
    r"[\s\S]{0,24}(?<![a-z0-9_])as(?![a-z0-9_])|"
    r"(?<![a-z0-9_])confus[a-z]*[\s\S]{0,24}"
    r"(?<![a-z0-9_])(?:with|and|as)(?![a-z0-9_])|"
    r"(?<![a-z0-9_])(?:chart|table)\s*/\s*(?:chart|table)"
    r"[\s\S]{0,16}(?<![a-z0-9_])confus[a-z]*(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_STRUCTURE_ACTION_RE = re.compile(
    r"(?:使用|采用|添加|加入|插入|放置|创建|制作|绘制|呈现|展示|改用|"
    r"(?<![a-z0-9_])(?:use|add|include|insert|create|make|render|"
    r"show|display|contain|using)(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_OBJECT_TOKEN = (
    r"柱状图|折线图|饼图|图表|表格|"
    r"(?<![a-z0-9_])(?:bar(?:\s+|-)+chart|line(?:\s+|-)+chart|"
    r"pie(?:\s+|-)+chart|chart|table)(?![a-z0-9_])"
)
_EXPLICIT_OBJECT_PREDICATE_RE = re.compile(
    rf"(?:必须|务必|需要)(?:是|为|采用)\s*(?:原生|可编辑|原生可编辑)?"
    rf"[\s\S]{{0,12}}(?:{_OBJECT_TOKEN})|"
    rf"(?<![a-z0-9_])must\s+be\s+(?:an?\s+)?"
    rf"(?:native\s+|editable\s+|natively\s+editable\s+)*(?:{_OBJECT_TOKEN})",
    flags=re.IGNORECASE,
)
_NEGATED_OBJECT_RE = re.compile(
    r"(?:不能是|不得是|并非|不是|不要(?:把|将|被)?|"
    r"无需(?:使用)?|不需要|不应(?:当作|视为|是)?|禁止(?:使用)?|"
    r"避免(?:使用)?|不推荐(?:使用)?|取消|移除|删除|"
    r"(?<![a-z0-9_])(?:without|no|not|never|"
    r"remove(?:\s+the)?|avoid(?:\s+using)?|instead\s+of|"
    r"(?:do\s+not|don't|must\s+not|cannot|can't)(?:\s+(?:use|be))?)"
    r"(?![a-z0-9_]))"
    r"[\s\S]{0,12}?"
    rf"(?P<object>{_OBJECT_TOKEN})",
    flags=re.IGNORECASE,
)
_OBJECT_THEN_NEGATED_RE = re.compile(
    rf"(?P<object>{_OBJECT_TOKEN})"
    r"\s*(?:不需要|不要|不推荐|应避免|禁止使用|取消|移除|删除|"
    r"(?<![a-z0-9_])(?:is\s+not|isn't)\s+(?:needed|required)"
    r"(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_EXPLICIT_IMAGE_MODE_FALSE_RE = re.compile(
    r"(?:(?<![a-z0-9_])image[_\s-]*mode\s*"
    r"(?:(?:=|:|：)\s*|is\s+)?"
    r"(?:false|off|0|禁用|关闭)(?![a-z0-9_])|"
    r"(?:(?:不要|请勿|禁止|无需)(?:使用|生成|放置)?|"
    r"不(?:要)?使用|不含|不放|不生成)"
    r"(?:任何)?(?:图片|图像|配图)(?!\s*(?:栅格化|扁平化))|"
    r"无(?:图片|图像|配图)|"
    r"无图(?=\s*(?:ppt|演示|幻灯片|文稿|$))|"
    r"(?<![a-z0-9_])image(?:\s+|-)+free(?![a-z0-9_])|"
    r"(?<![a-z0-9_])(?:do\s+not|don't)\s+"
    r"(?:use|include|add|show|generate)\s+(?:any\s+)?images?"
    r"(?![a-z0-9_])|"
    r"(?<![a-z0-9_])(?:no|without)\s+(?:any\s+)?images?"
    r"(?![a-z0-9_])"
    r"(?!(?:[-‐‑‒–—―−\s]+based)?[-‐‑‒–—―−\s]+"
    r"(?:rasteri[sz]|flatten)[a-z]*))",
    flags=re.IGNORECASE,
)
_NEGATED_SETTING_PREFIX_RE = re.compile(
    r"(?:不要(?:设置|将|生成|创建|制作|做|交付)?|"
    r"请勿(?:生成|创建|制作|做)?|不能|不得|不(?:接受|允许)|"
    r"(?:避免|禁止)(?:生成|创建|制作)?|"
    r"别(?:设置)?|勿(?:设置)?|不应(?:当)?设置|"
    r"(?:不是|并非|并不是|不代表)\s*|"
    r"(?<![a-z0-9_])(?:do\s+not|don't|not|never|avoid|reject|refuse)\s+"
    r"(?:(?:set|make)(?![a-z0-9_])|"
    r"\s*(?=(?:without|image(?:\s+|-)+free)\b)))\s*$",
    flags=re.IGNORECASE,
)
_ADDITIONAL_NEGATED_OBJECT_RE = re.compile(
    rf"\s*(?:或|和|与|及|以及|、|/|(?<![a-z0-9_])(?:or|and)(?![a-z0-9_]))"
    rf"\s*(?P<object>{_OBJECT_TOKEN})",
    flags=re.IGNORECASE,
)
_RASTERIZATION_TERM_RE = re.compile(
    r"(?:栅格化|扁平化|"
    r"(?<![a-z0-9_])(?:rasteri[sz]|flatten)[a-z]*(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_NATIVE_EDITABILITY_GUARD_RE = re.compile(
    r"(?:保持(?:为)?原生可编辑|不得(?:将|把)?[\s\S]{0,24}?"
    r"(?:转(?:换)?(?:成)?|变成|当作)(?:图片|图像)|"
    r"可编辑[\s\S]{0,24}(?:不|不得|无需)[\s\S]{0,12}?"
    r"(?:转(?:换)?(?:成)?|变成|当作)(?:图片|图像)|"
    r"(?:不能|不得|不应)(?:当作)?是?(?:图片|图像)|"
    r"(?<![a-z0-9_])(?:remain|keep|stay)[\s\S]{0,48}?"
    r"(?:native(?:ly)?[\s\S]{0,12})?editable|"
    r"(?<![a-z0-9_])editable[\s\S]{0,24}"
    r"(?:rather\s+than|instead\s+of|not|never)[\s\S]{0,12}images?|"
    r"(?<![a-z0-9_])(?:must\s+not|should\s+not|do\s+not|don't|never)"
    r"[\s\S]{0,24}(?:convert|turn|render)[a-z]*\s+"
    r"(?:\s+(?:them|it|these|those))?\s+"
    r"(?:into|to|as)\s+(?:an?\s+)?images?(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)
_NO_NEW_OBJECT_RE = re.compile(
    r"(?:不新增|(?:不要求|无需|不要|不必|不得)[\s\S]{0,12}"
    r"(?:新增|添加|创建))|"
    r"(?<![a-z0-9_])(?:do\s+not|don't|need\s+not|not\s+required\s+to)"
    r"[\s\S]{0,16}(?:add|create|include)(?![a-z0-9_])",
    flags=re.IGNORECASE,
)
_RASTERIZATION_NEGATION_RE = re.compile(
    r"(?:不要|请勿|禁止|不得|不能|避免|不应|并非|不是|均不得|"
    r"(?<![a-z0-9_])(?:no|not|never|neither|without|"
    r"must\s+not|should\s+not|do\s+not|don't|cannot|can't)"
    r"(?![a-z0-9_]))",
    flags=re.IGNORECASE,
)


def _global_validation_tail_start(segment: str) -> int | None:
    """Return the first clause that is clearly a deck-wide instruction."""

    for scope_match in _GLOBAL_DECK_ELEMENT_SCOPE_RE.finditer(segment):
        if _GLOBAL_EDITABILITY_ACTION_RE.search(
            segment[scope_match.start():scope_match.end() + 96]
        ):
            return scope_match.start()

    boundaries = list(_CLAUSE_BOUNDARY_RE.finditer(segment))
    for boundary_index, boundary in enumerate(boundaries):
        if (
            boundary.group() in {":", "："}
            and _CONTENT_LABEL_BEFORE_COLON_RE.search(segment[:boundary.start()])
        ):
            continue
        clause_end = (
            boundaries[boundary_index + 1].start()
            if boundary_index + 1 < len(boundaries)
            else len(segment)
        )
        clause = segment[boundary.end():clause_end]
        clause_has_scope = _GLOBAL_PAGE_SCOPE_RE.search(clause) is not None
        clause_has_action = _GLOBAL_VALIDATION_ACTION_RE.search(clause) is not None
        clause_has_global_elements = (
            _GLOBAL_DECK_ELEMENT_SCOPE_RE.search(clause) is not None
        )
        clause_has_editability = (
            _GLOBAL_EDITABILITY_ACTION_RE.search(clause) is not None
        )
        if clause_has_global_elements and clause_has_editability:
            return boundary.start()
        if clause_has_scope and clause_has_action:
            return boundary.start()
        if boundary_index + 1 < len(boundaries):
            next_boundary = boundaries[boundary_index + 1]
            next_clause_end = (
                boundaries[boundary_index + 2].start()
                if boundary_index + 2 < len(boundaries)
                else len(segment)
            )
            next_clause = segment[next_boundary.end():next_clause_end]
            if (
                clause_has_scope
                and _GLOBAL_VALIDATION_ACTION_RE.search(next_clause)
            ) or (
                clause_has_action
                and _GLOBAL_PAGE_SCOPE_RE.search(next_clause)
            ):
                return boundary.start()
    return None


def _requirement_clauses(segment: str) -> list[str]:
    """Keep instructions while excluding quoted slide copy and diagnostics."""

    result: list[str] = []
    copy_mode = False
    skip_first_copy_clause = False
    for raw_clause in _CLAUSE_BOUNDARY_RE.split(segment):
        clause = raw_clause
        copy_marker = _PRESERVED_COPY_MARKER_RE.search(clause)
        if copy_marker is not None:
            prefix = clause[:copy_marker.start()]
            if prefix.strip():
                result.append(prefix)
            copy_mode = True
            skip_first_copy_clause = True
            continue
        if copy_mode and skip_first_copy_clause:
            if not clause.strip():
                continue
            skip_first_copy_clause = False
            continue
        has_structure_action = _STRUCTURE_ACTION_RE.search(clause) is not None
        if copy_mode and not has_structure_action:
            continue
        if _AMBIGUITY_NOTE_RE.search(clause) and not has_structure_action:
            continue
        if clause.strip():
            result.append(clause)
    return result


def _is_pure_non_rasterization_clause(clause: str) -> bool:
    """Do not mistake an editability guard for a request to add an object."""

    return (
        re.search(_OBJECT_TOKEN, clause, flags=re.IGNORECASE) is not None
        and (
            (
                _RASTERIZATION_TERM_RE.search(clause) is not None
                and _RASTERIZATION_NEGATION_RE.search(clause) is not None
            )
            or _NATIVE_EDITABILITY_GUARD_RE.search(clause) is not None
            or _NO_NEW_OBJECT_RE.search(clause) is not None
        )
        and (
            _STRUCTURE_ACTION_RE.search(clause) is None
            or _NO_NEW_OBJECT_RE.search(clause) is not None
        )
    )


def _is_editability_only_clause(clause: str) -> bool:
    """An existing-object editability guard must not create a new object."""

    return (
        re.search(_OBJECT_TOKEN, clause, flags=re.IGNORECASE) is not None
        and _GLOBAL_EDITABILITY_ACTION_RE.search(clause) is not None
        and _EDITABILITY_ONLY_MARKER_RE.search(clause) is not None
        and _STRUCTURE_ACTION_RE.search(clause) is None
        and _EXPLICIT_OBJECT_PREDICATE_RE.search(clause) is None
    )


def _without_copy_quotes(topic: str) -> str:
    """Quoted slide copy is content, not a deck-level control instruction."""

    pieces: list[str] = []
    cursor = 0
    for match in _QUOTED_TEXT_RE.finditer(topic):
        prefix = topic[max(0, match.start() - 96):match.start()]
        if _QUOTED_REQUIREMENT_CONTEXT_RE.search(prefix) is not None:
            continue
        pieces.append(topic[cursor:match.start()])
        pieces.append(" ")
        cursor = match.end()
    pieces.append(topic[cursor:])
    return "".join(pieces)


def _requirement_from_object(object_text: str) -> str:
    normalized = object_text.casefold()
    if "柱状图" in normalized or re.search(
        r"(?<![a-z0-9_])bar(?:\s+|-)+chart(?![a-z0-9_])", normalized
    ):
        return "chart:bar"
    if "折线图" in normalized or re.search(
        r"(?<![a-z0-9_])line(?:\s+|-)+chart(?![a-z0-9_])", normalized
    ):
        return "chart:line"
    if "饼图" in normalized or re.search(
        r"(?<![a-z0-9_])pie(?:\s+|-)+chart(?![a-z0-9_])", normalized
    ):
        return "chart:pie"
    if "图表" in normalized or re.search(
        r"(?<![a-z0-9_])chart(?![a-z0-9_])", normalized
    ):
        return "chart"
    return "table"


def _remove_requirement(merged: list[str], requirement: str) -> None:
    if requirement == "chart":
        merged[:] = [item for item in merged if not item.startswith("chart")]
    elif requirement in merged:
        merged.remove(requirement)


def _add_requirement(merged: list[str], requirement: str) -> None:
    if requirement == "chart" and any(
        existing.startswith("chart:") for existing in merged
    ):
        return
    if requirement.startswith("chart:") and "chart" in merged:
        merged.remove("chart")
    if requirement not in merged:
        merged.append(requirement)


def _explicit_image_mode_is_false(topic: str) -> bool:
    instruction_source = _without_copy_quotes(topic)
    for instruction in _requirement_clauses(instruction_source):
        for match in _EXPLICIT_IMAGE_MODE_FALSE_RE.finditer(instruction):
            prefix = instruction[max(0, match.start() - 80):match.start()]
            match_text = match.group(0).lstrip().casefold()
            if match_text.startswith("without") and re.search(
                r"(?<![a-z0-9_])"
                r"(?:(?:do|should|must|can|is|are|was|were)\s+not|"
                r"don't|can't|cannot|shouldn't|wouldn't|couldn't|"
                r"mustn't|won't|not|never|avoid|refuse(?:d|s)?)"
                r"(?![a-z0-9_])[\s\S]{0,64}$",
                prefix,
                flags=re.IGNORECASE,
            ):
                continue
            if (
                match_text.startswith("without")
                and re.search(
                    r"^\s*(?:is|are|was|were)?\s*"
                    r"(?:not\s+(?:acceptable|allowed|permitted)|"
                    r"unacceptable|forbidden)",
                    instruction[match.end():match.end() + 64],
                    flags=re.IGNORECASE,
                )
            ):
                continue
            if not _NEGATED_SETTING_PREFIX_RE.search(prefix):
                return True
    return False


def _slide_requirements_from_topic(topic: str) -> dict[str, list[str]]:
    """Freeze explicit per-page native-object requirements from the request."""

    matches = list(
        re.finditer(
            r"(?:第\s*(?P<zh_page>\d+)\s*页|"
            r"(?<![a-z0-9_])(?:slide|page)\s*(?P<en_page>\d+)"
            r"(?![a-z0-9_]))",
            topic,
            flags=re.IGNORECASE,
        )
    )
    requirements: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        page = str(int(match.group("zh_page") or match.group("en_page")))
        segment_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(topic)
        )
        segment = topic[match.end():segment_end]
        global_tail_start = _global_validation_tail_start(segment)
        if global_tail_start is not None:
            segment = segment[:global_tail_start]
        merged = requirements.setdefault(page, [])
        base_clauses = _requirement_clauses(segment)
        has_explicit_object_creation = any(
            _STRUCTURE_ACTION_RE.search(clause) is not None
            or _EXPLICIT_OBJECT_PREDICATE_RE.search(clause) is not None
            for clause in base_clauses
        )
        if (
            _is_pure_non_rasterization_clause(segment)
            and not has_explicit_object_creation
        ):
            if not merged:
                requirements.pop(page, None)
            continue
        for base_clause in base_clauses:
            for clause in _OBJECT_ACTION_BOUNDARY_RE.split(base_clause):
                if (
                    _is_pure_non_rasterization_clause(clause)
                    or _is_editability_only_clause(clause)
                ):
                    continue
                negated_matches = [
                    *_NEGATED_OBJECT_RE.finditer(clause),
                    *_OBJECT_THEN_NEGATED_RE.finditer(clause),
                ]
                additionally_negated: list[tuple[int, int, str]] = []
                for negated_match in negated_matches:
                    _remove_requirement(
                        merged,
                        _requirement_from_object(negated_match.group("object")),
                    )
                    cursor = negated_match.end()
                    while True:
                        additional = _ADDITIONAL_NEGATED_OBJECT_RE.match(clause, cursor)
                        if additional is None:
                            break
                        _remove_requirement(
                            merged,
                            _requirement_from_object(additional.group("object")),
                        )
                        additionally_negated.append(
                            (
                                additional.start(),
                                additional.end(),
                                additional.group("object"),
                            )
                        )
                        cursor = additional.end()
                positive_text = clause
                negated_spans = [
                    (match.start(), match.end()) for match in negated_matches
                ] + [
                    (start, end) for start, end, _object in additionally_negated
                ]
                for start, end in sorted(
                    negated_spans, key=lambda item: item[0], reverse=True
                ):
                    positive_text = (
                        positive_text[:start]
                        + positive_text[end:]
                    )
                positive_text = positive_text.casefold()
                required: list[str] = []
                if "表格" in positive_text or re.search(
                    r"(?<![a-z0-9_])table(?![a-z0-9_])", positive_text
                ):
                    required.append("table")
                if "柱状图" in positive_text or re.search(
                    r"(?<![a-z0-9_])bar(?:\s+|-)+chart(?![a-z0-9_])",
                    positive_text,
                ):
                    required.append("chart:bar")
                if "折线图" in positive_text or re.search(
                    r"(?<![a-z0-9_])line(?:\s+|-)+chart(?![a-z0-9_])",
                    positive_text,
                ):
                    required.append("chart:line")
                if "饼图" in positive_text or re.search(
                    r"(?<![a-z0-9_])pie(?:\s+|-)+chart(?![a-z0-9_])",
                    positive_text,
                ):
                    required.append("chart:pie")
                has_specific_chart = any(
                    item.startswith("chart:") for item in required
                )
                if not has_specific_chart and (
                    "图表" in positive_text
                    or re.search(
                        r"(?<![a-z0-9_])chart(?![a-z0-9_])", positive_text
                    )
                ):
                    required.append("chart")
                for item in required:
                    _add_requirement(merged, item)
        if not merged:
            requirements.pop(page, None)
    return requirements

_RESEARCH_NODES = (
    "research_plan",
    "research_expand",
    "research_search",
    "research_direct",
    "research_fetch",
    "research_score",
    "research_gap",
    "research_rerank",
    "research_synth",
    "research_cite",
)
_ALL_VALUE_WRITERS = frozenset(
    {
        "normalize",
        *_RESEARCH_NODES,
        "outline",
        "wait_outline_decision",
        "revise_outline",
        "preflight",
        "image_probe",
        "prepare_slides",
        "image_map",
        "render",
        "preview",
        "visual_evaluate",
        "visual_revise",
        "publish",
        "terminal",
    }
)


def _effective(state: Mapping[str, object]) -> dict[str, object]:
    result = dict(state)
    values = state.get("values")
    if isinstance(values, Mapping):
        result.update(values)
    return result


def _merged_values(
    state: Mapping[str, object], updates: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    raw = state.get("values")
    result = dict(raw) if isinstance(raw, Mapping) else {}
    result.update(copy.deepcopy(dict(updates)))
    validate_json_value(result)
    return result


def _counter(state: Mapping[str, object], name: str) -> int:
    raw = state.get("loop_counters")
    return int(raw.get(name, 0)) if isinstance(raw, Mapping) else 0


def _budget(state: Mapping[str, object], name: str, default: int) -> int:
    raw = state.get("budgets")
    return int(raw.get(name, default)) if isinstance(raw, Mapping) else default


def _clamp_int(value: object, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, parsed))


def research_checkpoint_namespace(run_id: str, parent_ns: str = "") -> str:
    prefix = f"{parent_ns}:" if parent_ns else ""
    return f"{prefix}{RESEARCH_CORE_NAMESPACE}:{run_id}"


def _checkpoint_ref(context: WorkflowContext, *, node_id: str) -> dict[str, JsonValue]:
    identity = context.identity
    if identity is None:
        return {"node_id": node_id, "checkpoint_id": "", "checkpoint_ns": ""}
    return {
        "node_id": node_id,
        "checkpoint_id": identity.checkpoint_id,
        "checkpoint_ns": identity.checkpoint_ns,
        "task_id": identity.task_id,
    }


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    topic = str(effective.get("topic") or "").strip()
    if not topic:
        raise ValueError("topic is required")
    pages = _clamp_int(effective.get("pages"), default=8, lower=1, upper=20)
    theme = str(effective.get("theme") or "minimal").strip().lower()
    if theme not in ppt_tools.VALID_THEMES:
        theme = "minimal"
    outline_budget = _clamp_int(
        effective.get("max_outline_revisions"),
        default=MAX_OUTLINE_REVISIONS,
        lower=0,
        upper=5,
    )
    visual_budget = _clamp_int(
        effective.get("max_visual_revisions"),
        default=MAX_VISUAL_REVISIONS,
        lower=0,
        upper=5,
    )
    # A checkpoint created before the editability contract must keep its old
    # image/template behavior. Every new Run writes this field explicitly.
    editable_required = bool(effective.get("editable_required", False))
    raw_requirements = effective.get("slide_requirements")
    slide_requirements = (
        copy.deepcopy(dict(raw_requirements))
        if isinstance(raw_requirements, Mapping)
        else _slide_requirements_from_topic(topic)
    )
    base_values: dict[str, JsonValue] = {
        "topic": topic,
        "pages": pages,
        "depth": str(effective.get("depth") or "deep"),
        "theme": theme,
        "image_mode": bool(effective.get("image_mode", True)),
        # Missing means a legacy v1 checkpoint created before full-page mode.
        "full_page_images": (
            False
            if editable_required
            else bool(effective.get("full_page_images", False))
        ),
        "editable_required": editable_required,
        "slide_requirements": slide_requirements,
        "title": str(effective.get("title") or topic),
        "author": str(effective.get("author") or "Simple Harness"),
        "output_path": str(effective.get("output_path") or "") or None,
        "research_config": copy.deepcopy(effective.get("research_config", {})),
        "blob_root": str(effective.get("blob_root") or ""),
        "research_checkpoint": {
            "parent_run_id": str(state.get("run_id") or ""),
            "parent_node_id": "normalize",
            "checkpoint_ns": research_checkpoint_namespace(
                str(state.get("run_id") or ""),
                context.identity.checkpoint_ns if context.identity is not None else "",
            ),
            "checkpointer": "inherited",
        },
        "terminal_status": None,
        "terminal_error": None,
    }
    research_state = dict(state)
    research_state["values"] = _merged_values(state, base_values)
    research_patch = await research_graph.normalize_handler(research_state, context)
    research_values = research_patch.values.get("values", {})
    assert isinstance(research_values, Mapping)
    return StatePatch(
        {
            "values": _merged_values(
                state, {**base_values, **copy.deepcopy(dict(research_values))}
            ),
            "loop_counters": {
                "gap_iterations": 0,
                "outline_revisions": 0,
                "visual_revisions": 0,
                "image_iterations": 0,
            },
            "budgets": {
                "gap_iterations": research_graph.MAX_GAP_ITERATIONS,
                "outline_revisions": outline_budget,
                "visual_revisions": visual_budget,
                "image_iterations": MAX_IMAGE_ITERATIONS,
            },
        }
    )


async def _research_stage(state, context, handler) -> StatePatch:
    return await handler(state, context)


async def research_plan_handler(state, context):
    return await _research_stage(state, context, research_graph.plan_handler)


async def research_expand_handler(state, context):
    return await _research_stage(state, context, research_graph.expand_handler)


async def research_search_handler(state, context):
    return await _research_stage(state, context, research_graph.search_handler)


async def research_direct_handler(state, context):
    return await _research_stage(state, context, research_graph.direct_handler)


async def research_fetch_handler(state, context):
    return await _research_stage(state, context, research_graph.fetch_handler)


async def research_score_handler(state, context):
    return await _research_stage(state, context, research_graph.score_handler)


async def research_gap_handler(state, context):
    patch = await research_graph.gap_handler(state, context)
    updates = patch.values
    counters = dict(state.get("loop_counters", {}))
    raw_counters = updates.get("loop_counters", {})
    if isinstance(raw_counters, Mapping):
        counters.update(raw_counters)
    updates["loop_counters"] = counters
    return StatePatch(updates)


async def research_gap_route(state, context) -> str:
    route = await research_graph.gap_route(state, context)
    return "outline" if route == "no_results" else route


async def research_rerank_handler(state, context):
    return await _research_stage(state, context, research_graph.rerank_handler)


async def research_synth_handler(state, context):
    return await _research_stage(state, context, research_graph.synth_handler)


async def research_cite_handler(state, context):
    return await _research_stage(state, context, research_graph.cite_handler)


def _research_report(state: Mapping[str, object]):
    core = research_graph._decode_core(_effective(state))
    return research_graph.legacy.ResearchReport(
        topic=core.request_topic,
        summary=research_graph.legacy._extract_summary(core.report_md),
        report_md=core.report_md,
        citations=core.citations,
        sub_questions=core.sub_questions,
        coverage=research_graph._coverage(core),
        errors=core.errors,
    )


def _llm_call(context: WorkflowContext):
    port = context.ports.get("llm")
    call = getattr(port, "complete", None)
    if callable(call):
        return call
    if callable(port):
        return port
    raise ValueError("PPT Pro requires an LLM port")


def _operation_port(context: WorkflowContext) -> object | None:
    return context.ports.get("effect") or context.ports.get("tool")


async def _draft_outline(
    state: WorkflowState,
    context: WorkflowContext,
    *,
    feedback: str = "",
) -> StatePatch:
    effective = _effective(state)
    previous = effective.get("outline_slides", [])
    prev_slides = ppt_tools.parse_outline(previous) if previous else None
    slides = await ppt_tools._draft_outline_from_research(
        str(effective["topic"]),
        _research_report(state),
        pages=int(effective["pages"]),
        theme=str(effective["theme"]),
        image_mode=bool(effective["image_mode"]),
        editable_required=bool(effective.get("editable_required", False)),
        llm_call=_llm_call(context),
        feedback=feedback,
        prev_slides=prev_slides,
    )
    payload = [nodes.slide_payload(slide) for slide in slides]
    revision = _counter(state, "outline_revisions")
    run_id = context.identity.run_id if context.identity is not None else str(state["run_id"])
    outline_id = ppt_outline_store.workflow_outline_id(run_id, revision)
    outline_hash = nodes.content_hash(payload)
    core = research_graph._decode_core(_effective(state))
    ppt_outline_store.project_workflow_outline(
        outline_id,
        str(state.get("session_id") or ""),
        str(effective["topic"]),
        payload,
        len(core.citations),
    )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "outline_id": outline_id,
                    "outline_revision": revision,
                    "outline_hash": outline_hash,
                    "outline_slides": payload,
                    "outline_checkpoint_ref": _checkpoint_ref(
                        context, node_id=context.identity.node_id if context.identity else "outline"
                    ),
                    "outline_decision": None,
                },
            )
        }
    )


async def outline_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _draft_outline(state, context)


async def outline_ready_handler(
    state: WorkflowState, context: WorkflowContext
) -> StatePatch:
    notifier = context.ports.get("notifier")
    if callable(notifier):
        effective = _effective(state)
        core = research_graph._decode_core(effective)
        await notifier(
            {
                "run_id": str(state.get("run_id") or ""),
                "session_id": str(state.get("session_id") or "default"),
                "outline_id": str(effective["outline_id"]),
                "topic": str(effective["topic"]),
                "outline_md": ppt_tools._outline_to_markdown(
                    ppt_tools.parse_outline(effective["outline_slides"])
                ),
                "sources_count": len(core.citations),
                "no_research": not bool(core.citations),
            }
        )
    return StatePatch({})


async def wait_outline_decision_handler(
    state: WorkflowState, context: WorkflowContext
) -> StatePatch:
    effective = _effective(state)
    prompt: dict[str, JsonValue] = {
        "kind": "ppt_outline",
        "decision_id": str(effective["outline_id"]),
        "outline_id": str(effective["outline_id"]),
        "outline_hash": str(effective["outline_hash"]),
        "revision": int(effective["outline_revision"]),
        "slides": copy.deepcopy(effective["outline_slides"]),
        "outline_markdown": ppt_tools._outline_to_markdown(
            ppt_tools.parse_outline(effective["outline_slides"])
        ),
        "topic": str(effective.get("topic") or ""),
        "sources_count": len(research_graph._decode_core(effective).citations),
        "no_research": not bool(research_graph._decode_core(effective).citations),
        "checkpoint_ref": _checkpoint_ref(context, node_id="wait_outline_decision"),
        "actions": ["accept", "modify", "reuse", "cancel"],
    }
    raw = workflow_interrupt(prompt)
    decision = dict(raw) if isinstance(raw, Mapping) else {"action": str(raw)}
    if "action" not in decision and isinstance(decision.get("approved"), bool):
        decision["action"] = "accept" if decision["approved"] else "cancel"
    action = str(decision.get("action") or "modify").strip().lower()
    if action == "revise":
        action = "modify"
    if action not in {"accept", "modify", "reuse", "cancel"}:
        action = "modify"
    decision["action"] = action
    if action == "reuse":
        row = ppt_outline_store.get_outline(str(decision.get("reuse_id") or ""))
        if row is None:
            decision = {"action": "modify", "feedback": "Selected outline is unavailable."}
        else:
            slides = json.loads(str(row["slides_json"]))
            effective["outline_slides"] = slides
            effective["outline_hash"] = nodes.content_hash(slides)
            decision["action"] = "accept"
    ppt_outline_store.project_workflow_decision(str(effective["outline_id"]), str(decision["action"]))
    updates: dict[str, JsonValue] = {
        "outline_decision": copy.deepcopy(decision),
        "outline_slides": copy.deepcopy(effective["outline_slides"]),
        "outline_hash": str(effective["outline_hash"]),
        "decision_checkpoint_ref": _checkpoint_ref(context, node_id="wait_outline_decision"),
    }
    if decision["action"] == "cancel":
        updates.update({"terminal_status": "cancelled", "terminal_error": None})
    elif (
        decision["action"] == "modify"
        and _counter(state, "outline_revisions") >= _budget(
            state, "outline_revisions", MAX_OUTLINE_REVISIONS
        )
    ):
        updates.update(
            {
                "terminal_status": "error",
                "terminal_error": "outline revision budget exhausted",
            }
        )
    return StatePatch({"values": _merged_values(state, updates)})


async def outline_decision_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    effective = _effective(state)
    if effective.get("terminal_status") in {"cancelled", "error"}:
        return "terminal"
    decision = effective.get("outline_decision", {})
    action = decision.get("action") if isinstance(decision, Mapping) else "modify"
    return "preflight" if action == "accept" else "revise"


async def revise_outline_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    decision = effective.get("outline_decision", {})
    feedback = str(decision.get("feedback") or "") if isinstance(decision, Mapping) else ""
    counters = dict(state.get("loop_counters", {}))
    counters["outline_revisions"] = _counter(state, "outline_revisions") + 1
    revised_state = dict(state)
    revised_state["loop_counters"] = counters
    patch = await _draft_outline(revised_state, context, feedback=feedback)
    return StatePatch({**patch.values, "loop_counters": counters})


async def preflight_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    error = ppt_tools._disk_preflight(
        image_mode=bool(effective["image_mode"]), pages=int(effective["pages"])
    )
    updates: dict[str, JsonValue] = {"preflight": {"ok": error is None, "error": error}}
    if error:
        updates.update({"terminal_status": "error", "terminal_error": error})
    return StatePatch({"values": _merged_values(state, updates)})


async def preflight_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "terminal" if _effective(state).get("terminal_status") == "error" else "probe"


async def image_probe_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    reachable = False
    if bool(effective["image_mode"]):
        reachable = await nodes.probe_images(
            _operation_port(context),
            timeout_s=float(effective.get("image_probe_timeout_s") or 8.0),
        )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "image_probe": {
                        "requested": bool(effective["image_mode"]),
                        "reachable": reachable,
                    }
                },
            )
        }
    )


async def prepare_slides_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    try:
        parsed = ppt_tools.parse_outline(effective["outline_slides"])
        if bool(effective.get("editable_required", False)):
            for slide in parsed:
                if slide.layout == "image_full":
                    slide.layout = "image" if slide.image_prompt else "bullet"
                if slide.layout == "quote" and not slide.quote and slide.bullets:
                    slide.layout = "bullet"
                if (
                    slide.layout == "two_column"
                    and slide.bullets
                    and not slide.left
                    and not slide.right
                ):
                    split_at = max(1, (len(slide.bullets) + 1) // 2)
                    slide.left = list(slide.bullets[:split_at])
                    slide.right = list(slide.bullets[split_at:])
                if slide.layout == "section" and slide.bullets:
                    slide.layout = "toc"
        records = nodes.stable_slide_records(
            parsed,
            full_page_images=(
                bool(effective["image_mode"])
                and bool(effective.get("full_page_images", False))
            ),
        )
    except ppt_tools.FullPageLayoutError as exc:
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "terminal_status": "error",
                        "terminal_error": exc.as_dict(),
                    },
                )
            }
        )
    except Exception:  # noqa: BLE001
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "terminal_status": "error",
                        "terminal_error": {
                            "code": "ppt_layout_failed",
                            "user_message": "PPT 页面排版预检失败。",
                            "recovery_action": "调整页面内容后重试。",
                        },
                    },
                )
            }
        )
    probe = effective.get("image_probe", {})
    reachable = bool(probe.get("reachable")) if isinstance(probe, Mapping) else False
    full_page_requested = bool(effective["image_mode"]) and bool(
        effective.get("full_page_images", False)
    )
    if full_page_requested and not reachable:
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "slide_records": records,
                        "terminal_status": "error",
                        "terminal_error": {
                            "code": "ppt_full_page_provider_unavailable",
                            "user_message": "整页生图服务暂时不可用，PPT 未生成，也不会回退为普通模板；请稍后重试。",
                            "recovery_action": "请稍后重试；DeskPet 不会把整页生图任务静默降级为普通模板。",
                        },
                    },
                )
            }
        )
    records, render_mode = nodes.prepare_slide_records(
        records, image_mode=bool(effective["image_mode"]), reachable=reachable
    )
    fallback_reason = (
        "provider_unavailable"
        if bool(effective["image_mode"]) and not reachable
        else None
    )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "slide_records": records,
                    "render_mode": render_mode,
                    "slide_map_hash": nodes.content_hash(records),
                    "render_revision": 0,
                    "image_fallback_reason": fallback_reason,
                },
            )
        }
    )


async def prepare_slides_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "terminal" if _effective(state).get("terminal_status") == "error" else "images"


async def _image_map_once(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    try:
        records = nodes.normalize_full_page_records(effective.get("slide_records", []))
    except ppt_tools.FullPageLayoutError as exc:
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {"terminal_status": "error", "terminal_error": exc.as_dict()},
                )
            }
        )
    pending = nodes.next_pending_slide(records)
    if pending is None:
        return StatePatch({"values": _merged_values(state, {"slide_records": records})})
    updated = await nodes.generate_slide_image(pending, _operation_port(context))
    records = [updated if item["slide_id"] == updated["slide_id"] else item for item in records]
    image = updated.get("image", {})
    if isinstance(image, Mapping) and image.get("status") == "fallback_required":
        if bool(effective.get("full_page_images", False)):
            provider_attempts = int(image.get("provider_attempts") or 0) + 1
            retry_image = dict(image)
            retry_image["provider_attempts"] = provider_attempts
            updated["image"] = retry_image
            records = [
                updated if item["slide_id"] == updated["slide_id"] else item
                for item in records
            ]
            if provider_attempts < MAX_FULL_PAGE_PROVIDER_ATTEMPTS:
                retry_image["status"] = "pending"
                return StatePatch(
                    {
                        "values": _merged_values(
                            state,
                            {
                                "slide_records": records,
                                "render_mode": nodes.FULL_PAGE_RENDER_MODE,
                                "slide_map_hash": nodes.content_hash(records),
                            },
                        )
                    }
                )
            return StatePatch(
                {
                    "values": _merged_values(
                        state,
                        {
                            "slide_records": records,
                            "terminal_status": "error",
                            "terminal_error": {
                                "code": "ppt_full_page_generation_unavailable",
                                "user_message": "整页生图服务连续重试后仍不可用，PPT 未生成，也不会回退为普通模板；请稍后重试。",
                                "recovery_action": "已生成页面和节点状态会保留；服务恢复后可从安全节点重试。",
                            },
                        },
                    )
                }
            )
        records, render_mode = nodes.prepare_slide_records(
            records, image_mode=False, reachable=False
        )
        fallback_reason: str | None = "provider_unavailable"
    else:
        render_mode = str(effective.get("render_mode") or "template")
        fallback_reason = (
            str(effective.get("image_fallback_reason") or "") or None
        )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "slide_records": records,
                    "render_mode": render_mode,
                    "slide_map_hash": nodes.content_hash(records),
                    "image_fallback_reason": fallback_reason,
                },
            )
        }
    )


async def image_map_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    patch = await _image_map_once(state, context)
    counters = dict(state.get("loop_counters", {}))
    counters["image_iterations"] = int(counters.get("image_iterations") or 0) + 1
    return StatePatch({**patch.values, "loop_counters": counters})


async def image_map_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    if _effective(state).get("terminal_status") == "error":
        return "terminal"
    return "pending" if nodes.next_pending_slide(_effective(state).get("slide_records", [])) else "done"


async def render_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    records = nodes.normalize_full_page_records(effective["slide_records"])
    result = await nodes.render_deck(
        records,
        port=_operation_port(context),
        topic=str(effective["topic"]),
        title=str(effective["title"]),
        author=str(effective["author"]),
        theme=str(effective["theme"]),
        output_path=str(effective.get("output_path") or "") or None,
        render_mode=str(effective["render_mode"]),
        render_revision=int(effective.get("render_revision") or 0),
        editable_required=bool(effective.get("editable_required", False)),
        slide_requirements=(
            effective.get("slide_requirements")
            if isinstance(effective.get("slide_requirements"), Mapping)
            else {}
        ),
    )
    updates: dict[str, JsonValue] = {"render_ref": result}
    if not result.get("ok"):
        updates.update(
            {
                "terminal_status": "error",
                "terminal_error": str(result.get("error") or "PPT render failed"),
            }
        )
    return StatePatch({"values": _merged_values(state, updates)})


async def render_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "terminal" if _effective(state).get("terminal_status") == "error" else "preview"


async def preview_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    previews = await nodes.render_preview(effective["render_ref"], _operation_port(context))
    previous_hashes = {
        int(item["page"]): str(item["sha256"])
        for item in effective.get("preview_refs", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("page"), int)
        and item.get("sha256")
    }
    current_hashes = {
        int(item["page"]): str(item["sha256"])
        for item in previews
        if isinstance(item, Mapping)
        and isinstance(item.get("page"), int)
        and item.get("sha256")
    }
    raw_revision_pages = effective.get("visual_revision_pages", [])
    revision_pages = sorted(
        {
            int(page)
            for page in raw_revision_pages
            if isinstance(page, int) and int(page) > 0
        }
    )
    no_effect_pages = [
        page
        for page in revision_pages
        if page in previous_hashes
        and page in current_hashes
        and previous_hashes[page] == current_hashes[page]
    ]
    revision_no_effect = bool(
        int(effective.get("render_revision") or 0) > 0
        and previous_hashes
        and (
            no_effect_pages
            or (not revision_pages and current_hashes == previous_hashes)
        )
    )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "preview_refs": previews,
                    "preview_hash": nodes.content_hash(previews),
                    "visual_revision_no_effect": revision_no_effect,
                    "visual_revision_no_effect_pages": no_effect_pages,
                },
            )
        }
    )


async def visual_evaluate_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    if bool(effective.get("visual_revision_no_effect", False)):
        no_effect_pages = list(
            effective.get("visual_revision_no_effect_pages", [])
        )
        review = {
            "issues": [],
            "reviews": [],
            "reviewed_pages": 0,
            "hard_failures": [
                {
                    "code": "ppt_visual_revision_no_effect",
                    "pages": no_effect_pages,
                }
            ],
        }
        review["review_hash"] = nodes.content_hash(review)
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "visual_review": review,
                        "terminal_status": "error",
                        "terminal_error": (
                            "PPT visual revision did not change reviewed page(s): "
                            + (
                                ", ".join(str(page) for page in no_effect_pages)
                                if no_effect_pages
                                else "unknown"
                            )
                        ),
                    },
                )
            }
        )
    review = await nodes.evaluate_visuals(
        effective.get("preview_refs", []),
        effective["slide_records"],
        context.ports.get("evaluator"),
    )
    updates: dict[str, JsonValue] = {"visual_review": review}
    issues = review.get("issues", []) if isinstance(review, Mapping) else []
    hard_failures = (
        review.get("hard_failures", []) if isinstance(review, Mapping) else []
    )
    if hard_failures:
        codes = [
            str(item.get("code") or "ppt_quality_gate_failed")
            for item in hard_failures
            if isinstance(item, Mapping)
        ]
        updates.update(
            {
                "terminal_status": "error",
                "terminal_error": (
                    "PPT quality gate could not verify every page: "
                    + ", ".join(codes)
                ),
            }
        )
    elif issues and _counter(state, "visual_revisions") >= _budget(
        state, "visual_revisions", MAX_VISUAL_REVISIONS
    ):
        updates.update(
            {
                "terminal_status": "error",
                "terminal_error": "PPT page quality issues remain after revision budget",
            }
        )
    return StatePatch({"values": _merged_values(state, updates)})


async def visual_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    effective = _effective(state)
    review = effective.get("visual_review", {})
    issues = review.get("issues", []) if isinstance(review, Mapping) else []
    hard_failures = (
        review.get("hard_failures", []) if isinstance(review, Mapping) else []
    )
    if hard_failures:
        return "terminal"
    if issues and _counter(state, "visual_revisions") < _budget(
        state, "visual_revisions", MAX_VISUAL_REVISIONS
    ):
        return "revise"
    if issues:
        return "terminal"
    return "publish"


async def visual_revise_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    review = effective.get("visual_review", {})
    issues = review.get("issues", []) if isinstance(review, Mapping) else []
    records = nodes.apply_visual_revision(effective["slide_records"], issues)
    revision_pages = sorted(
        {
            int(page)
            for issue in issues
            if isinstance(issue, Mapping)
            for page in [issue.get("page", issue.get("slide_index"))]
            if isinstance(page, int) and int(page) > 0
        }
    )
    counters = dict(state.get("loop_counters", {}))
    counters["visual_revisions"] = _counter(state, "visual_revisions") + 1
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "slide_records": records,
                    "render_revision": counters["visual_revisions"],
                    "slide_map_hash": nodes.content_hash(records),
                    "visual_revision_pages": revision_pages,
                },
            ),
            "loop_counters": counters,
        }
    )


async def publish_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    render = effective.get("render_ref", {})
    if not isinstance(render, Mapping) or not render.get("ok") or not render.get("path"):
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {"terminal_status": "error", "terminal_error": "render artifact is missing"},
                )
            }
        )
    previews = effective.get("preview_refs", [])
    review = effective.get("visual_review", {})
    records = effective.get("slide_records", [])
    quality = nodes.validate_delivery_quality(
        render_ref=render,
        previews=previews if isinstance(previews, list) else [],
        review=review if isinstance(review, Mapping) else {},
        expected_slide_count=len(records) if isinstance(records, list) else 0,
        expected_preview_hash=str(effective.get("preview_hash") or ""),
        editable_required=bool(effective.get("editable_required", False)),
        slide_requirements=(
            effective.get("slide_requirements")
            if isinstance(effective.get("slide_requirements"), Mapping)
            else {}
        ),
        expected_slides=records if isinstance(records, list) else [],
    )
    if not quality["passed"]:
        codes = [
            str(item.get("code") or "ppt_delivery_quality_failed")
            for item in quality["hard_failures"]
            if isinstance(item, Mapping)
        ]
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "terminal_status": "error",
                        "terminal_error": (
                            "PPT delivery quality revalidation failed: "
                            + ", ".join(codes)
                        ),
                        "delivery_quality_gate": quality,
                    },
                )
            }
        )
    artifact_ref = f"pptx:{render['output_hash']}"
    publish_ref: dict[str, JsonValue] = {
        "artifact_ref": artifact_ref,
        "path": str(render["path"]),
        "sha256": str(render["output_hash"]),
        "preview_refs": copy.deepcopy(effective.get("preview_refs", [])),
        "review_ref": copy.deepcopy(effective.get("visual_review", {})),
        "render_mode": str(effective.get("render_mode") or "template"),
        "fallback_reason": str(effective.get("image_fallback_reason") or "") or None,
        "quality_warning": copy.deepcopy(effective.get("visual_quality_warning")),
    }
    return StatePatch(
        {
            "values": _merged_values(
                state, {"publish_ref": publish_ref, "terminal_status": "success"}
            ),
            "artifact_refs": [artifact_ref],
        }
    )


async def terminal_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    run_id = context.identity.run_id if context.identity is not None else str(state["run_id"])
    status = str(effective.get("terminal_status") or "error")
    raw_error = effective.get("terminal_error")
    if isinstance(raw_error, Mapping):
        error = str(raw_error.get("user_message") or "PPT 生成失败")
        error_code = str(raw_error.get("code") or "ppt_layout_failed")
        recovery_action = str(raw_error.get("recovery_action") or "调整内容后重试。")
    else:
        error = str(raw_error or "") or None
        error_code = "ppt_failed" if error else ""
        recovery_action = "检查任务状态后重试。" if error else ""
    receipt_id = f"{run_id}:receipt"
    intents: list[dict[str, JsonValue]] = [
        {
            "intent_id": receipt_id,
            "kind": "receipt",
            "channel": "receipt",
            "payload": {
                "tool": "ppt_pro",
                "outcome": status,
                "run_id": run_id,
                "error": error,
                "error_code": error_code or None,
                "recovery_action": recovery_action or None,
            },
        }
    ]
    publish = effective.get("publish_ref")
    if status == "success" and isinstance(publish, Mapping):
        if publish.get("fallback_reason") == "provider_unavailable":
            final_text = f"PPT 已生成（图片服务不可用，已使用模板回退）：{publish['path']}"
        elif isinstance(publish.get("quality_warning"), Mapping):
            count = int(publish["quality_warning"].get("issues_remaining") or 0)
            final_text = f"PPT 已生成（仍有 {count} 页视觉检查警告，请打开检查）：{publish['path']}"
        else:
            final_text = f"PPT 已生成：{publish['path']}"
        intents.extend(
            [
                {
                    "intent_id": f"{run_id}:artifact",
                    "kind": "artifact_card",
                    "channel": "artifact",
                    "payload": copy.deepcopy(dict(publish)),
                },
                {
                    "intent_id": f"{run_id}:open",
                    "kind": "open_artifact",
                    "channel": "desktop_open",
                    "payload": {
                        "path": str(publish["path"]),
                        "artifact_ref": str(publish["artifact_ref"]),
                    },
                },
                {
                    "intent_id": f"{run_id}:final",
                    "kind": "final_assistant",
                    "channel": "final_assistant",
                    "payload": {"text": final_text},
                },
            ]
        )
    else:
        text = "PPT 任务已取消。" if status == "cancelled" else f"PPT 生成失败：{error or 'unknown error'}"
        intents.append(
            {
                "intent_id": f"{run_id}:final",
                "kind": "final_assistant",
                "channel": "final_assistant",
                "payload": {"text": text},
            }
        )
    return StatePatch(
        {
            "values": _merged_values(state, {"delivery_intents": intents}),
            "receipt_refs": [receipt_id],
        }
    )


def _single(value_type: JsonType, writers: frozenset[str]) -> ChannelSpec:
    return ChannelSpec(
        value_type=value_type,
        reducer=ReducerKind.SINGLE_WRITER,
        allowed_writers=writers,
    )


PPT_PRO_V1_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=(
        NodeDefinition("normalize", normalize_handler),
        NodeDefinition("research_plan", research_plan_handler),
        NodeDefinition("research_expand", research_expand_handler),
        NodeDefinition("research_search", research_search_handler),
        NodeDefinition("research_direct", research_direct_handler),
        NodeDefinition("research_fetch", research_fetch_handler),
        NodeDefinition("research_score", research_score_handler),
        NodeDefinition("research_gap", research_gap_handler),
        NodeDefinition("research_rerank", research_rerank_handler),
        NodeDefinition("research_synth", research_synth_handler),
        NodeDefinition("research_cite", research_cite_handler),
        NodeDefinition("outline", outline_handler),
        NodeDefinition("outline_ready", outline_ready_handler),
        NodeDefinition(
            "wait_outline_decision",
            wait_outline_decision_handler,
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
        ),
        NodeDefinition("revise_outline", revise_outline_handler),
        NodeDefinition("preflight", preflight_handler),
        NodeDefinition("image_probe", image_probe_handler),
        NodeDefinition("prepare_slides", prepare_slides_handler),
        NodeDefinition("image_map", image_map_handler),
        NodeDefinition("render", render_handler),
        NodeDefinition("preview", preview_handler),
        NodeDefinition("visual_evaluate", visual_evaluate_handler),
        NodeDefinition("visual_revise", visual_revise_handler),
        NodeDefinition("publish", publish_handler),
        NodeDefinition("terminal", terminal_handler),
    ),
    channels={
        "values": _single(JsonType.OBJECT, _ALL_VALUE_WRITERS),
        "loop_counters": _single(
            JsonType.OBJECT,
            frozenset(
                {"normalize", "research_gap", "revise_outline", "image_map", "visual_revise"}
            ),
        ),
        "budgets": _single(JsonType.OBJECT, frozenset({"normalize"})),
        "artifact_refs": _single(JsonType.ARRAY, frozenset({"publish"})),
        "receipt_refs": _single(JsonType.ARRAY, frozenset({"terminal"})),
    },
    edges=(
        Edge("normalize", "research_plan"),
        Edge("research_plan", "research_expand"),
        Edge("research_expand", "research_search"),
        Edge("research_search", "research_direct"),
        Edge("research_direct", "research_fetch"),
        Edge("research_fetch", "research_score"),
        Edge("research_score", "research_gap"),
        Edge("research_rerank", "research_synth"),
        Edge("research_synth", "research_cite"),
        Edge("research_cite", "outline"),
        Edge("outline", "outline_ready"),
        Edge("revise_outline", "outline_ready"),
        Edge("outline_ready", "wait_outline_decision"),
        Edge("image_probe", "prepare_slides"),
        Edge("preview", "visual_evaluate"),
        Edge("visual_revise", "image_map"),
        Edge("publish", "terminal"),
        Edge("terminal", END_NODE),
    ),
    conditional_edges=(
        ConditionalEdge(
            "research_gap",
            research_gap_route,
            {"continue": "research_gap", "done": "research_rerank", "outline": "outline"},
        ),
        ConditionalEdge(
            "wait_outline_decision",
            outline_decision_route,
            {"preflight": "preflight", "revise": "revise_outline", "terminal": "terminal"},
        ),
        ConditionalEdge(
            "preflight", preflight_route, {"probe": "image_probe", "terminal": "terminal"}
        ),
        ConditionalEdge(
            "prepare_slides", prepare_slides_route, {"images": "image_map", "terminal": "terminal"}
        ),
        ConditionalEdge(
            "image_map", image_map_route,
            {"pending": "image_map", "done": "render", "terminal": "terminal"},
        ),
        ConditionalEdge("render", render_route, {"preview": "preview", "terminal": "terminal"}),
        ConditionalEdge(
            "visual_evaluate",
            visual_route,
            {"revise": "visual_revise", "publish": "publish", "terminal": "terminal"},
        ),
    ),
    recursion_limit=256,
    max_supersteps=192,
    loop_budgets={
        "gap_iterations": research_graph.MAX_GAP_ITERATIONS,
        "outline_revisions": MAX_OUTLINE_REVISIONS,
        "visual_revisions": MAX_VISUAL_REVISIONS,
        "image_iterations": MAX_IMAGE_ITERATIONS,
    },
    loop_budget_bindings={
        "research_gap->research_gap": "gap_iterations",
        "wait_outline_decision->revise_outline": "outline_revisions",
        "visual_evaluate->visual_revise": "visual_revisions",
        "image_map->image_map": "image_iterations",
    },
    prompt_manifest={
        "research_core": "v1 inherited-checkpointer staged-subgraph",
        "outline": "ppt-pro-outline-v1",
        "visual_review": "ppt-visual-review-v1",
    },
    policy_manifest={
        "implementation": "ppt-pro-graph-v1.0.0",
        "research_terminal_delivery": False,
        "research_checkpoint_namespace": RESEARCH_CORE_NAMESPACE,
        "slide_map_key": "stable_slide_id",
        "decision_barrier": "deskpet-native-interrupt-v1",
        "terminal_contract": "delivery-intents-only-after-publish",
        "legacy_fallback": "new-runs-only-explicit-kill-switch",
    },
)

PPT_PRO_V1: CompiledWorkflow = compile_workflow(PPT_PRO_V1_DEFINITION)


def initial_state(
    *,
    topic: str,
    run_id: str,
    thread_id: str | None = None,
    session_id: str = "",
    pages: int = 8,
    depth: str = "deep",
    theme: str = "minimal",
    image_mode: bool = True,
    full_page_images: bool = False,
    editable_required: bool | None = None,
    slide_requirements: Mapping[str, Sequence[str]] | None = None,
    title: str | None = None,
    author: str = "Simple Harness",
    output_path: str | Path | None = None,
    research_config: Mapping[str, JsonValue] | None = None,
    blob_root: str | Path | None = None,
    max_outline_revisions: int = MAX_OUTLINE_REVISIONS,
    max_visual_revisions: int = MAX_VISUAL_REVISIONS,
) -> WorkflowState:
    effective_editable_required = (
        not full_page_images
        if editable_required is None
        else bool(editable_required)
    )
    frozen_requirements = (
        {
            str(page): [str(item) for item in requirements]
            for page, requirements in slide_requirements.items()
        }
        if slide_requirements is not None
        else _slide_requirements_from_topic(topic)
    )
    effective_image_mode = (
        False if _explicit_image_mode_is_false(topic) else image_mode
    )
    effective_full_page_images = (
        False if effective_editable_required else full_page_images
    )
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow_name": WORKFLOW_NAME,
        "workflow_version": WORKFLOW_VERSION,
        "thread_id": thread_id or run_id,
        "run_id": run_id,
        "session_id": session_id,
        "active_nodes": [],
        "active_step_id": None,
        "status": "pending",
        "values": {
            "topic": topic,
            "pages": pages,
            "depth": depth,
            "theme": theme,
            "image_mode": effective_image_mode,
            "full_page_images": effective_full_page_images,
            "editable_required": effective_editable_required,
            "slide_requirements": frozen_requirements,
            "title": title or topic,
            "author": author,
            "output_path": str(output_path) if output_path is not None else None,
            "research_config": dict(research_config or {}),
            "blob_root": str(blob_root) if blob_root is not None else "",
            "max_outline_revisions": max_outline_revisions,
            "max_visual_revisions": max_visual_revisions,
        },
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {
            "gap_iterations": 0,
            "outline_revisions": 0,
            "visual_revisions": 0,
            "image_iterations": 0,
        },
        "budgets": {
            "gap_iterations": research_graph.MAX_GAP_ITERATIONS,
            "outline_revisions": max_outline_revisions,
            "visual_revisions": max_visual_revisions,
            "image_iterations": MAX_IMAGE_ITERATIONS,
        },
        "errors": [],
    }


__all__ = [
    "MAX_OUTLINE_REVISIONS",
    "MAX_VISUAL_REVISIONS",
    "PPT_PRO_V1",
    "PPT_PRO_V1_DEFINITION",
    "RESEARCH_CORE_NAMESPACE",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "initial_state",
    "research_checkpoint_namespace",
]
