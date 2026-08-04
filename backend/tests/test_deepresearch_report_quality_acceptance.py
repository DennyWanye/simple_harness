from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.acceptance.deepresearch_report_quality import evaluate_report, main


ROOT = Path(__file__).resolve().parents[2]


def _professional_report() -> str:
    findings = [
        (
            "AtlasRT 推理引擎",
            "AtlasRT 推理引擎发布分块调度器，在混合批处理下将首字延迟降低 28%。",
            0.91,
            1.0,
            "生产可用；建议先用线上影子流量验证峰值延迟，再逐步扩大采用范围。",
        ),
        (
            "Orion Agent 协议",
            "Orion Agent 协议新增可验证工具授权票据，减少跨智能体调用时的权限漂移。",
            0.78,
            0.8,
            "正在成熟；适合开展限界 PoC，并审计身份映射与撤权链路。",
        ),
        (
            "星云原生多模态模型",
            "星云原生多模态模型统一视觉与语音编码，在长视频理解基准上提升 16%。",
            0.66,
            0.8,
            "正在成熟；建议以领域视频集验证幻觉率和时间定位稳定性。",
        ),
        (
            "RiverTrain 训练系统",
            "RiverTrain 训练系统引入容错流水并行，使节点故障后的恢复时间缩短至四分钟。",
            0.52,
            0.6,
            "实验阶段；仅建议在非关键训练集群进行技术验证和故障演练。",
        ),
        (
            "松果开源向量运行时",
            "松果开源向量运行时增加稀疏稠密联合索引，降低知识检索阶段的内存占用。",
            0.41,
            0.3,
            "实验阶段；采用前应复核许可证、索引迁移成本和召回稳定性。",
        ),
    ]
    summary = "\n".join(
        f"- **{entity}**：{core} 当前判断为{maturity.split('；', 1)[0]}（总分 {score:.2f}），"
        f"建议按证据范围推进验证。 [{index}]"
        for index, (entity, core, score, _recency, maturity) in enumerate(findings[:4], 1)
    )
    blocks = []
    for index, (entity, core, score, recency, maturity) in enumerate(findings, 1):
        blocks.append(
            f"#### {index}. {entity}\n\n"
            f"- 核心变化：{core} [{index}]\n"
            f"- 价值判断：总分 {score:.2f}；近期性 {recency:.1f}、技术影响 2/3、证据质量 3/3。 [{index}]\n"
            f"- 成熟度与采用建议：{maturity} [{index}]\n"
            f"- 风险与不确定性：独立复现范围仍有限，需要在真实负载下持续核验边界条件。 [{index}]\n"
            f"- 引用：[{index}]\n"
            f"- 来源日期：2026-07-{index:02d}"
        )
    appendix = "\n".join(
        f"[{index}] 支持条目：{findings[index - 1][0]}｜来源类型：官方发布页｜"
        f"来源 {index} — https://source{index}.example/release"
        for index in range(1, 6)
    )
    joined_blocks = "\n\n".join(blocks)
    return (
        "# 人工智能前沿技术情报\n\n"
        "## 一页式执行摘要\n\n"
        "- 情报窗口：2026-01-16 至 2026-07-15\n\n"
        "### 关键结论与采用动作\n\n"
        f"{summary}\n\n"
        "## 分主题 Top 技术（本次核验 5 项）\n\n"
        "### 推理、智能体与基础设施\n\n"
        f"{joined_blocks}\n\n"
        "## 方法与局限\n\n"
        "- 排序综合近期性、影响、成熟度和证据质量；缺少独立复现时保守降权。\n\n"
        "## 引用\n\n"
        f"{appendix}"
    )


def _old_bad_shape() -> str:
    return """# AI 技术报告

## Top 技术

### 1. English claim sentence.

- 发生了什么：English claim sentence. [1]
- 为什么重要：当前证据未形成可核验的影响量。
- 成熟度：证据不足，成熟度未知
- 价值分：0.100（近期性 0.0）

### 2. Electronic Health Records are useful.

- 发生了什么：EHR healthcare sample. [2]
- 为什么重要：当前证据未形成可核验的影响量。
- 成熟度：证据不足，成熟度未知
- 价值分：0.100（近期性 0.0）

## 引用 Appendix

[1] Source — https://one.example
[2] Source — https://two.example

## Coverage

```json
{"provider_attempts": [], "published_claims": []}
```
"""


def test_synthetic_professional_report_passes_all_rules() -> None:
    markdown = _professional_report()
    result = evaluate_report(markdown)

    assert result["passed"] is True
    assert result == evaluate_report(markdown)
    assert result["failure_codes"] == []
    assert all(result["checks"].values())
    assert result["counts"]["top_finding_count"] == 5
    assert result["counts"]["summary_judgment_count"] == 4
    assert result["counts"]["distinct_score_count"] == 5
    assert result["counts"]["appendix_citation_count"] == 5
    assert result["counts"]["mapped_appendix_citation_count"] == 5


def test_synthetic_old_claim_dump_fails_without_echoing_content() -> None:
    result = evaluate_report(_old_bad_shape())

    assert result["passed"] is False
    assert result["checks"]["required_sections"] is False
    assert result["checks"]["finding_count"] is False
    assert result["checks"]["score_diversity"] is False
    assert result["checks"]["forbidden_noise"] is False
    encoded = json.dumps(result, ensure_ascii=False)
    for forbidden in (
        "English claim sentence",
        "Electronic Health Records",
        "https://one.example",
        "provider_attempts",
    ):
        assert forbidden not in encoded


def test_current_local_legacy_report_is_rejected_when_artifact_is_present() -> None:
    matches = list((ROOT / "DeepResearch").glob("*-66720bcf682b46eeb6e6923a11ec02eb.md"))
    if not matches:
        pytest.skip("local legacy artifact is not part of a clean checkout")

    result = evaluate_report(matches[0].read_text(encoding="utf-8"))

    assert result["passed"] is False
    assert result["checks"]["forbidden_noise"] is False
    assert result["checks"]["score_diversity"] is False
    assert result["counts"]["forbidden_rule_match_count"] > 0


def test_cli_writes_only_safe_structured_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "private-report.md"
    output = tmp_path / "quality.json"
    report.write_text(_professional_report(), encoding="utf-8")

    exit_code = main(["--report-md", str(report), "--output", str(output)])
    payload = json.loads(output.read_text(encoding="utf-8"))
    stdout_payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == stdout_payload
    assert payload["passed"] is True
    raw = output.read_text(encoding="utf-8")
    for secret in (
        "AtlasRT",
        "人工智能前沿技术情报",
        "source1.example",
        "private-report.md",
    ):
        assert secret not in raw


@pytest.mark.parametrize(
    ("mutation", "failed_check"),
    [
        (lambda text: text.replace("总分 0.78", "总分 0.91"), "score_diversity"),
        (lambda text: text.replace("近期性 1.0", "近期性 0.0").replace("近期性 0.8", "近期性 0.0").replace("近期性 0.6", "近期性 0.0").replace("近期性 0.3", "近期性 0.0"), "recency_signal"),
        (lambda text: text.replace("- 引用：[3]", "- 引用：缺少引用"), "finding_citations"),
    ],
)
def test_quality_dimensions_fail_closed(mutation, failed_check: str) -> None:
    report = mutation(_professional_report())
    if failed_check == "score_diversity":
        report = report.replace("总分 0.66", "总分 0.91").replace("总分 0.52", "总分 0.91").replace("总分 0.41", "总分 0.91")
    result = evaluate_report(report)
    assert result["passed"] is False
    assert result["checks"][failed_check] is False


def test_bare_two_letter_acronym_and_unmapped_citations_fail_closed() -> None:
    report = _professional_report().replace("AtlasRT 推理引擎", "BT")
    result = evaluate_report(report)
    assert result["passed"] is False
    assert result["checks"]["entity_title_format"] is False

    report = _professional_report().replace("AtlasRT 推理引擎", "Plus")
    result = evaluate_report(report)
    assert result["passed"] is False
    assert result["checks"]["entity_title_format"] is False

    report = _professional_report().replace("支持条目：", "相关内容：", 1)
    result = evaluate_report(report)
    assert result["passed"] is False
    assert result["checks"]["citation_explainability"] is False


def test_weak_evidence_cannot_recommend_direct_poc_and_duplicate_punctuation_fails() -> None:
    report = _professional_report().replace(
        "证据质量 3/3。 [2]",
        "证据质量 2/3。 [2]",
        1,
    )
    result = evaluate_report(report)
    assert result["passed"] is False
    assert result["checks"]["adoption_consistency"] is False
    assert result["counts"]["inconsistent_adoption_count"] == 1

    report = _professional_report().replace("边界条件。 [1]", "边界条件。。 [1]", 1)
    result = evaluate_report(report)
    assert result["passed"] is False
    assert result["checks"]["forbidden_noise"] is False
