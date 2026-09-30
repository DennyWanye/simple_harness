# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""收尾前复查"通过的依据还有效吗"（完成度评估第 4 项，2026-10-01）。

通过证书的读集分四类：具体对象 OBJECT（按 kind / id / revision 定死、正文不可变）、固定查询集
QUERY_SET（整任务全表的超集）、权限 ACCESS、策略 POLICY。此前有效性观察者只比整任务的三个时钟，
任务里任何一件事都让证书"来源已变"，所以这个观察从没接到任何决定上。这里逐项重读**真正的依据**：

* OBJECT：按原样重读（同一条读取器），正文指纹不同或读不到才算变；
* QUERY_SET：只比验收公式真正消费的三张表（观察 / 依据集 / 依据成员），指纹不含时钟；
  其它表是超集噪音（事件、验收本身……），不比；
* ACCESS / POLICY：没有权限评估器，不比——权威变化由 ROOT_CHANGED 覆盖。

红线（用户 2026-10-01）：结果只用来判"仍有效 / 已过期"和拦住收尾，从不写 ``goal_resolutions.validity``。
"""
from __future__ import annotations

from typing import Any

from ..assurance.codec import AssuranceError, decode, fingerprint
from ..assurance.refs import AssuranceRef, Pin
from ..storage.assurance_reads import AssuranceReader, read_complete_evidence_snapshot

#: 验收公式（``assurance_validity._snapshot_sources``）真正消费的查询集。
EVIDENCE_QUERY_KINDS = frozenset({"observations", "justification_sets", "support_members"})
#: 收尾前要复查的证书用途：根结论、中间目标结论、每条贡献验收。
CLOSEOUT_CONSUMER_KINDS = ("ACCEPTANCE", "ROOT_RESOLUTION", "COMPOUND_RESOLUTION")
EVIDENCE_STALE = "EVIDENCE_STALE"


def live_usable_certificates(connection: Any, mission_id: str, *, limit: int) -> list[Any]:
    """每个消费方身份最新的一张证书，且是 USABLE 的（才有有效性可观察）。"""
    return connection.execute(
        "SELECT c.* FROM assurance_use_certificates c WHERE c.mission_id=? "
        "AND json_extract(c.certificate_json,'$.decision')='USABLE' "
        "AND NOT EXISTS(SELECT 1 FROM assurance_use_certificates newer "
        "WHERE newer.mission_id=c.mission_id AND newer.consumer_kind=c.consumer_kind "
        "AND newer.consumer_id=c.consumer_id AND newer.purpose=c.purpose "
        "AND newer.scope_id=c.scope_id AND newer.rowid>c.rowid) "
        "ORDER BY c.rowid LIMIT ?",
        (mission_id, limit),
    ).fetchall()


def changed_items(store: Any, *, tenant_id: str, certificate: dict[str, Any]) -> list[dict[str, str]]:
    """证书读集里真正变了的项；空列表 = 依据仍成立。须在一致读或写事务里调用。"""
    mission_id = str(certificate["mission_id"])
    reader = AssuranceReader(store, tenant_id=tenant_id, mission_id=mission_id)
    changed: list[dict[str, str]] = []
    wanted: dict[str, str] = {}
    for item in certificate["read_set"]:
        channel, key = str(item["channel"]), str(item["key"])
        if channel == "OBJECT":
            pin = decode(key)
            ref = AssuranceRef(str(pin["kind"]), Pin(str(pin["id"]), int(pin["revision"]), str(item["fingerprint"])))
            try:
                reader.read_exact_metadata(ref)
            except AssuranceError as error:
                changed.append({"channel": "OBJECT", "key": key, "reason": error.code})
        elif channel == "QUERY_SET" and decode(key).get("query_kind") in EVIDENCE_QUERY_KINDS:
            wanted[key] = str(item["fingerprint"])
    if wanted:
        try:
            current = {read.query_key: read.read_item.fingerprint
                       for read in read_complete_evidence_snapshot(reader, scope_id=str(certificate["scope_id"]))}
        except AssuranceError as error:
            current, unavailable = {}, error.code
        else:
            unavailable = "QUERY_UNAVAILABLE"
        for key, expected in sorted(wanted.items()):
            now = current.get(key)
            if now != expected:
                # 记下现在的指纹：同一张表再变一次是新的一处变化，不被"只记一次"盖住。
                changed.append({"channel": "QUERY_SET", "key": str(decode(key)["query_kind"]),
                                "reason": "SET_CHANGED" if now is not None else unavailable,
                                "current": "" if now is None else now[:16]})
    return changed


def stale_source_key(certificate_id: str, changed: list[dict[str, str]]) -> str:
    """同一张证书同一处变化只记一条修复请求。"""
    return "evidence-stale:" + fingerprint({"certificate_id": certificate_id, "changed_items": changed})


def stale_certificates(store: Any, *, tenant_id: str, mission_id: str, limit: int = 4096) -> list[dict[str, Any]]:
    """收尾要看的证书里依据已变、且还没被规划器处理掉的那些。须在一致读或写事务里调用。

    * 只看消费方还**现行**的证书（验收 validity=CURRENT；目标结论 adopted=1 且 CURRENT）：
      重做后新验收是新的消费方身份，旧证书自然不再算；
    * 由它记的修复请求已被处理（``PlanningRepairAddressed``：系统复核过或计划改过）也不再算，
      否则后继步骤那条路上旧证书会永远拦着收尾。
    """
    connection = store.connection
    handled: set[str] = set()
    requested: dict[str, str] = {}
    for event in store.iter_events(mission_id):
        if event.type == "PlanningRepairRequested":
            requested[str(event.payload.get("source_key"))] = str(event.payload.get("request_id"))
        elif event.type == "PlanningRepairAddressed":
            handled.update(str(r) for r in event.payload.get("repair_request_ids", ()))
    out: list[dict[str, Any]] = []
    for row in live_usable_certificates(connection, mission_id, limit=limit):
        kind, consumer_id = str(row["consumer_kind"]), str(row["consumer_id"])
        if kind not in CLOSEOUT_CONSUMER_KINDS:
            continue
        if kind == "ACCEPTANCE":
            current = connection.execute(
                "SELECT task_id FROM acceptances WHERE acceptance_id=? AND mission_id=? AND validity='CURRENT'",
                (consumer_id, mission_id)).fetchone()
        else:
            current = connection.execute(
                "SELECT goal_task_id FROM goal_resolutions WHERE resolution_id=? AND mission_id=? "
                "AND adopted=1 AND validity='CURRENT'", (consumer_id, mission_id)).fetchone()
        if current is None:
            continue
        certificate = decode(row["certificate_json"])
        changed = changed_items(store, tenant_id=tenant_id, certificate=certificate)
        if not changed:
            continue
        source_key = stale_source_key(str(row["certificate_id"]), changed)
        if requested.get(source_key) in handled:
            continue
        out.append({"certificate_id": str(row["certificate_id"]), "consumer_kind": kind,
                    "consumer_id": consumer_id, "scope_id": str(row["scope_id"]),
                    "task_id": str(current[0]), "changed_items": changed, "source_key": source_key})
    return out


def closeout_stale_findings(store: Any, mission_id: str) -> list[dict[str, Any]]:
    """收尾评估最近一次写下的"依据已变"清单（NOT_READY + EVIDENCE_STALE 时才有）。"""
    row = store.connection.execute(
        "SELECT state, check_body_json FROM assurance_closeouts WHERE mission_id=?", (mission_id,)).fetchone()
    if row is None or row["state"] != "NOT_READY":
        return []
    body = decode(row["check_body_json"])
    if EVIDENCE_STALE not in (body.get("reasons") or ()):
        return []
    return [dict(item) for item in body.get("stale_certificates") or ()]


__all__ = ("closeout_stale_findings", "CLOSEOUT_CONSUMER_KINDS", "EVIDENCE_QUERY_KINDS", "EVIDENCE_STALE", "changed_items",
           "live_usable_certificates", "stale_certificates", "stale_source_key")
