# Visible Cytoscape viewport in the primary memory scroll pane

2026-09-06. plan-status: finalized under user's scoped native defect repair.
Base65a604f8. Native remains coordinator-owned; no Provider/mutation.

Observed with original component and actual Cytoscape/WebKit: in the primary50%
scroll pane, controls leave only the top57px of a340px canvas visible. Nonzero
node pixels exist below that clipped strip. Wheel events over the strip are
consumed by graph zoom, preventing expected page scroll. The provided native
PNG at time of inspection shows a large filtered node, so it does not prove
renderer pixel loss. Cytoscape3.34.2 itself supplies relative positioning; no
unsupported claim that adding position:relative repairs a native engine bug.

Small correction: canvas uses min(340px,40vh), verified in the two stated half-height
pane layouts (not an arbitrary-window guarantee); reveal it on first open/explicit fit (never steal an active filter input), wheel scrolls
the containing page. Explicit zoom buttons and drag pan/select remain available.
No altered graph data, authority, filters, request/ACK or lifecycle semantics.

Oracle before code: actual WebKit and existing React component in1000x700 and
800x560 nested primary layout. Initial visible canvas must contain node pixels;
wheel over canvas must scroll ancestor; fit reveals canvas; actual pixel click
selects a node/detail; filtering while typing retains focus; +/- zoom and panel
remount continue working. Original code must fail the visibility/scroll checks. Ordinary producer refresh and
owner-keyed remount must not auto-reveal again. The once-only flag is layout state
in the panel, never cross-owner memory data.
Focused frontend/typecheck/build plus independent review. Native exact-build
confirmation is separate; fixture layout does not establish native or HM-AC6 PASS.
