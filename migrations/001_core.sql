CREATE TABLE entity (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 digest TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL
);
CREATE TABLE revision (
 entity_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL,
 changed_at TEXT NOT NULL, reason TEXT NOT NULL, batch_id TEXT,
 PRIMARY KEY(entity_id,version)
);
CREATE TABLE relation (
 id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES entity(id), predicate TEXT NOT NULL,
 object TEXT NOT NULL REFERENCES entity(id), body TEXT NOT NULL, digest TEXT NOT NULL,
 version INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE relation_revision (
 relation_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL,
 changed_at TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(relation_id,version)
);
CREATE TABLE import_batch (
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, sha256 TEXT NOT NULL, source TEXT NOT NULL,
 snapshot_path TEXT NOT NULL, imported_at TEXT NOT NULL, report TEXT NOT NULL,
 UNIQUE(namespace,sha256)
);
CREATE TABLE alias (
 namespace TEXT NOT NULL, source_id TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES entity(id),
 PRIMARY KEY(namespace,source_id)
);
CREATE TABLE grant_record (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entity(id), digest TEXT NOT NULL,
 destination TEXT NOT NULL, fields TEXT NOT NULL, approved_by TEXT NOT NULL,
 evidence TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE job (
 id TEXT PRIMARY KEY, entity_id TEXT REFERENCES entity(id), kind TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','running','blocked','done','cancelled')),
 priority INTEGER NOT NULL DEFAULT 5, detail TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL,
 detail TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE INDEX entity_kind ON entity(kind);
CREATE INDEX relation_subject ON relation(subject);
CREATE INDEX relation_object ON relation(object);
