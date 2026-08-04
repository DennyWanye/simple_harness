-- State DB v27: persist privacy-safe DEV provider-fault correlation evidence.
-- The migration runner owns the transaction, marker, and user_version.

ALTER TABLE provider_workload_audit
    ADD COLUMN injection_correlation_hash TEXT;

CREATE INDEX idx_provider_workload_audit_injection
    ON provider_workload_audit(injection_ref, injection_correlation_hash);
