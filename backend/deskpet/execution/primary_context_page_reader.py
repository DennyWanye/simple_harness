"""主对话历史页读取器（阶段 6E 从 primary_context_pages 挪出：它要用依赖校验与当前工具页，底层页面准入模块因此不再依赖它们）。"""

from __future__ import annotations

import aiosqlite
from simple_harness import thaw_json

from deskpet.execution.current_tool_pages import PREFIX as CURRENT_PREFIX
from deskpet.execution.current_tool_pages import admitted_current_page
from deskpet.execution.primary_context_pages import PrimaryContextPageUnavailable, admitted_page
from deskpet.execution.primary_dependencies import read_run_dependencies
from deskpet.execution.primary_dependency_proof import dependencies
from deskpet.memory.trusted_disclosure import resolve_current_disclosure
from deskpet.sdk_adapters.tools import active_product_tool_context


class PrimaryContextPageReader:
    def __init__(self, db_path, *, stack_getter, policy_factory):
        self.path, self.stack_getter, self.policy_factory = db_path, stack_getter, policy_factory

    async def __call__(self, arguments):

        context = active_product_tool_context()
        if context is None or context.effect_id is None:
            raise PrimaryContextPageUnavailable("primary_page_tool_context_missing")
        sdk_run_id = context.run_id.value
        stack = self.stack_getter()
        start, (effect,) = stack.read_primary_dependency_facts(
            sdk_run_id, (context.effect_id.value,)
        )
        if (
            effect is None
            or effect.run_id != context.run_id
            or effect.call_id != context.call_id
            or effect.tool_name != "context_page_in"
            or effect.task_execution_envelope != context.task_execution_envelope
            or thaw_json(effect.arguments) != dict(arguments)
        ):
            raise PrimaryContextPageUnavailable("primary_page_tool_identity_mismatch")
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT r.host_run_id,r.subject,r.primary_conversation_id "
                "FROM foreground_runs r JOIN foreground_run_sdk_bindings b USING(host_run_id) WHERE b.sdk_run_id=?",
                (sdk_run_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            if len(rows) != 1:
                raise PrimaryContextPageUnavailable("primary_page_host_run_missing")
            run = rows[0]

            if arguments["reference_id"].startswith(CURRENT_PREFIX):
                result = await admitted_current_page(
                    db=db,
                    stack=stack,
                    run=run,
                    sdk_run_id=sdk_run_id,
                    page_effect=effect,
                    arguments=arguments,
                )

                # Includes the target's output dependencies, not merely the
                # prefix before the target. The page must follow that source.
                _, proof = await read_run_dependencies(
                    db=db,
                    stack=stack,
                    sdk_run_id=sdk_run_id,
                    before_effect_id=effect.effect_id.value,
                )
            else:
                result = await admitted_page(
                    db=db,
                    stack=stack,
                    run=run,
                    sdk_run_id=sdk_run_id,
                    start=start,
                    arguments=arguments,
                )
                source = result["source"]
                proof = dependencies(
                    [dict(evidence_id=source["evidence_id"], envelope_hash=source["envelope_hash"])]
                )
            disclosure = await resolve_current_disclosure(
                db_path=self.path,
                subject=run["subject"],
                run_id=sdk_run_id,
                request_id=context.request_id.value,
            )
            policy = self.policy_factory(run["subject"])
            if not await policy.check_dependencies(
                db=db,
                primary_ref=run["primary_conversation_id"],
                dependencies=proof,
                disclosure_context=disclosure,
            ):
                raise PrimaryContextPageUnavailable("primary_page_source_not_visible")
        current = await resolve_current_disclosure(
            db_path=self.path,
            subject=run["subject"],
            run_id=sdk_run_id,
            request_id=context.request_id.value,
        )
        if current != disclosure:
            raise PrimaryContextPageUnavailable("primary_page_disclosure_changed")
        return result
