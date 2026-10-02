import json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, ".")
sys.path.insert(0, "tests/orchestration")
import _pool_identity as P
from _word_counter import FixtureWordCounter
from agent_orchestrator.runtime.assembly import OrchestratorConfig
import deskpet.orchestration.runtime_profile as rp
from deskpet.orchestration.native_plane import HostNativePlane
class Prov:
    async def invoke(self, request, *, cancel=None):
        raise RuntimeError('not used')


def build(tmp: Path, *, models: bool, snapshot: bool):
    native = HostNativePlane(tenant_id=P.TENANT, principal_id=P.PRINCIPAL, allowed_tools=P.TOOLS,
                             models_dir=P.fake_models_dir(tmp) if models else None,
                             meter_factory=P.stub_meter_factory, clock_ms=lambda: 1)
    config = OrchestratorConfig(evidence_root=tmp / "root", model=P.MODEL, price_table=None)
    original = rp.deepseek_counter_for
    rp.deepseek_counter_for = lambda snap, settings=None, **kw: FixtureWordCounter() if snap is not None else None
    try:
        snap = SimpleNamespace(requested_model=P.MODEL, base_url="https://api.deepseek.com") if snapshot else None
        options = rp.source_runtime_options(config, Prov(), snap, native=native,
                    native_test_counter=None if snapshot else FixtureWordCounter(),
                    thinking_provider=Prov() if snapshot else None)
    finally:
        rp.deepseek_counter_for = original
    return P.identity(native, options, tmp / "scratch")

out = {}
for models in (False, True):
    for snapshot in (False, True):
        with tempfile.TemporaryDirectory() as d:
            out[f"models={models},snapshot={snapshot}"] = build(Path(d), models=models, snapshot=snapshot)
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
print({k: sorted(v["pools"]) for k, v in out.items()})
