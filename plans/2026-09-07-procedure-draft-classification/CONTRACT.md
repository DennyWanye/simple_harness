# Candidate Procedure classification — Host prompt v5.1

2026-09-07; base e1b98baffb840319a20377787b7abda66f01989d. Focused controls6PASS; see [results](RESULTS.md).
Actual model controls remain NOT_RUN. C05 is preserved separately at0e9a4d4a.

## Observed problem

Main/Dirac traced r24 Host attempt5061c3be-a763-5d38-9dee-09ba2573b347,
request20147a…: original USER quote was intact but the actual model proposed
only episode/create. No compiler downgrade occurred. Old response and native
FAIL are retained and will not be reclassified or replayed as a new success.

v5 inherits the tool description “a reusable how-to the user follows”, implying
adopted use, and emphasizes concrete file requests as episode/semantic. The
later v4 rule supports uncertain→DRAFT but does not explicitly instruct creation
of a candidate for a user-described tentative reusable workflow.

## Minimal change

New `analysis_proposal_v5_1` changes both real system instruction and tool
description. Explicit self-described reusable multi-step candidate/trial flow
with complete USER steps should yield uncertain/DRAFT, possibly alongside an
episode. One-off tasks, assistant plans, and quoted other-person suggestions
are not automatically user Procedures. No invented steps/adoption/success;
undecided is never adoption/ACTIVE; classification is not execution permission.

New worker default exact tuple is `(prompt/v5.1, schema/v5, policy/v5)`.
v3/v4/v5 modules are unchanged. The new compiler checks that tuple and invokes
v4 `_compile_validated_proposal` with the original request and v5 discriminator.
No request/hash rewriting, SDK change, wire/schema mutation or old job rewrite.
Old exact tuple selects its original prompt, tool description and compiler.

## Necessary verification (focused controls executed; model calls pending)

- New/old exact request dispatch and differing request hashes, identical schema,
  actual new tool description; old v5 text retained.
- Two Chinese complete tentative/trial examples scripted as uncertain compile
  DRAFT. Removing undecided/no-execution context fails full-USER quote binding.
- Ordinary one-off episode remains episode; compiler does not synthesize a
  Procedure. This is not evidence that the model always classifies it correctly.
- One actual v5 response-only persisted failure, then new5.1 worker recovery:
  original request semantics retained, saved response reused, zero new Provider.
  Existing helper extended for v5 discriminator; old test groups not rerun.
- Later at least two real model calls under the actual new prompt: tentative
  reusable flow positive and one-off/assistant-plan negative. These—not static
  text assertions or scripted classifications—establish classification behavior.

The one authorized focused batch completed and released its slot. No model slot taken. No gold or full Case
is sent to the model; actual positive/negative inputs will be reviewed before
the separately scheduled calls. Until then this is not native/quality closure.
