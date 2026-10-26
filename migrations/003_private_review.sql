CREATE TABLE review_package (
 id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL UNIQUE, body TEXT NOT NULL,
 digest TEXT NOT NULL, snapshot_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE review_record (
 id TEXT PRIMARY KEY, upstream_id TEXT NOT NULL, package_id TEXT NOT NULL REFERENCES review_package(id),
 body TEXT NOT NULL, digest TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE review_revision (
 record_id TEXT NOT NULL, version INTEGER NOT NULL, package_id TEXT NOT NULL REFERENCES review_package(id),
 body TEXT NOT NULL, digest TEXT NOT NULL, changed_at TEXT NOT NULL,
 PRIMARY KEY(record_id,version)
);
CREATE TABLE review_entity_link (
 review_id TEXT NOT NULL REFERENCES review_record(id), entity_id TEXT NOT NULL REFERENCES entity(id),
 kind TEXT NOT NULL, basis TEXT NOT NULL, PRIMARY KEY(review_id,entity_id,kind)
);
CREATE INDEX review_link_entity ON review_entity_link(entity_id);
