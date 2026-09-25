from __future__ import annotations

import io
import json
import os
import stat
import zipfile

import httpx
import pytest

from deskpet.capabilities.skill_source import (
    BoundedGitHubSkillSource,
    GitHubSkillSourceLimits,
    ResolvedSkillSourceEvidence,
    normalize_github_repo_url,
)
from deskpet.capabilities.source import CapabilitySourceError

SHA = "a" * 40


def _repo_archive(files: dict[str, bytes], *, special: tuple[str, int] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, payload in files.items():
            archive.writestr(f"owner-repo-{SHA[:7]}/{path}", payload)
        if special is not None:
            path, mode = special
            info = zipfile.ZipInfo(f"owner-repo-{SHA[:7]}/{path}")
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, b"target")
    return output.getvalue()


def _skill(name: str, *, description: str = "test", extra: str = "") -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n# {name}\n"
    ).encode()


def _transport(archive: bytes, *, metadata: bytes | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(
                200,
                content=metadata or json.dumps({"sha": SHA}).encode(),
                request=request,
            )
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={"location": f"https://codeload.github.com/owner/repo/legacy.zip/{SHA}"},
                request=request,
            )
        return httpx.Response(200, content=archive, request=request)

    return httpx.MockTransport(handler)


async def _resolve(archive: bytes, **kwargs):
    async with httpx.AsyncClient(
        transport=_transport(archive), follow_redirects=False
    ) as client:
        return await BoundedGitHubSkillSource(client=client, **kwargs).resolve(
            "https://github.com/owner/repo"
        )


def test_normalizes_only_public_https_github_repository_urls() -> None:
    assert normalize_github_repo_url("https://github.com/Owner/repo.git/") == (
        "https://github.com/Owner/repo",
        "Owner",
        "repo",
    )
    for value in (
        "http://github.com/o/r",
        "https://evil.example/o/r",
        "https://user@github.com/o/r",
        "https://github.com/o/r/tree/main",
        "https://github.com/o/r?q=1",
    ):
        with pytest.raises(CapabilitySourceError) as caught:
            normalize_github_repo_url(value)
        assert caught.value.code == "github_url_invalid"


@pytest.mark.asyncio
async def test_single_skill_is_exact_deterministic_and_validator_accepted() -> None:
    archive = _repo_archive(
        {
            "SKILL.md": _skill("solo"),
            "references/guide.md": b"guide",
        }
    )
    first = await _resolve(archive)
    second = await _resolve(archive)

    assert first.batch_digest == second.batch_digest
    assert [item.skill_name for item in first.packs] == ["solo"]
    assert first.packs[0].archive_bytes == second.packs[0].archive_bytes
    assert first.evidence.exact_commit == SHA
    assert first.evidence.normalized_url == "https://github.com/owner/repo"
    with zipfile.ZipFile(io.BytesIO(first.packs[0].archive_bytes)) as package:
        assert package.namelist() == [
            "deskpet-pack.json",
            "skills/SKILL.md",
            "skills/references/guide.md",
        ]
        manifest = json.loads(package.read("deskpet-pack.json"))
        assert manifest["source"]["revision"] == SHA
        assert manifest["entries"]["skills"][0]["id"] == "solo"
        assert manifest["version"] == (
            f"0.0.0+git.{SHA[:12]}.simpleharness.pkg2"
        )


@pytest.mark.asyncio
async def test_declared_version_is_namespaced_by_packager_revision() -> None:
    result = await _resolve(
        _repo_archive(
            {"SKILL.md": _skill("solo", extra="version: 1.2.3+upstream.4\n")}
        )
    )
    with zipfile.ZipFile(io.BytesIO(result.packs[0].archive_bytes)) as package:
        manifest = json.loads(package.read("deskpet-pack.json"))
    assert manifest["version"] == "1.2.3+upstream.4.simpleharness.pkg2"


@pytest.mark.asyncio
async def test_full_commit_bypasses_rate_limited_github_api() -> None:
    archive = _repo_archive({"SKILL.md": _skill("solo")})
    requested_urls: list[str] = []

    def direct_codeload_only(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(403, request=request)
        assert request.url.host == "codeload.github.com"
        assert request.url.path == f"/owner/repo/zip/{SHA}"
        return httpx.Response(200, content=archive, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(direct_codeload_only),
        follow_redirects=False,
    ) as client:
        result = await BoundedGitHubSkillSource(client=client).resolve(
            "https://github.com/owner/repo",
            requested_ref=SHA.upper(),
        )

    assert result.evidence.requested_ref == SHA.upper()
    assert result.evidence.exact_commit == SHA
    assert requested_urls == [f"https://codeload.github.com/owner/repo/zip/{SHA}"]


@pytest.mark.asyncio
async def test_multi_skill_preserves_sibling_resource_layout() -> None:
    result = await _resolve(
        _repo_archive(
            {
                "skills/zeta/SKILL.md": _skill("zeta"),
                "skills/zeta/note.md": b"zeta",
                "skills/alpha/SKILL.md": _skill("alpha"),
                "skills/alpha/child/SKILL.md": _skill("child"),
                "skills/alpha/child/private.md": b"child only",
                "README.md": b"repo",
            }
        )
    )
    assert [item.skill_name for item in result.packs] == ["alpha", "child", "zeta"]
    alpha = next(item for item in result.packs if item.skill_name == "alpha")
    with zipfile.ZipFile(io.BytesIO(alpha.archive_bytes)) as package:
        assert "skills/alpha/SKILL.md" in package.namelist()
        assert "skills/alpha/child/SKILL.md" in package.namelist()
        assert "skills/alpha/child/private.md" in package.namelist()
        assert "skills/zeta/note.md" in package.namelist()
        manifest = json.loads(package.read("deskpet-pack.json"))
        assert manifest["entries"]["skills"][0]["path"] == "skills/alpha/SKILL.md"


@pytest.mark.asyncio
async def test_multi_skill_relative_support_reference_survives_packaging() -> None:
    result = await _resolve(
        _repo_archive(
            {
                "skills/plan-bs/SKILL.md": _skill(
                    "plan-bs", description="brainstorm"
                )
                + b"Read ../plan-test/config.md\n",
                "skills/plan-test/SKILL.md": _skill("plan-test"),
                "skills/plan-test/config.md": b"shared config",
            }
        )
    )
    plan_bs = next(item for item in result.packs if item.skill_name == "plan-bs")
    with zipfile.ZipFile(io.BytesIO(plan_bs.archive_bytes)) as package:
        assert package.read("skills/plan-test/config.md") == b"shared config"
        manifest = json.loads(package.read("deskpet-pack.json"))
        assert manifest["entries"]["skills"][0]["path"] == "skills/plan-bs/SKILL.md"


@pytest.mark.asyncio
async def test_batch_name_collision_fails_closed() -> None:
    duplicate = _repo_archive(
        {
            "one/SKILL.md": _skill("Same"),
            "two/SKILL.md": _skill("same"),
        }
    )
    with pytest.raises(CapabilitySourceError) as caught:
        await _resolve(duplicate)
    assert caught.value.code == "skill_name_collision"

    with pytest.raises(CapabilitySourceError) as caught:
        async with httpx.AsyncClient(
            transport=_transport(_repo_archive({"SKILL.md": _skill("solo")}))
        ) as client:
            await BoundedGitHubSkillSource(client=client).resolve(
                "https://github.com/owner/repo", visible_skill_names=("SOLO",)
            )
    assert caught.value.code == "skill_name_collision"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("archive", "expected"),
    [
        (_repo_archive({"../SKILL.md": _skill("escape")}), "capability_package_limit_exceeded"),
        (
            _repo_archive(
                {"SKILL.md": _skill("safe")},
                special=("link", stat.S_IFLNK | 0o777),
            ),
            "capability_package_limit_exceeded",
        ),
        (_repo_archive({"README.md": b"empty"}), "skill_source_empty"),
    ],
)
async def test_malicious_or_empty_archive_rejects_whole_batch(
    archive: bytes, expected: str
) -> None:
    with pytest.raises(CapabilitySourceError) as caught:
        await _resolve(archive)
    assert caught.value.code == expected


@pytest.mark.asyncio
async def test_redirect_host_and_actual_stream_byte_caps_are_enforced() -> None:
    async def bad_redirect(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": SHA}, request=request)
        return httpx.Response(
            302,
            headers={"location": "https://evil.example/archive.zip"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_redirect)) as client:
        with pytest.raises(CapabilitySourceError) as caught:
            await BoundedGitHubSkillSource(client=client).resolve(
                "https://github.com/owner/repo"
            )
    assert caught.value.code == "github_redirect_rejected"

    archive = _repo_archive(
        {"SKILL.md": _skill("solo"), "large.bin": os.urandom(2048)}
    )
    limits = GitHubSkillSourceLimits(archive_bytes=512)
    with pytest.raises(CapabilitySourceError) as caught:
        await _resolve(archive, limits=limits)
    assert caught.value.code == "github_archive_too_large"


@pytest.mark.asyncio
async def test_declared_metadata_cap_and_zip_bomb_ratio_are_enforced() -> None:
    def oversized_metadata(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "999999"},
            content=b"{}",
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(oversized_metadata)
    ) as client:
        with pytest.raises(CapabilitySourceError) as caught:
            await BoundedGitHubSkillSource(
                client=client,
                limits=GitHubSkillSourceLimits(metadata_bytes=128),
            ).resolve("https://github.com/owner/repo")
    assert caught.value.code == "github_metadata_too_large"

    compressed = _repo_archive(
        {"SKILL.md": _skill("solo"), "payload.txt": b"a" * 32_000}
    )
    with pytest.raises(CapabilitySourceError) as caught:
        await _resolve(
            compressed,
            limits=GitHubSkillSourceLimits(
                max_archive_entry_compression_ratio=2.0
            ),
        )
    assert caught.value.code == "capability_package_limit_exceeded"


def test_resolved_evidence_cannot_be_forged() -> None:
    with pytest.raises(CapabilitySourceError) as caught:
        ResolvedSkillSourceEvidence(
            normalized_url="https://github.com/o/r",
            requested_ref="HEAD",
            exact_commit=SHA,
            archive_hash="b" * 64,
            raw_file_set_digest="c" * 64,
            selected_subdirectories=(".",),
            evidence_hash="d" * 64,
        )
    assert caught.value.code == "skill_source_evidence_host_only"


async def _resolve_subpath(archive: bytes, subpath: str, **kwargs):
    async with httpx.AsyncClient(
        transport=_transport(archive), follow_redirects=False
    ) as client:
        return await BoundedGitHubSkillSource(client=client, **kwargs).resolve(
            "https://github.com/owner/repo", subpath=subpath
        )


@pytest.mark.asyncio
async def test_subpath_installs_only_that_skill_and_limits_count_only_it() -> None:
    # 2026-09-25 UI 全量点击：市场条目指向 anthropics/skills 的一个子目录，以前整个
    # 仓库一起打包、文件数超限。只取子目录；目录外的文件再多也不计入限额、不被读取。
    files = {f"other/f{i}.md": b"x" for i in range(600)}
    files.update({
        "skills/pdf/SKILL.md": _skill("pdf"),
        "skills/pdf/reference.md": b"pdf ref",
        "skills/docx/SKILL.md": _skill("docx"),
    })
    result = await _resolve_subpath(_repo_archive(files), "skills/pdf")
    assert [item.skill_name for item in result.packs] == ["pdf"]
    with zipfile.ZipFile(io.BytesIO(result.packs[0].archive_bytes)) as package:
        names = package.namelist()
    assert any(name.endswith("reference.md") for name in names)
    assert not any(name.startswith("other/") or "docx" in name or name.endswith("f0.md") for name in names)


@pytest.mark.asyncio
@pytest.mark.parametrize("subpath", ["../etc", "skills/../..", "a\\\\b"])
async def test_subpath_rejects_traversal(subpath: str) -> None:
    with pytest.raises(CapabilitySourceError) as caught:
        await _resolve_subpath(_repo_archive({"skills/pdf/SKILL.md": _skill("pdf")}), subpath)
    assert caught.value.code == "skill_source_subpath_invalid"


@pytest.mark.asyncio
async def test_subpath_without_files_is_refused() -> None:
    with pytest.raises(CapabilitySourceError) as caught:
        await _resolve_subpath(_repo_archive({"skills/pdf/SKILL.md": _skill("pdf")}), "skills/xlsx")
    assert caught.value.code == "skill_source_subpath_empty"
