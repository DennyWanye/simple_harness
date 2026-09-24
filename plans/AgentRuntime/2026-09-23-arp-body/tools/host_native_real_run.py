"""Host 生产装配 + 真实模型（DeepSeek 日卡，经本机闸门 127.0.0.1:28181）+ 原生运行平面默认开启：一整趟 Mission。

用法：cd backend && PYTHONPATH=. .venv/bin/python ../plans/AgentRuntime/2026-09-23-arp-body/tools/host_native_real_run.py <证据目录> [目标文本]
密钥只在进程内从主副本 .env 读取，不写入任何输出；证据目录必须在 .local-test-evidence 下（gitignored）。
改自 Assurance 会话的 host_real_run.py（run-21），差别：Host 用本 worktree；设置里声明日卡转发主机为 DeepSeek 兼容；
结束时倾倒原生池账本（会话/创建意图/上下文计量回执/提供方调用/技能与作业表计数）。
"""
import asyncio, json, os, re, sqlite3, sys, time
from pathlib import Path

HOST = Path(__file__).resolve().parents[4]          # <worktree>
ENV_FILE = Path(os.environ.get("REAL_ENV_FILE", "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.env"))
RUN = Path(sys.argv[1]).resolve(); RUN.mkdir(parents=True, exist_ok=True)
assert ".local-test-evidence" in RUN.parts, "证据目录必须在 .local-test-evidence 下"
sys.path.insert(0, str(HOST / "backend")); os.chdir(HOST / "backend")
from deskpet.orchestration.provider import ProviderSnapshot, build_provider
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from agent_orchestrator.governance.permissions import Principal

GOAL = sys.argv[2] if len(sys.argv) > 2 else "写一份 NOTES.md，用中文列出桌面工作台「任务编排」视图的三个要点"
CRITERIA = json.loads(os.environ["REAL_CRITERIA"]) if os.environ.get("REAL_CRITERIA") else ["file:NOTES.md", "NOTES.md 是中文，恰好列出三个要点"]
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "STOPPED"}
LOG = (RUN / "driver.log").open("a", encoding="utf-8")
def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True); LOG.write(line + "\n"); LOG.flush()

def read_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        m = re.match(r"^\s*(?:export\s+)?DEEPSEEKER_APIKEY\s*=(.*)$", line)
        if m: return m.group(1).strip().strip("'\"")
    raise SystemExit("no key")

def dump_native(root: Path, summary: dict) -> None:
    """原生池账本：只读，计数 + 计量回执要点（不含请求正文）。"""
    native = {}
    for db in sorted(root.glob("execution-deepseek-native-*.db")):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
        entry: dict = {"tables": {}}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'arp_%'"):
            entry["tables"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        entry["sessions"] = [dict(r) for r in con.execute("SELECT agent_id, state, generation FROM arp_agent_sessions")]
        entry["creation_intents"] = [{"state": r["state"], "receipt": json.loads(r["original_receipt_ref_json"])["id"]} for r in con.execute("SELECT state, original_receipt_ref_json FROM arp_creation_intents")]
        entry["context_requests"] = [dict(r) for r in con.execute("SELECT provider_request_ordinal, input_charge, input_budget, output_reserve, journal_highwater FROM arp_context_requests ORDER BY provider_request_ordinal")]
        def pick(node, out):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("count_mode", "input_limit_scope", "wire_input_tokens", "prior_output_reserve_tokens", "requested_output_tokens", "coverage", "retrieval_mode", "embedding_mode"):
                        out[k] = v
                    pick(v, out)
            elif isinstance(node, list):
                for v in node: pick(v, out)
        receipts = []
        for r in con.execute("SELECT manifest_json FROM arp_context_requests ORDER BY provider_request_ordinal"):
            found: dict = {}; pick(json.loads(r[0]), found); receipts.append(found)
        entry["token_receipts"] = receipts
        entry["provider_invocations"] = [dict(r) for r in con.execute("SELECT state, substr(usage_json, 1, 200) AS usage FROM provider_invocations")]
        entry["jobs"] = [dict(r) for r in con.execute("SELECT kind, state, COUNT(*) AS n FROM arp_jobs GROUP BY kind, state")] if entry["tables"].get("arp_jobs") else []
        entry["embedding_calls"] = [dict(r) for r in con.execute("SELECT kind, COUNT(*) AS n FROM arp_original_receipts GROUP BY kind")] if any(t == "arp_original_receipts" for t in entry["tables"]) else "n/a"
        con.close(); native[db.name] = entry
    summary["native_pools"] = native

async def main():
    snapshot = ProviderSnapshot(provider_id="deepseek-daycard-gate", base_url="http://127.0.0.1:28181/v1",
                                configured_model="deepseek-v4.1-flash", requested_model="deepseek-v4.1-flash", api_key=read_key(),
                                response_model_aliases=("deepseek-ai/DeepSeek-V4.1-Flash",))
    provider, client = build_provider(snapshot, timeout=900.0, allow_private_http=True)
    root = RUN / "userdata" / "data" / "agent-orchestrator"
    settings = OrchestrationSettings(deepseek_compatible_hosts="127.0.0.1")
    service = OrchestrationService(root, settings, principal=Principal("local-user:real", "本机用户"),
                                   provider=provider, provider_snapshot=snapshot, http_client=client)
    await service.start()
    summary = {"provider": snapshot.public(), "goal": GOAL, "criteria": CRITERIA, "commands": [], "host": str(HOST)}
    started = time.monotonic(); stop_reason = None; mid = None
    try:
        st = service.status()
        log("status", st["state"], st.get("reason"), "native=", json.dumps(st.get("native_plane"), ensure_ascii=False)[:400], "default_profile=", st.get("default_context_profile_id"))
        summary["status_at_start"] = {k: st.get(k) for k in ("state", "reason", "assurance_available", "assurance_profile", "sdk_version", "native_plane", "default_context_profile_id", "context_profiles")}
        if st["state"] != "available":
            stop_reason = "service_unavailable"; return
        # Budget: the Host default for the selected profile (4M tokens for the 256K pools) unless
        # REAL_BUDGET_TOKENS is set.  A certified counter reserves the worst case per attempt
        # (input limit + output ceiling = 294,912 for 256K/32K), so run-21's 300K would fail.
        request = {"goal": GOAL, "success_criteria": CRITERIA, "idempotency_key": f"native-{RUN.name}"}
        if os.environ.get("REAL_BUDGET_TOKENS"):
            request["budget"] = {"max_tokens": int(os.environ["REAL_BUDGET_TOKENS"]), "max_attempts": 4}
        receipt = service.create_mission(request)
        mid = receipt["mission_id"]; summary["mission_id"] = mid; log("created", mid)
        summary["mission_profile"] = (service._orchestrator.store.get_mission(mid).final_report or {}).get("runtime_profile_id")
        log("mission runtime profile", summary["mission_profile"])
        last_status = None; approved_spec = False; issued = set(); passed_reviews = set()
        deadline = started + float(os.environ.get("REAL_TIMEOUT", "1500"))
        while time.monotonic() < deadline:
            d = service.mission_detail(mid)
            status = d["mission"]["status"]
            if status != last_status:
                log("mission status", status, "waiting_on=", d.get("waiting_on"), "blocked=", d.get("blocked")); last_status = status
            if status in TERMINAL: break
            ws = d.get("operation_workspace")
            if ws and ws.get("mission_id") and ws.get("state") != "APPROVED" and ws.get("editable") is True and not approved_spec:
                ref = ws["requirements_ref"]
                proposal = {"schema_version": 1, "mission_id": mid,
                            "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
                            "mode": "CONTENT_ONLY", "content_criterion_ids": [c["id"] for c in ws.get("criteria", [])], "effects": []}
                try:
                    r = service.approve_operation_completion_spec({"mission_id": mid, "expected_requirements_ref": ref, "proposal": proposal, "command_id": "cmd-spec-1"})
                    approved_spec = True; log("spec approved"); summary["commands"].append({"approve_spec": r})
                except Exception as e:
                    log("spec approve failed", type(e).__name__, str(e)[:300]); stop_reason = "spec_approve_failed"; break
            for req in d.get("planning_authorization_requests") or []:
                rid = req.get("request_id")
                if rid and rid not in issued:
                    try:
                        r = service.planning_authorization({"operation": "issue", "mission_id": mid, "request_id": rid, "command_id": f"cmd-auth-{rid}"})
                        issued.add(rid); log("planning authorized", rid); summary["commands"].append({"authorize": r})
                    except Exception as e:
                        log("authorize failed", type(e).__name__, str(e)[:300]); stop_reason = "authorize_failed"; break
            if stop_reason: break
            qs = [q for q in d.get("planning_questions") or [] if q.get("state") == "PENDING"]
            if qs:
                log("planning question pending; stopping", json.dumps(qs, ensure_ascii=False)[:500]); summary["planning_questions"] = qs; stop_reason = "planning_question"; break
            pend = [a for a in d.get("approvals") or [] if a.get("state") == "PENDING"]
            for a in pend:
                if a.get("kind") == "review" and a["request_id"] not in passed_reviews:
                    service.decide(a["request_id"], "review_pass", note="真实模型验收：复核由测试执行者按预设通过，产物保存在证据里供事后核对")
                    passed_reviews.add(a["request_id"]); log("review passed by operator", a["request_id"], a.get("summary"))
            other = [a for a in pend if a.get("kind") != "review"]
            if other:
                log("waiting on person (non-review); stopping", json.dumps(other, ensure_ascii=False)[:500]); summary["waiting_on_person"] = other; stop_reason = "waiting_on_person"; break
            await asyncio.sleep(3)
        else:
            stop_reason = "timeout"
        d = service.mission_detail(mid)
        summary.update(elapsed_s=round(time.monotonic() - started, 1), mission=d["mission"], attempts=d["attempts"], results=d["results"],
                       blocked=d["blocked"], waiting_on=d["waiting_on"], usage=d["usage"], event_count=d["event_count"],
                       operation_workspace_state=(d.get("operation_workspace") or {}).get("state"))
        arts = []
        for art in d["artifacts"]:
            try: arts.append(service.artifact_read(art["id"]))
            except Exception as e: arts.append({"id": art.get("id"), "read_error": str(e)})
        summary["artifacts"] = arts
        events = service.events(mid, limit=300)["events"]
        (RUN / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=1))
        summary["event_types"] = [e["type"] for e in events]
        try:
            from agent_orchestrator.storage.assurance_store import AssuranceStore
            summary["assurance_lane"] = AssuranceStore(service._orchestrator.store).lane(mid)
        except Exception as e:
            summary["assurance_lane_error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        summary["stop_reason"] = stop_reason
        try:
            orch = service._orchestrator
            if orch is not None:
                (RUN / "orchestrator-progress.log").write_text("\n".join(str(x) for x in orch.progress_log))
        except Exception as error:
            (RUN / "orchestrator-progress.log").write_text(f"unavailable: {error}")
        await service.close()
        try:
            dump_native(root, summary)
        except Exception as error:
            summary["native_dump_error"] = f"{type(error).__name__}: {error}"
        (RUN / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
        log("DONE status=", (summary.get("mission") or {}).get("status"), "stop_reason=", stop_reason, "lane=", summary.get("assurance_lane"),
            "native=", json.dumps({k: (v.get("tables", {}).get("arp_agent_sessions"), len(v.get("context_requests", []))) for k, v in (summary.get("native_pools") or {}).items()}))
asyncio.run(main())
