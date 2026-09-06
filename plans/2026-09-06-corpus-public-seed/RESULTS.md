# Public seed results

2026-09-06. Three unique focused controls, with real failures retained.

- r1: 1FAIL/1PASS. Missing principal-owner registration; pure setup input rejection
  passed. PG6461 exit1,1.515s,192384KiB,remaining[].
- r2: 2FAIL,1deselected. Source-only admission lacks mutation ingestion receipt.
  PG6574 exit1,1.936s,182144KiB,remaining[].
- r3: 2FAIL,1deselected. Trying both admission modes correctly conflicts.
  PG6631 exit1,1.722s,188160KiB,remaining[].
- r4: 2PASS,1deselected,pytest1.11s. Actual C01-10 public seed/replay/reopen/source
  rejection plus atomic graph source/Procedure/relation and public graph/reopen.
  PG6675 exit0,1.722s,173856KiB,remaining[],minDisk5708MiB.

No prior green corpus/SDK matrices rerun. H078/M618 from main existing installed
target; existing primary-m0615 Python supplies generic dependencies, no SDK overlay.
No Provider or quality score. Claim/current suppression and full runtime not tested.

Commands: default shared `scripts/run_resource_bounded.py --rss-mib 2048 --seconds
180 -- <existing Python> -I -B .local-test-evidence/2026-09-06/corpus-public-seed/run.py
<batch>`; r2/r3/r4 add `-k 'test_c01_10 or test_public_atomic'`.

Raw ignored indexes:
- `.local-test-evidence/2026-09-06/corpus-public-seed/r1/command.log` SHA256 `2b0ef8b0ba089915895f3c88230e3c634b94a8807cfd010a43187f890d1d3806`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r1/resource.json` SHA256 `bdb5f877755ea7dfdce307d4919eb3f5edf66ae7cc582e2c331f1cee5aadfa4c`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r2/command.log` SHA256 `6c66d6420d860723c781a11e8d849edac011bf2ba8570bd165e9e1eab1a3d383`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r2/resource.json` SHA256 `31ae2121cf50ba025084a95b40927134ee98ecd80c3d2807c888121ca27ceb59`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r3/command.log` SHA256 `4f81f6674493c10b4aa7c246e6666b7e03ed9aa77e87441428e7f2bfa166d961`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r3/resource.json` SHA256 `9eade677bb3df0cec28b00cb9feb4994a46a3f0ddd7534d5b36c4114ca54b59c`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r4/command.log` SHA256 `535f069799696e8b687fe860bd4b42fad1cbbcebdf10c61c14a46279a560c491`
- `.local-test-evidence/2026-09-06/corpus-public-seed/r4/resource.json` SHA256 `6895fb9499d1541bc9a2bf9abb6dcd6a27c77f7a5669441975a3c2997fdc12c6`
