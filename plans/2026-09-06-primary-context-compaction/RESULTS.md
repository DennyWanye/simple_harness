# History page scoped results

Updated 2026-09-06. Product 9b4c8ace; existing H077/M616 installed targets plus this Host source. No frozen artifact, main checkout or original userdata changes. Actual HTTP MockTransport, not native or external Provider evidence.

| Batch | Result | Scope |
|---|---|---|
|r1|1 PASS / 3 FAIL, 6.33s|Independent UTF-8 bytes oracle passed. Real-file fixture exceeded the existing 3000-character write cap.|
|r2|1 FAIL, 3.42s|Real bounded append fixed; fixture selected the first of two different large-result summaries.|
|r3|1 FAIL, 4.34s|Three actual pages worked. Wrong hash produced SDK failed/value=None; executor and new dependency reader incorrectly expected a success value.|
|r4|1 PASS, 4.20s|Actual source, first/next/tail pages, wrong-hash rejection, next physical send and full-stack reopen dependency read.|
|r5|2 PASS, 6.08s|Only original forget failures: forget before excerpt gives zero new sends; forget after page blocks next send.|

**4 unique PASS**, without adding retries. Dirac gave scoped source/results ACCEPT at history536daece; main has merged that history leaf independently. PG1442 and PG1574 exited 0 with remaining=[] and cleanup_error=null. Shared slot released.

Reproduction: existing typed-use-primary/venv074614/bin/python invokes main candidate scripts/run_resource_bounded.py --evidence-dir <new-dir> -- <same-python> -I -B .local-test-evidence/2026-09-06/primary-context-pages/run_tests.py <new-basetemp> -k <target>. The ignored launcher explicitly loads existing H077/M616 installed targets and this Host source; it is not a new full environment identity gate.

Current Run page:causal remains unfinished and continues in this leaf. These four controls do not cover current Run pending/cross-turn source controls, external Provider or native. Full S5/program is not complete.

Raw evidence (ignored, local):
- `.local-test-evidence/2026-09-06/primary-context-pages/r1/command.log` SHA256 `d90cfb0de73b19e540a1504b6206316fcbbdc903bad0bbecfccac29a67dc8222`
- `.local-test-evidence/2026-09-06/primary-context-pages/r1/resource.json` SHA256 `64b946ce227258fbd7294911c72b17e9b64cd4e4adc344c43edc483db0619f06`
- `.local-test-evidence/2026-09-06/primary-context-pages/r2/command.log` SHA256 `b95b4415bba997a397b8cbef96acbb6227a6b7c18268c92eccf4f2f7f6baba9b`
- `.local-test-evidence/2026-09-06/primary-context-pages/r2/resource.json` SHA256 `c89ae403d93daec27f85b55f962a1aec961d58f33ff5c80c280cc78ed1d0f666`
- `.local-test-evidence/2026-09-06/primary-context-pages/r3/command.log` SHA256 `bb8b8a53ddc4dbb18cdb4aec0f71b80721fd2bb91adc77ec0312d64d39d43635`
- `.local-test-evidence/2026-09-06/primary-context-pages/r3/resource.json` SHA256 `8c7c373df0bc18eb91474ea0162a4e4c78e2f83bfca2cee29ad396eca3aa63fa`
- `.local-test-evidence/2026-09-06/primary-context-pages/r4/command.log` SHA256 `c941822639733085b4db42315626b9a0ae4a170eb6a6f6aff5ec005620e45e4b`
- `.local-test-evidence/2026-09-06/primary-context-pages/r4/resource.json` SHA256 `98a2380035223f831df7ed6edcf3c6659b54eab6274ff8713a3737e3544a1277`
- `.local-test-evidence/2026-09-06/primary-context-pages/r5/command.log` SHA256 `c5f2daec5d3fcf8c6bb32d55fc5f56687dab9f69b3bfdf119e52d6af8a6f5ee3`
- `.local-test-evidence/2026-09-06/primary-context-pages/r5/resource.json` SHA256 `799aadd569e433125cd6047041b7eb64ca4456346defdb4e53860b793dadd63e`
