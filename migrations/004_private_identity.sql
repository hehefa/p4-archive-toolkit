CREATE TABLE identity_package (
 id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL UNIQUE, body TEXT NOT NULL,
 digest TEXT NOT NULL, snapshot_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE identity_record (
 id TEXT PRIMARY KEY, upstream_id TEXT NOT NULL, kind TEXT NOT NULL,
 person_entity_id TEXT REFERENCES entity(id), package_id TEXT NOT NULL REFERENCES identity_package(id),
 body TEXT NOT NULL, digest TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(kind,upstream_id)
);
CREATE TABLE identity_revision (
 record_id TEXT NOT NULL REFERENCES identity_record(id), version INTEGER NOT NULL,
 package_id TEXT NOT NULL REFERENCES identity_package(id), body TEXT NOT NULL,
 digest TEXT NOT NULL, changed_at TEXT NOT NULL, PRIMARY KEY(record_id,version)
);
CREATE INDEX identity_person ON identity_record(person_entity_id,kind);
