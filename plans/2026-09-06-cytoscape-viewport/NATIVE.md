# Native viewport recovery and unresolved forget acknowledgement

2026-09-06. Actual native backend9b57c5c8eb17843d8d429bc8414cc99eb20559b7,
installed H0.7.2/M0.6.12/S0.3.12, frontend rebuilt with18120. Binary SHA256
7b2c29fb96e9930897047418ccd50f75318d62eee0ed5fa6713e04ad5e42985a.
Source c9907e14 viewport fix independently accepted; both integrated candidates
retain byte-identical three product frontend files. Original65a app exited0.

Actual UI with original userdata: graph opens with7 real nodes/0 relations; coordinate
click selects the real autumn preference and highlights its node. Zoom changes visible
geometry; filter favorite_season yields1node; fit reveals it; ordinary wheel reaches
selected details and actual USER source hashes. No new CREATE/test chat was sent.
Viewport/selection/zoom/filter/source-details checks PASS at this specific scope.
Seven-node labels overlap: dense readability is an outstanding limitation.
This data has no relations, so no native relation generation/correction claim.

Actual forget removes the autumn memory and related ordinary history, but the UI
remains unknown. One visible same-action retry causes invalidation/reload and returns
to unknown again; graph correctly blocks old content, but cannot recover availability.
Native complete forget/reopen flow remains FAIL; do not clear pending or infer success
from absence. Reviewer independently traced state=null parent unmount cancelling the
CognitiveRequests write listener before the same-owner ACK. Fix and full parent-React
invalidation-before-ACK regression are being implemented separately. No fake ACK or
weakened owner/disconnect boundary. Old action/source/failure evidence preserved.

Raw main checkout evidence keeps the launcher's historical ignored date root:
`.local-test-evidence/2026-09-05/human-memory-resume/primary-ui-q9xgypw1/`.
Earlier per-step AX files are mostly incremental no-change notices, not complete trees;
retain them as such. 08 is a fresh full tree with disableDiffing=true. App remained open
at this checkpoint (PID3255/launcher6394); do not launch a second backend on18120.

| File | SHA256 |
|---|---|
| launch.json | 27a240740dae8b8d6b2484ef5131a6c60e6ea36c3407ed34cc978070706c1478 |
| 01-graph-visible.png | bf24375fda407ab125a5a6deeb7a3d61851852f486a5dd304ee54ee6e6ba3177 |
| 02-node-selected.png | de8caf58fec4a560b22276620dd60d90d35d843623b2e36127ea600e05d04701 |
| 03-zoom.png | 537a8a3d1cafbf13d21292cee825fdece75e6710c28c1ae492ae745f0976898b |
| 04-filtered-node.png | b1cbf12f2049ce456e254e4e2e6d10049ab4d46a4fdfd6b90e962ab188f4990e |
| 05-source-details.png | e439d47facbf06392635d5354cb0d4446fed69f27aa958dbb154f99792d13250 |
| 08-forget-blocked-full.ax.txt | 7ae640292e34193c84a3c52afb49e9daeb0145879fc35d05204dff7a2dd69498 |
| 08-forget-blocked-full.png | d3d14fda39860af4b653deb224b50e84074c71d699c660bc4699c175f318d611 |
