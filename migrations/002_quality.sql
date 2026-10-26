CREATE TABLE validation_run (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, report TEXT NOT NULL
);
CREATE INDEX job_state ON job(state, priority);
