-- Host-selected current disclosure; configuration is not a Memory input permit.
CREATE TABLE human_memory_disclosure_configs (
    binding_ref TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    policy_generation INTEGER NOT NULL CHECK(policy_generation > 0),
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    binding_hash TEXT NOT NULL CHECK(length(binding_hash)=64),
    binding_json TEXT NOT NULL,
    UNIQUE(subject, policy_generation),
    UNIQUE(subject, request_key)
);
CREATE TABLE human_memory_disclosure_heads (
    subject TEXT PRIMARY KEY,
    binding_ref TEXT NOT NULL REFERENCES human_memory_disclosure_configs(binding_ref)
);
CREATE TRIGGER human_memory_disclosure_no_update BEFORE UPDATE ON human_memory_disclosure_configs
BEGIN SELECT RAISE(ABORT,'human_memory_disclosure_append_only'); END;
CREATE TRIGGER human_memory_disclosure_no_delete BEFORE DELETE ON human_memory_disclosure_configs
BEGIN SELECT RAISE(ABORT,'human_memory_disclosure_append_only'); END;
