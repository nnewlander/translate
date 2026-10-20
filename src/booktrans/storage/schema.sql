PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source_sha256 TEXT NOT NULL UNIQUE,
    canonical_source_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    first_seen_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inspection_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    inspected_at_utc TEXT NOT NULL,
    report_path TEXT NOT NULL,
    source_mutated INTEGER NOT NULL CHECK(source_mutated IN (0, 1))
);

CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY,
    inspection_run_id INTEGER NOT NULL REFERENCES inspection_runs(id),
    part_name TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    UNIQUE(inspection_run_id, part_name)
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    extracted_at_utc TEXT NOT NULL,
    document_ir_path TEXT NOT NULL,
    coverage_path TEXT NOT NULL,
    source_mutated INTEGER NOT NULL CHECK(source_mutated IN (0, 1)),
    coverage_complete INTEGER NOT NULL CHECK(coverage_complete IN (0, 1))
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    unit_uid TEXT NOT NULL,
    display_id TEXT NOT NULL,
    part_name TEXT NOT NULL,
    story TEXT NOT NULL,
    block_type TEXT NOT NULL,
    content_role TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    structure_fingerprint TEXT NOT NULL,
    UNIQUE(document_id, unit_uid)
);
