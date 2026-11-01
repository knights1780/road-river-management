CREATE TABLE IF NOT EXISTS schema_versions (
    schema_version_id INTEGER PRIMARY KEY,
    version_number INTEGER NOT NULL UNIQUE,
    migration_name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    application_version TEXT,
    result_status TEXT NOT NULL DEFAULT 'SUCCESS',
    remarks TEXT
);
