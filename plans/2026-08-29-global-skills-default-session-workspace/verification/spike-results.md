# Required spike results

## SPIKE-NATIVE-DOCUMENTS-BOOTSTRAP

Command: `swift -e 'import Foundation; let u = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true); print(u.isFileURL); print(u.standardizedFileURL.path)'`

Observed output: `true` and `/Users/denny/Documents`. Code evidence: `process_manager.rs::spawn_once` already writes a one-shot trusted JSON line to child stdin on every spawn/supervisor respawn. Decision: extend that edge to host-bootstrap-v2; packaged round-trip remains an implementation testcase.

## SPIKE-GLOBAL-OWNER-AUTHORITY

Command: `PYTHONPATH=backend backend/.venv/bin/python` probe using `load_or_create_local_identity`, `os.lstat`, uid/mode/regular-file/symlink checks, and domain-separated SHA-256.

Observed output: `stable True`, `owner_mode_symlink True`, `global_owner_key_len 64`, `symlink_detectable True`. Decision: identity namespace hash is the stable seed; Task B1 must add the checked authority and negative copied/shared/corrupt fixtures.
