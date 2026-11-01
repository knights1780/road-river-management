
-- ============================================================
-- Migration: 0011_budget_requests.sql
--
-- Fiscal-year budget requests, revisions, assessments,
-- appropriations and workflow events.
--
-- Requires migrations 0001 through 0010.
-- ============================================================


-- ============================================================
-- 1. Fiscal-year budget requests
-- ============================================================

CREATE TABLE budget_requests (
    budget_request_id INTEGER PRIMARY KEY,
    request_uuid TEXT NOT NULL UNIQUE,
    request_code TEXT UNIQUE,

    project_id INTEGER NOT NULL,
    stage_id INTEGER,

    fiscal_year INTEGER NOT NULL
        CHECK (fiscal_year BETWEEN 1900 AND 2200),

    request_title TEXT NOT NULL
        CHECK (length(trim(request_title)) > 0),

    request_category_code TEXT NOT NULL DEFAULT 'OTHER'
        CHECK (
            request_category_code IN (
                'SURVEY',
                'DESIGN',
                'CONSTRUCTION',
                'LAND_ACQUISITION',
                'MAINTENANCE',
                'OTHER'
            )
        ),

    budget_account_code TEXT,
    budget_item_name TEXT,

    original_requested_amount_yen INTEGER NOT NULL
        CHECK (original_requested_amount_yen >= 0),

    current_requested_amount_yen INTEGER NOT NULL
        CHECK (current_requested_amount_yen >= 0),

    request_status_code TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (
            request_status_code IN (
                'DRAFT',
                'SUBMITTED',
                'UNDER_REVIEW',
                'ASSESSED',
                'ON_HOLD',
                'WITHDRAWN',
                'CLOSED'
            )
        ),

    previous_request_id INTEGER,

    requested_by_department_id INTEGER,
    requested_by_user_id INTEGER,

    submitted_on TEXT,
    request_purpose TEXT,
    urgency_reason TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id, stage_id)
        REFERENCES project_stages(project_id, stage_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (previous_request_id)
        REFERENCES budget_requests(budget_request_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (requested_by_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (requested_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        previous_request_id IS NULL
        OR previous_request_id <> budget_request_id
    )
);

CREATE INDEX idx_budget_requests_project_year
ON budget_requests(project_id, fiscal_year);

CREATE INDEX idx_budget_requests_fiscal_status
ON budget_requests(fiscal_year, request_status_code);

CREATE INDEX idx_budget_requests_previous
ON budget_requests(previous_request_id);

CREATE INDEX idx_budget_requests_stage
ON budget_requests(stage_id);


-- Prevent cross-project re-request chains.

CREATE TRIGGER trg_budget_previous_request_insert
BEFORE INSERT ON budget_requests
WHEN NEW.previous_request_id IS NOT NULL
BEGIN
    SELECT RAISE(
        ABORT,
        'previous request belongs to another project'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_requests AS previous
        WHERE previous.budget_request_id = NEW.previous_request_id
          AND previous.project_id = NEW.project_id
          AND previous.fiscal_year <= NEW.fiscal_year
    );
END;

CREATE TRIGGER trg_budget_previous_request_update
BEFORE UPDATE OF previous_request_id, project_id, fiscal_year
ON budget_requests
WHEN NEW.previous_request_id IS NOT NULL
BEGIN
    SELECT RAISE(
        ABORT,
        'invalid previous budget request'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_requests AS previous
        WHERE previous.budget_request_id = NEW.previous_request_id
          AND previous.project_id = NEW.project_id
          AND previous.fiscal_year <= NEW.fiscal_year
    );
END;


-- ============================================================
-- 2. Budget request revision history
-- ============================================================

CREATE TABLE budget_request_revisions (
    budget_revision_id INTEGER PRIMARY KEY,
    budget_request_id INTEGER NOT NULL,

    revision_number INTEGER NOT NULL
        CHECK (revision_number >= 1),

    previous_requested_amount_yen INTEGER NOT NULL
        CHECK (previous_requested_amount_yen >= 0),

    new_requested_amount_yen INTEGER NOT NULL
        CHECK (new_requested_amount_yen >= 0),

    previous_request_purpose TEXT,
    new_request_purpose TEXT,

    revision_reason TEXT NOT NULL
        CHECK (length(trim(revision_reason)) > 0),

    revised_by_user_id INTEGER,
    revised_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (budget_request_id)
        REFERENCES budget_requests(budget_request_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (revised_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (budget_request_id, revision_number)
);

CREATE INDEX idx_budget_revisions_request
ON budget_request_revisions(
    budget_request_id,
    revision_number
);


-- ============================================================
-- 3. Budget assessments
-- ============================================================

CREATE TABLE budget_assessments (
    budget_assessment_id INTEGER PRIMARY KEY,
    budget_request_id INTEGER NOT NULL,

    assessment_round INTEGER NOT NULL
        CHECK (assessment_round >= 1),

    assessment_result_code TEXT NOT NULL
        CHECK (
            assessment_result_code IN (
                'FULL',
                'REDUCED',
                'ZERO',
                'HELD',
                'REJECTED'
            )
        ),

    requested_amount_at_assessment_yen INTEGER NOT NULL
        CHECK (requested_amount_at_assessment_yen >= 0),

    assessed_amount_yen INTEGER
        CHECK (
            assessed_amount_yen IS NULL
            OR assessed_amount_yen >= 0
        ),

    assessment_reason TEXT,
    conditions_text TEXT,

    assessed_by_department_id INTEGER,
    assessed_by_user_id INTEGER,
    assessed_on TEXT NOT NULL,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (budget_request_id)
        REFERENCES budget_requests(budget_request_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (assessed_by_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (assessed_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (
        budget_request_id,
        assessment_round
    ),

    CHECK (
        (
            assessment_result_code = 'FULL'
            AND assessed_amount_yen =
                requested_amount_at_assessment_yen
        )
        OR (
            assessment_result_code = 'REDUCED'
            AND assessed_amount_yen > 0
            AND assessed_amount_yen <
                requested_amount_at_assessment_yen
        )
        OR (
            assessment_result_code = 'ZERO'
            AND assessed_amount_yen = 0
        )
        OR (
            assessment_result_code = 'HELD'
            AND assessed_amount_yen IS NULL
        )
        OR (
            assessment_result_code = 'REJECTED'
            AND (
                assessed_amount_yen IS NULL
                OR assessed_amount_yen = 0
            )
        )
    ),

    -- FULL, REDUCED and ZERO require an explicit assessed amount.
    -- SQLite CHECK constraints otherwise allow NULL expressions.

    CHECK (
        assessment_result_code NOT IN (
            'FULL',
            'REDUCED',
            'ZERO'
        )
        OR assessed_amount_yen IS NOT NULL
    )
);

CREATE INDEX idx_budget_assessments_request
ON budget_assessments(
    budget_request_id,
    assessment_round
);

CREATE INDEX idx_budget_assessments_result
ON budget_assessments(
    assessment_result_code,
    assessed_on
);


-- ============================================================
-- 4. Formally appropriated budgets
-- ============================================================

CREATE TABLE budget_appropriations (
    budget_appropriation_id INTEGER PRIMARY KEY,
    budget_request_id INTEGER NOT NULL,

    appropriation_type_code TEXT NOT NULL
        CHECK (
            appropriation_type_code IN (
                'ORIGINAL',
                'SUPPLEMENTAL'
            )
        ),

    appropriation_number INTEGER NOT NULL DEFAULT 1
        CHECK (appropriation_number >= 1),

    appropriated_amount_yen INTEGER NOT NULL
        CHECK (appropriated_amount_yen >= 0),

    approved_on TEXT NOT NULL,
    effective_on TEXT,

    decision_reference TEXT,
    decision_notes TEXT,

    recorded_by_user_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (budget_request_id)
        REFERENCES budget_requests(budget_request_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (
        budget_request_id,
        appropriation_type_code,
        appropriation_number
    )
);

CREATE INDEX idx_budget_appropriations_request
ON budget_appropriations(budget_request_id);


-- ============================================================
-- 5. Budget request workflow / administrative events
-- ============================================================

CREATE TABLE budget_request_events (
    budget_event_id INTEGER PRIMARY KEY,
    budget_request_id INTEGER NOT NULL,

    event_type_code TEXT NOT NULL
        CHECK (
            event_type_code IN (
                'CREATED',
                'SUBMITTED',
                'REVIEW_STARTED',
                'ASSESSMENT_RECORDED',
                'HELD',
                'RESUMED',
                'REVISED',
                'WITHDRAWN',
                'CLOSED',
                'COMMENT'
            )
        ),

    previous_status_code TEXT,
    new_status_code TEXT,

    event_description TEXT,
    acted_by_user_id INTEGER,

    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (budget_request_id)
        REFERENCES budget_requests(budget_request_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (acted_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_budget_events_request
ON budget_request_events(
    budget_request_id,
    occurred_at
);
