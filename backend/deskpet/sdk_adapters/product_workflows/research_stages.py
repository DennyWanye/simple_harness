# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Detached DeepResearch v7 stages backed only by typed product ports.

The planning, continuation, citation and report rules are the product-owned
parts of the former v7 implementation.  They live here so the active graph
does not import the retired Workflow engine or its definition package.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from simple_harness.contracts import JsonValue, validate_json_value
from simple_harness.workflow import StatePatch, WorkflowContext

from .payloads import bounded_report_envelope, report_sha256
from .research_ports import ResearchPorts


PLAN_PROMPT = """\
You are planning a deep research project from the original user request below.

ORIGINAL USER REQUEST (authoritative; derive ALL sub-questions from THIS):
{user_request}

CANDIDATE TOPIC FROM TOOL ARGS (untrusted; ignore it if it conflicts):
{topic}

Break it into 3-6 focused, complementary sub-questions covering background,
current state, controversies, recent developments and outlook.
Output ONLY a JSON array of strings. No prose and no fences.
"""

SYNTH_PROMPT = """\
Write a unified DeepResearch Markdown briefing for the original request:
{topic}

Use only the numbered evidence below. Every factual claim must cite an
existing footnote like [^1]. Never invent or renumber a source. Distinguish
official facts from secondary reports, label dates and measurement scopes,
and surface conflicts rather than averaging them.

Required sections: # title, ## TL;DR, ## Key findings, ## Analysis,
## Caveats and open questions, ## Conclusion.

EVIDENCE:
{evidence}

REPORT:
"""

CONTINUATION_PROMPT = """\
The same DeepResearch sub-direction returned insufficient evidence.
SUB-DIRECTION: {question}
REASON: {reason}
Return one specific continuation query for the SAME direction. Prefer an
official or first-party source. Output only the query.
"""

_FOOTNOTE_RE = re.compile(r"\[\^(\d+)\]")
_AUTHORITY = {
    "gov": 1.6,
    "edu": 1.5,
    "who.int": 1.4,
    "ietf.org": 1.5,
    "github.com": 1.3,
    "openai.com": 1.3,
    "anthropic.com": 1.3,
    "medium.com": 0.85,
}


@dataclass(frozen=True, slots=True)
class Citation:
    n: int
    url: str
    title: str
    snippet: str
    authority: float = 1.0

    def footnote(self) -> str:
        return f"[^{self.n}]: [{self.title}]({self.url})"


@dataclass(slots=True)
class ResearchReport:
    topic: str
    summary: str
    report_md: str
    citations: list[Citation]
    sub_questions: list[str]
    coverage: dict[str, JsonValue]
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "topic": self.topic,
            "summary": self.summary,
            "report_md": self.report_md,
            "citations": [asdict(item) for item in self.citations],
            "sub_questions": list(self.sub_questions),
            "coverage": copy.deepcopy(self.coverage),
            "errors": list(self.errors),
        }
        validate_json_value(value, path="$.research_report")
        return value


def _values(state: Mapping[str, object]) -> dict[str, JsonValue]:
    value = state.get("values")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _patch(state: Mapping[str, object], **changes: JsonValue) -> StatePatch:
    values = _values(state)
    values.update(copy.deepcopy(changes))
    validate_json_value(values, path="$.values")
    return StatePatch({"values": values})


def operation_key(state: Mapping[str, object], node: str, effect: str = "physical") -> str:
    run_id = str(state.get("run_id") or "")
    if not run_id:
        raise ValueError("DeepResearch run_id is required")
    return hashlib.sha256(f"{run_id}|deep-research-v7-sdk1|{node}|{effect}".encode()).hexdigest()


def parse_sub_questions(raw: str, *, maximum: int) -> list[str]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text[text.find("\n") + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    left, right = text.find("["), text.rfind("]")
    values: list[object] = []
    if 0 <= left < right:
        try:
            decoded = json.loads(text[left : right + 1])
            values = decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            values = []
    if not values:
        values = [
            re.sub(r"^[-*\d.\s]+", "", line).strip().strip("\"'")
            for line in text.splitlines()
            if "?" in line or "？" in line
        ]
    result: list[str] = []
    for value in values:
        question = str(value).strip()
        if question and question not in result:
            result.append(question)
    return result[:maximum]


def _normalized_url(url: str) -> str:
    try:
        value = urlsplit(url)
        return f"{value.scheme.lower()}://{value.netloc.lower()}{value.path.rstrip('/')}"
    except ValueError:
        return url.rstrip("/").lower()


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _authority(url: str) -> float:
    host = _host(url)
    for suffix, score in _AUTHORITY.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return score
    if host.endswith(".gov"):
        return _AUTHORITY["gov"]
    if host.endswith(".edu"):
        return _AUTHORITY["edu"]
    return 1.0


def _summary(report_md: str, fallback: str) -> str:
    match = re.search(
        r"##\s+TL;DR\s*\n+([^\n#]+(?:\n[^\n#]+)*)",
        report_md,
        flags=re.IGNORECASE,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()[:4096]
    return next(
        (line.strip("# ")[:4096] for line in report_md.splitlines() if line.strip()),
        fallback,
    )


def _finalize_citations(report_md: str, citations: list[Citation]) -> tuple[str, list[str]]:
    errors: list[str] = []
    known = {item.n for item in citations}
    refs = {int(value) for value in _FOOTNOTE_RE.findall(report_md)}
    missing = sorted(refs - known)
    if missing:
        errors.append(f"citation_check_missing:{','.join(map(str, missing))}")
        report_md = _FOOTNOTE_RE.sub(
            lambda match: match.group(0) if int(match.group(1)) in known else "",
            report_md,
        )
    if citations and not (refs & known):
        report_md = report_md.rstrip() + "\n\n## Key sources\n\n" + "\n".join(
            f"- {item.title}: {item.snippet[:240]}[^{item.n}]" for item in citations
        )
        refs = {item.n for item in citations}
    used = [item for item in citations if item.n in refs] or citations
    if used:
        report_md = re.sub(r"(?ms)\n---\n\n## (?:引用|Sources).*\Z", "", report_md).rstrip()
        report_md += "\n\n---\n\n## 引用\n\n" + "\n".join(
            item.footnote() for item in used
        ) + "\n"
    return report_md, errors


async def _publish_progress(
    context: WorkflowContext, children: list[dict[str, JsonValue]]
) -> None:
    try:
        reporter = context.port("progress")
    except (KeyError, LookupError):
        return
    publish = getattr(reporter, "report_deep_research_v7_children", None)
    if callable(publish):
        await publish(context.identity, copy.deepcopy(children))


async def normalize_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    topic = str(values.get("topic") or "").strip()
    if not topic:
        raise ValueError("DeepResearch topic is required")
    mode = str(values.get("mode") or "standard").strip().lower()
    if mode not in {"light", "standard", "deep"}:
        mode = "standard"
    config = values.get("research_config")
    config = dict(config) if isinstance(config, Mapping) else {}
    maximum = max(2, min(6, int(config.get("max_sub_questions", 4))))
    return _patch(
        state,
        topic=topic,
        mode=mode,
        max_sub_questions=maximum,
        max_child_attempts=2,
        sub_questions=[],
        business_status="researching",
    )


async def plan_handler(state, context: WorkflowContext) -> StatePatch:
    ports = ResearchPorts.from_context(context)
    values = _values(state)
    topic = str(values["topic"])
    errors: list[str] = []
    try:
        raw = await ports.llm.complete(
            PLAN_PROMPT.format(topic=topic, user_request=topic),
            operation_key=operation_key(state, "plan"),
        )
    except Exception as exc:  # noqa: BLE001 - bounded product fallback
        raw = ""
        errors.append(f"plan_llm:{type(exc).__name__}")
    questions = parse_sub_questions(raw, maximum=int(values["max_sub_questions"]))
    if len(questions) < 2:
        questions = [
            f"{topic} 的当前事实、背景和主要参与者是什么？",
            f"{topic} 的关键争议、风险、替代方案和未来趋势是什么？",
        ][: int(values["max_sub_questions"])]
        errors.append("plan_fallback:two_complementary_directions")
    children = [
        {
            "child_id": f"dr-{index}",
            "question": question,
            "status": "queued",
            "attempt": 0,
            "max_attempts": int(values["max_child_attempts"]),
            "n_sources": 0,
            "reason_code": "",
            "continuation": "",
        }
        for index, question in enumerate(questions)
    ]
    await _publish_progress(context, children)
    return _patch(state, sub_questions=questions, child_records=children, plan_errors=errors)


async def search_handler(state, context: WorkflowContext) -> StatePatch:
    ports = ResearchPorts.from_context(context)
    values = _values(state)
    sources: list[dict[str, JsonValue]] = []
    children = [copy.deepcopy(dict(item)) for item in values.get("child_records", []) if isinstance(item, Mapping)]
    errors: list[str] = []
    seen_urls: set[str] = set()
    max_attempts = int(values.get("max_child_attempts") or 2)
    for index, question_value in enumerate(values.get("sub_questions", [])):
        question = str(question_value)
        continuation = ""
        accepted = 0
        attempts: list[dict[str, JsonValue]] = []
        for attempt in range(1, max_attempts + 1):
            query = question if not continuation else f"{question}\n{continuation}"
            found = await ports.search.search(
                query,
                operation_key=operation_key(state, "search", f"query:{index}:{attempt}"),
            )
            before = len(sources)
            for item_index, item in enumerate(found[:4]):
                url = str(item.get("url") or "").strip()
                normalized = _normalized_url(url)
                if not url:
                    continue
                if normalized in seen_urls:
                    # One authoritative source may legitimately support more than
                    # one manager sub-direction.  Keep the global citation unique
                    # while still marking this child as evidenced.
                    accepted += 1
                    continue
                try:
                    text = await ports.fetch.fetch(
                        url,
                        operation_key=operation_key(
                            state, "search", f"fetch:{index}:{attempt}:{item_index}"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - sibling source isolation
                    errors.append(f"fetch:{index}:{attempt}:{type(exc).__name__}")
                    continue
                excerpt = str(text).strip()
                if not excerpt:
                    continue
                seen_urls.add(normalized)
                sources.append(
                    {
                        "url": url,
                        "title": str(item.get("title") or url)[:512],
                        "excerpt": excerpt[:8192],
                        "question": question,
                        "authority": _authority(url),
                    }
                )
            accepted += len(sources) - before
            reason = "ok" if accepted else "no_citations"
            attempts.append(
                {"attempt": attempt, "status": "valid" if accepted else "retryable", "reason_code": reason, "n_sources": accepted}
            )
            if accepted:
                break
            if attempt < max_attempts:
                try:
                    continuation = (
                        await ports.llm.complete(
                            CONTINUATION_PROMPT.format(question=question, reason=reason),
                            operation_key=operation_key(state, "search", f"continue:{index}:{attempt}"),
                        )
                    ).strip()[:1024]
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"continuation:{index}:{type(exc).__name__}")
                    continuation = f"{question} official primary source"
        record = children[index] if index < len(children) else {"child_id": f"dr-{index}", "question": question}
        record.update(
            status="valid" if accepted else "insufficient",
            attempt=len(attempts),
            n_sources=accepted,
            reason_code="ok" if accepted else "no_citations",
            continuation=continuation,
            attempts=attempts,
        )
        if index >= len(children):
            children.append(record)
        await _publish_progress(context, children)
    return _patch(state, sources=sources, child_records=children, search_errors=errors)


async def synth_handler(state, context: WorkflowContext) -> StatePatch:
    ports = ResearchPorts.from_context(context)
    values = _values(state)
    sources = [dict(item) for item in values.get("sources", []) if isinstance(item, Mapping)]
    citations = [
        Citation(
            index,
            str(item["url"]),
            str(item.get("title") or item["url"]),
            str(item.get("excerpt") or "")[:1024],
            float(item.get("authority") or 1.0),
        )
        for index, item in enumerate(sources, start=1)
    ]
    evidence = "\n\n".join(
        f"({item.n}) {item.title}\nURL: {item.url}\n{item.snippet}" for item in citations
    )
    topic = str(values["topic"])
    errors = [str(value) for value in values.get("plan_errors", [])] + [str(value) for value in values.get("search_errors", [])]
    if citations:
        try:
            report = await ports.llm.complete(
                SYNTH_PROMPT.format(topic=topic, evidence=evidence[:48000]),
                operation_key=operation_key(state, "synth"),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"synth_llm:{type(exc).__name__}")
            report = ""
        if not report.strip():
            report = f"# {topic}\n\n## TL;DR\n\n已收集 {len(citations)} 个可引用来源。"
        report, citation_errors = _finalize_citations(report.strip(), citations)
        errors.extend(citation_errors)
    else:
        report = (
            f"# {topic}\n\n## TL;DR\n\n未能找到可用来源。\n\n"
            "## 已尝试方向\n\n"
            + "\n".join(f"- {value}" for value in values.get("sub_questions", []))
        )
    children = [dict(item) for item in values.get("child_records", []) if isinstance(item, Mapping)]
    insufficient = [item for item in children if item.get("status") != "valid"]
    if insufficient:
        report += "\n\n## 调研局限\n\n" + "\n".join(
            f"- {item.get('question')}: {item.get('reason_code')}，尝试 {item.get('attempt')} 次。"
            for item in insufficient
        )
    status = "insufficient_evidence" if not citations else "partial" if insufficient else "completed"
    research_report = ResearchReport(
        topic,
        _summary(report, topic),
        report,
        citations,
        [str(value) for value in values.get("sub_questions", [])],
        {
            "mode": "manager_fanout_v7_sdk1",
            "n_sources": len(citations),
            "n_domains": len({_host(item.url) for item in citations if _host(item.url)}),
            "n_children": len(children),
            "n_insufficient": len(insufficient),
        },
        errors,
    )
    return _patch(state, report_payload=research_report.to_json(), business_status=status)


async def persist_handler(state, context: WorkflowContext) -> StatePatch:
    ports = ResearchPorts.from_context(context)
    values = _values(state)
    raw = values.get("report_payload")
    if not isinstance(raw, Mapping):
        raise ValueError("DeepResearch report payload is unavailable")
    report = str(raw.get("report_md") or "")
    digest = report_sha256(report)
    blob = await ports.blob.put(
        report.encode("utf-8"),
        media_type="text/markdown",
        operation_key=operation_key(state, "persist", "blob"),
    )
    artifact: dict[str, JsonValue] | None = None
    if raw.get("citations") and report:
        artifact = await ports.artifact.save_report(
            title=str(values["topic"]),
            report_markdown=report,
            report_sha256=digest,
            operation_key=operation_key(state, "persist", "artifact"),
        )
    return _patch(
        state,
        report_sha256=digest,
        report_blob=blob,
        report_artifact=artifact,
    )


async def finalize_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    raw = values.get("report_payload")
    if not isinstance(raw, Mapping):
        raise ValueError("DeepResearch report payload is unavailable")
    artifact = values.get("report_artifact")
    if not isinstance(artifact, Mapping):
        artifact = {}
    envelope = bounded_report_envelope(
        title=str(values["topic"]),
        summary=str(raw.get("summary") or ""),
        report_hash=str(values["report_sha256"]),
        blob=values["report_blob"],  # type: ignore[arg-type]
        artifact=artifact,
    )
    run_id = str(state.get("run_id"))
    intents: list[dict[str, JsonValue]] = []
    if artifact:
        intents.append(
            {
                "intent_id": f"{run_id}:research-report",
                "kind": "artifact_card",
                "channel": "artifact",
                "payload": envelope,
            }
        )
    intents.append(
        {
            "intent_id": f"{run_id}:final",
            "kind": "final_assistant",
            "channel": "final_assistant",
            "payload": {
                "text": str(raw.get("summary") or values["topic"]),
                "business_status": str(values.get("business_status") or "insufficient_evidence"),
                "report": envelope,
            },
        }
    )
    return _patch(state, delivery_intents=intents)


__all__ = (
    "Citation",
    "CONTINUATION_PROMPT",
    "PLAN_PROMPT",
    "ResearchReport",
    "SYNTH_PROMPT",
    "finalize_handler",
    "normalize_handler",
    "operation_key",
    "parse_sub_questions",
    "persist_handler",
    "plan_handler",
    "search_handler",
    "synth_handler",
)
