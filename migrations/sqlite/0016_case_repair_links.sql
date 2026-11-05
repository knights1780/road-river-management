-- Migration 0016: many-to-many association between administrative cases and repairs.
-- Do not duplicate repair or case records. Unlinking preserves audit history.
CREATE TABLE case_repair_links (
    case_repair_link_id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES administrative_cases(case_id) ON DELETE RESTRICT,
    repair_id INTEGER NOT NULL REFERENCES facility_repairs(repair_id) ON DELETE RESTRICT,
    linked_by_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    link_note TEXT,
    unlinked_at TEXT,
    unlinked_by_user_id INTEGER REFERENCES users(user_id) ON DELETE RESTRICT,
    unlink_reason TEXT,
    CHECK (
        (unlinked_at IS NULL AND unlinked_by_user_id IS NULL AND unlink_reason IS NULL)
        OR
        (unlinked_at IS NOT NULL AND unlinked_by_user_id IS NOT NULL
         AND unlink_reason IS NOT NULL AND length(trim(unlink_reason)) > 0)
    )
);
CREATE UNIQUE INDEX idx_case_repair_active_unique
ON case_repair_links(case_id, repair_id) WHERE unlinked_at IS NULL;
CREATE INDEX idx_case_repair_case
ON case_repair_links(case_id, unlinked_at);
CREATE INDEX idx_case_repair_repair
ON case_repair_links(repair_id, unlinked_at);

CREATE TABLE case_repair_link_events (
    event_id INTEGER PRIMARY KEY,
    case_repair_link_id INTEGER NOT NULL
        REFERENCES case_repair_links(case_repair_link_id) ON DELETE RESTRICT,
    event_type_code TEXT NOT NULL CHECK (event_type_code IN ('LINKED', 'UNLINKED')),
    actor_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    event_note TEXT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_case_repair_events_link
ON case_repair_link_events(case_repair_link_id, event_id);
CREATE TRIGGER trg_case_repair_events_no_update
BEFORE UPDATE ON case_repair_link_events
BEGIN SELECT RAISE(ABORT, 'case repair link events are immutable'); END;
CREATE TRIGGER trg_case_repair_events_no_delete
BEFORE DELETE ON case_repair_link_events
BEGIN SELECT RAISE(ABORT, 'case repair link events are immutable'); END;
CREATE TRIGGER trg_case_repair_links_no_delete
BEFORE DELETE ON case_repair_links
BEGIN SELECT RAISE(ABORT, 'case repair links must be unlinked, not deleted'); END;
