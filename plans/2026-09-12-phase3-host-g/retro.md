# Execution retrospective

2026-09-14 P36: independent review caught unsafe raw SDK report copying; actual native testing caught old document fixture drift, incomplete in-flight cost wording and receipt overflow. Root `tsc --noEmit` was a no-op; replaced with actual project `npm run typecheck`, which caught a missing closing brace. Broad concatenated doc/log reads wasted tokens; use bounded sections and explicit output projections. Native elapsed time includes reporting/waits and must not be called active work. No additional user approval needed within existing scope.
