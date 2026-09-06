CREATE TABLE prospective_timer_events (
    owner_key TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    phase TEXT NOT NULL CHECK(
        phase IN ('prepared','claimed','handed_off','applied','invalidated')
    ),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL CHECK(claim_epoch >= 0),
    lease_until REAL,
    body_json TEXT NOT NULL CHECK(json_valid(body_json)),
    body_hash TEXT NOT NULL CHECK(
        length(body_hash)=64 AND body_hash NOT GLOB '*[^0-9a-f]*'
    ),
    prior_hash TEXT NOT NULL CHECK(
        length(prior_hash)=64 AND prior_hash NOT GLOB '*[^0-9a-f]*'
    ),
    record_hash TEXT NOT NULL CHECK(
        length(record_hash)=64 AND record_hash NOT GLOB '*[^0-9a-f]*'
    ),
    PRIMARY KEY(owner_key, signal_id, sequence),
    CHECK(
        (phase='prepared' AND claim_owner IS NULL
         AND claim_epoch=0 AND lease_until IS NULL)
        OR
        (phase IN ('claimed','handed_off')
         AND claim_owner IS NOT NULL AND length(claim_owner)>0
         AND claim_epoch>0 AND lease_until IS NOT NULL AND lease_until>=0)
        OR
        (phase IN ('applied','invalidated')
         AND claim_owner IS NOT NULL AND length(claim_owner)>0
         AND claim_epoch>0 AND lease_until IS NULL)
    )
);

CREATE UNIQUE INDEX prospective_timer_prepared
ON prospective_timer_events(owner_key, signal_id)
WHERE phase='prepared';

CREATE UNIQUE INDEX prospective_timer_authority
ON prospective_timer_events(
    owner_key, json_extract(body_json,'$.authority.authority_id')
) WHERE phase='prepared';

CREATE UNIQUE INDEX prospective_timer_handoff_epoch
ON prospective_timer_events(owner_key, signal_id, claim_epoch)
WHERE phase='handed_off';

CREATE INDEX prospective_timer_recovery
ON prospective_timer_events(owner_key, phase, lease_until, signal_id);

CREATE TRIGGER prospective_timer_events_no_update
BEFORE UPDATE ON prospective_timer_events
BEGIN
    SELECT RAISE(ABORT, 'prospective_timer_events_append_only');
END;

CREATE TRIGGER prospective_timer_events_no_delete
BEFORE DELETE ON prospective_timer_events
BEGIN
    SELECT RAISE(ABORT, 'prospective_timer_events_append_only');
END;
