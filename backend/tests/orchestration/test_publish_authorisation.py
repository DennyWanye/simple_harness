# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P3.2 P32-14: nothing is published into a directory a person did not authorise.

The publish connector is enabled only when the user named a directory, it exists, and it
can carry a hard link — the connector's only atomic commit point.  Without all three it is
not enabled at all, so a Mission may not even carry a ``action:file_publish…`` criterion:
the refusal happens at the door, not after a model has already produced a candidate.
"""

from __future__ import annotations

import json

import pytest

from deskpet.orchestration.manifest import MANIFEST_NAME
from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService
from deskpet.orchestration.settings import OrchestrationSettings

from ._support import notes_provider, notes_request

PUBLISH_CRITERION = "action:file_publish.publish:reports/weekly.md"


async def _service(root, principal, **settings):
    service = OrchestrationService(
        root,
        OrchestrationSettings(**settings),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    return service


@pytest.mark.asyncio
async def test_without_an_authorised_directory_nothing_can_be_published(
    orchestration_root, principal
):
    service = await _service(orchestration_root, principal)
    try:
        status = service.status()
        assert status["publish"] == {"enabled": False, "reason": "未授权发布目录"}
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(
                notes_request("k-publish", success_criteria=["file:NOTES.md", PUBLISH_CRITERION])
            )
        # refused at the door with the precise reason, not a generic bad request
        assert refused.value.code == "action_criteria_disabled"
        manifest = json.loads((orchestration_root / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["features"]["publish"]["enabled"] is False
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_directory_the_user_authorised_enables_publishing(
    orchestration_root, principal, tmp_path
):
    published = tmp_path / "reports"
    published.mkdir()
    service = await _service(orchestration_root, principal, publish_dir=str(published))
    try:
        status = service.status()
        assert status["publish"] == {"enabled": True, "root": str(published)}
        created = service.create_mission(
            notes_request("k-publish-ok", success_criteria=["file:NOTES.md", PUBLISH_CRITERION])
        )
        assert created["mission_id"]
        manifest = json.loads((orchestration_root / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["features"]["publish"] == {"enabled": True, "root": str(published)}
        assert "file_publish" in manifest["features"]["deployment_policy"]["enabled_connectors"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_directory_that_does_not_exist_is_not_authorised(
    orchestration_root, principal, tmp_path
):
    service = await _service(orchestration_root, principal, publish_dir=str(tmp_path / "nope"))
    try:
        publish = service.status()["publish"]
        assert publish["enabled"] is False and publish["reason"] == "授权目录不存在"
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(
                notes_request(
                    "k-publish-missing", success_criteria=["file:NOTES.md", PUBLISH_CRITERION]
                )
            )
        assert refused.value.code == "action_criteria_disabled"
    finally:
        await service.close()
