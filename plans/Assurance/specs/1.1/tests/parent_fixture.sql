-- TEST ONLY. Minimal key-contract fixture; never a production SDK initializer.
CREATE TABLE missions(mission_id TEXT PRIMARY KEY) STRICT;
CREATE TABLE tasks(task_id TEXT PRIMARY KEY,mission_id TEXT NOT NULL REFERENCES missions(mission_id)) STRICT;
CREATE TABLE commit_receipts(commit_id TEXT PRIMARY KEY) STRICT;
CREATE TABLE dispatch_intents(intent_id TEXT PRIMARY KEY,mission_id TEXT NOT NULL REFERENCES missions(mission_id),state TEXT NOT NULL) STRICT;
CREATE TABLE requirements_revisions(mission_id TEXT NOT NULL REFERENCES missions(mission_id),revision INTEGER NOT NULL,content_hash TEXT NOT NULL,PRIMARY KEY(mission_id,revision)) STRICT;
CREATE TABLE review_packages(package_id TEXT PRIMARY KEY,mission_id TEXT NOT NULL REFERENCES missions(mission_id)) STRICT;
CREATE TABLE review_records(record_id TEXT PRIMARY KEY,package_id TEXT NOT NULL REFERENCES review_packages(package_id),mission_id TEXT NOT NULL REFERENCES missions(mission_id)) STRICT;
CREATE TABLE goal_resolutions(resolution_id TEXT PRIMARY KEY,mission_id TEXT NOT NULL REFERENCES missions(mission_id)) STRICT;

CREATE TABLE events(event_id TEXT PRIMARY KEY,mission_id TEXT NOT NULL REFERENCES missions(mission_id),type TEXT NOT NULL,payload TEXT NOT NULL) STRICT;
