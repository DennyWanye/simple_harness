# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""User decision 2026-09-26: an UNKNOWN charge is counted at its upper bound at closeout.

Desktop run (thermos Mission): every criterion was met and the root review accepted,
but one failed attempt's UNKNOWN charge kept its 57k reservation held forever, so the
closeout stayed DRAINING and the Mission never ended.  The charge is now counted at
the larger of the reservation and the known facts — never less — and the Mission can
close; the usage fact itself stays unknown (the truth).

HTN 补齐阶段 A′（2026-10-03）：原来 5 条直接在裸 ``CommitService`` 上手写预留、用量、意图与
"等原调用对账"回执（①b2，产品之外造状态），现在都由产品同形用例在真实主循环里守：

* 1（未知用量按上限计入）、3（收尾按上限计入）→ ``p35/test_provider_accounting_loop.py``
  ``test_a_charge_nobody_can_state_is_held_at_its_bound_and_counted_at_closeout``；
* 4、5（只等答不上来的原调用的审阅不挡收尾）→ ``p35/test_provider_accounting_restart.py``
  ``test_a_review_whose_call_died_with_the_process_does_not_hold_the_closeout``；
* 2（已知事实超过预留不截）与 3 的反向分支：产品里造不出那种组合，删除，理由见
  ``p35/test_provider_accounting_restart.py`` 文件头。

本文件只留下面这条纯常量检查（E）。
"""

from __future__ import annotations


def test_a_resume_under_new_code_re_evaluates_the_closeout():
    """Desktop: the thermos Mission sat DRAINING under old code; after the upgrade
    nothing new happened on it, so the new counting rule never ran."""

    from agent_orchestrator.orchestrator.assurance_consumers import CLOSEOUT_SOURCE_EVENTS

    assert "PolicyInterpreterDrift" in CLOSEOUT_SOURCE_EVENTS
