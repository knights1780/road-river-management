-- Migration 0015: administrative cases and append-only response history.
-- Requires existing users, departments, managed_facilities tables.
CREATE TABLE administrative_cases (
    case_id INTEGER PRIMARY KEY,
    case_uuid TEXT NOT NULL UNIQUE,
    case_number TEXT UNIQUE,
    case_type_code TEXT NOT NULL CHECK (case_type_code IN ('CONSULTATION','COMPLAINT_REQUEST','CIVIL_WORKS_REQUEST')),
    case_subtype_code TEXT,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT NOT NULL DEFAULT '',
    received_on TEXT NOT NULL CHECK (received_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    due_on TEXT CHECK (due_on IS NULL OR due_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    importance_code TEXT NOT NULL DEFAULT 'NORMAL' CHECK (importance_code IN ('CRITICAL','HIGH','NORMAL','LOW')),
    status_code TEXT NOT NULL DEFAULT 'OPEN' CHECK (status_code IN ('OPEN','IN_PROGRESS','ON_HOLD','CLOSED')),
    responsible_department_id INTEGER REFERENCES departments(department_id) ON DELETE RESTRICT,
    responsible_user_id INTEGER REFERENCES users(user_id) ON DELETE RESTRICT,
    created_by_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    followup_required INTEGER NOT NULL DEFAULT 0 CHECK (followup_required IN (0,1)),
    followup_on TEXT CHECK (followup_on IS NULL OR followup_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    followup_reason TEXT,
    closed_on TEXT CHECK (closed_on IS NULL OR closed_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((followup_required = 0 AND followup_on IS NULL AND followup_reason IS NULL)
        OR (followup_required = 1 AND followup_on IS NOT NULL AND length(trim(coalesce(followup_reason,''))) > 0)),
    CHECK ((status_code = 'CLOSED' AND closed_on IS NOT NULL)
        OR (status_code <> 'CLOSED' AND closed_on IS NULL))
);
CREATE INDEX idx_admin_cases_status_due ON administrative_cases(status_code, due_on);
CREATE INDEX idx_admin_cases_followup ON administrative_cases(followup_required, followup_on);
CREATE INDEX idx_admin_cases_department ON administrative_cases(responsible_department_id, status_code);
CREATE INDEX idx_admin_cases_user ON administrative_cases(responsible_user_id, status_code);

CREATE TABLE administrative_case_facilities (
    case_id INTEGER NOT NULL REFERENCES administrative_cases(case_id) ON DELETE RESTRICT,
    facility_id INTEGER NOT NULL REFERENCES managed_facilities(facility_id) ON DELETE RESTRICT,
    PRIMARY KEY (case_id, facility_id)
);
CREATE INDEX idx_admin_case_facilities_facility ON administrative_case_facilities(facility_id);

CREATE TABLE administrative_case_responses (
    response_id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES administrative_cases(case_id) ON DELETE RESTRICT,
    response_on TEXT NOT NULL CHECK (response_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    response_type_code TEXT NOT NULL CHECK (response_type_code IN ('CONTACT','SITE_VISIT','INVESTIGATION','REPAIR','NOTICE','OTHER')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    next_action TEXT,
    recorded_by_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_admin_responses_case ON administrative_case_responses(case_id, response_id);
CREATE TRIGGER trg_admin_responses_no_update BEFORE UPDATE ON administrative_case_responses
BEGIN SELECT RAISE(ABORT, 'case response history is immutable'); END;
CREATE TRIGGER trg_admin_responses_no_delete BEFORE DELETE ON administrative_case_responses
BEGIN SELECT RAISE(ABORT, 'case response history is immutable'); END;

CREATE TABLE administrative_case_events (
    event_id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES administrative_cases(case_id) ON DELETE RESTRICT,
    event_type_code TEXT NOT NULL CHECK (event_type_code IN ('CREATED','STATUS_CHANGED','FOLLOWUP_CHANGED')),
    previous_value TEXT,
    new_value TEXT,
    changed_by_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_admin_case_events_case ON administrative_case_events(case_id, event_id);
CREATE TRIGGER trg_admin_events_no_update BEFORE UPDATE ON administrative_case_events
BEGIN SELECT RAISE(ABORT, 'case event history is immutable'); END;
CREATE TRIGGER trg_admin_events_no_delete BEFORE DELETE ON administrative_case_events
BEGIN SELECT RAISE(ABORT, 'case event history is immutable'); END;
