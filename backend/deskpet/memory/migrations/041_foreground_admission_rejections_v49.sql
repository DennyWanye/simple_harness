-- Host queue disposition before any Host/SDK Run exists. Original turn stays immutable.
CREATE TABLE foreground_admission_rejections (
    turn_id TEXT PRIMARY KEY REFERENCES foreground_turns(turn_id),
    subject TEXT NOT NULL,
    rejection_hash TEXT NOT NULL CHECK(length(rejection_hash)=64),
    rejection_json TEXT NOT NULL
);
CREATE TRIGGER foreground_admission_rejections_no_update BEFORE UPDATE ON foreground_admission_rejections
BEGIN SELECT RAISE(ABORT,'foreground_admission_rejections_append_only'); END;
CREATE TRIGGER foreground_admission_rejections_no_delete BEFORE DELETE ON foreground_admission_rejections
BEGIN SELECT RAISE(ABORT,'foreground_admission_rejections_append_only'); END;
