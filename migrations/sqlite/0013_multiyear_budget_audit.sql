
-- ============================================================
-- Migration 0013: Multiyear budget audit history
--
-- Requires migrations 0001 through 0012.
-- Original multiyear tables remain unchanged.
-- ============================================================

CREATE TABLE budget_multiyear_audit (
    audit_id INTEGER PRIMARY KEY,
    audit_uuid TEXT NOT NULL UNIQUE,

    record_type_code TEXT NOT NULL
        CHECK (
            record_type_code IN (
                'OBLIGATION',
                'CONTINUING'
            )
        ),

    record_id INTEGER NOT NULL
        CHECK (record_id > 0),

    action_code TEXT NOT NULL
        CHECK (
            action_code IN (
                'CREATED',
                'SCHEDULE_CHANGED',
                'AUTHORIZED',
                'REVISED'
            )
        ),

    previous_snapshot_json TEXT
        CHECK (
            previous_snapshot_json IS NULL
            OR json_valid(previous_snapshot_json)
        ),

    new_snapshot_json TEXT NOT NULL
        CHECK (json_valid(new_snapshot_json)),

    change_reason TEXT NOT NULL
        CHECK (length(trim(change_reason)) > 0),

    decision_reference TEXT,
    changed_by_user_id INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (changed_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_multiyear_audit_record
ON budget_multiyear_audit (
    record_type_code,
    record_id,
    audit_id
);

CREATE INDEX idx_multiyear_audit_action
ON budget_multiyear_audit (
    action_code,
    created_at
);

-- Audit history must be append-only.

CREATE TRIGGER trg_multiyear_audit_no_update
BEFORE UPDATE ON budget_multiyear_audit
BEGIN
    SELECT RAISE(
        ABORT,
        'multiyear audit history cannot be updated'
    );
END;

CREATE TRIGGER trg_multiyear_audit_no_delete
BEFORE DELETE ON budget_multiyear_audit
BEGIN
    SELECT RAISE(
        ABORT,
        'multiyear audit history cannot be deleted'
    );
END;
