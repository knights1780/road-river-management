
-- ============================================================
-- Migration 0012: Multiyear budgets
--
-- Requires 0011_budget_requests.sql
--
-- Fiscal years are Gregorian start years.
-- Amounts are integer Japanese yen.
--
-- Do not edit previously applied migration files.
-- ============================================================


-- ============================================================
-- 1. Changes to recorded appropriations
-- ============================================================

CREATE TABLE budget_appropriation_adjustments (
    adjustment_id INTEGER PRIMARY KEY,
    adjustment_uuid TEXT NOT NULL UNIQUE,

    budget_appropriation_id INTEGER NOT NULL,

    adjustment_number INTEGER NOT NULL
        CHECK (adjustment_number >= 1),

    adjustment_type_code TEXT NOT NULL
        CHECK (
            adjustment_type_code IN (
                'INCREASE',
                'DECREASE',
                'CORRECTION'
            )
        ),

    amount_delta_yen INTEGER NOT NULL
        CHECK (
            (
                adjustment_type_code = 'INCREASE'
                AND amount_delta_yen > 0
            )
            OR (
                adjustment_type_code = 'DECREASE'
                AND amount_delta_yen < 0
            )
            OR (
                adjustment_type_code = 'CORRECTION'
                AND amount_delta_yen <> 0
            )
        ),

    approved_on TEXT NOT NULL,
    decision_reference TEXT,

    adjustment_reason TEXT NOT NULL
        CHECK (length(trim(adjustment_reason)) > 0),

    recorded_by_user_id INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (budget_appropriation_id)
        REFERENCES budget_appropriations(
            budget_appropriation_id
        )
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (
        budget_appropriation_id,
        adjustment_number
    )
);

CREATE INDEX idx_budget_adjustments_appropriation
ON budget_appropriation_adjustments (
    budget_appropriation_id,
    adjustment_number
);


-- ============================================================
-- 2. Budget carryovers
--
-- MEIKYO   : 繰越明許費
-- ACCIDENT : 事故繰越し
--
-- These are different from debt obligations.
-- ============================================================

CREATE TABLE budget_carryovers (
    carryover_id INTEGER PRIMARY KEY,
    carryover_uuid TEXT NOT NULL UNIQUE,

    budget_request_id INTEGER NOT NULL,

    carryover_type_code TEXT NOT NULL
        CHECK (
            carryover_type_code IN (
                'MEIKYO',
                'ACCIDENT'
            )
        ),

    source_fiscal_year INTEGER NOT NULL
        CHECK (
            source_fiscal_year BETWEEN 1900 AND 2199
        ),

    destination_fiscal_year INTEGER NOT NULL,

    carryover_status_code TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (
            carryover_status_code IN (
                'DRAFT',
                'AUTHORIZED',
                'FINALIZED',
                'CANCELLED'
            )
        ),

    authorized_limit_yen INTEGER
        CHECK (
            authorized_limit_yen IS NULL
            OR authorized_limit_yen >= 0
        ),

    actual_carryover_amount_yen INTEGER
        CHECK (
            actual_carryover_amount_yen IS NULL
            OR actual_carryover_amount_yen >= 0
        ),

    carryover_reason TEXT NOT NULL
        CHECK (
            length(trim(carryover_reason)) > 0
        ),

    unavoidable_accident_description TEXT,
    obligation_incurred_on TEXT,

    authorization_date TEXT,
    finalization_date TEXT,

    decision_reference TEXT,
    notes TEXT,

    recorded_by_user_id INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (budget_request_id)
        REFERENCES budget_requests(budget_request_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        destination_fiscal_year =
            source_fiscal_year + 1
    ),

    CHECK (
        actual_carryover_amount_yen IS NULL
        OR authorized_limit_yen IS NULL
        OR actual_carryover_amount_yen <=
            authorized_limit_yen
    ),

    CHECK (
        carryover_status_code <> 'FINALIZED'
        OR actual_carryover_amount_yen IS NOT NULL
    ),

    CHECK (
        carryover_type_code <> 'ACCIDENT'
        OR (
            unavoidable_accident_description IS NOT NULL
            AND length(
                trim(unavoidable_accident_description)
            ) > 0
            AND obligation_incurred_on IS NOT NULL
        )
    )
);

CREATE INDEX idx_budget_carryovers_request
ON budget_carryovers (
    budget_request_id,
    source_fiscal_year
);

CREATE INDEX idx_budget_carryovers_years
ON budget_carryovers (
    source_fiscal_year,
    destination_fiscal_year,
    carryover_type_code
);


-- Source fiscal year must match the budget request.
CREATE TRIGGER trg_carryover_source_year_insert
BEFORE INSERT ON budget_carryovers
BEGIN
    SELECT RAISE(
        ABORT,
        'carryover source fiscal year mismatch'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_requests AS r
        WHERE r.budget_request_id =
            NEW.budget_request_id
          AND r.fiscal_year =
            NEW.source_fiscal_year
    );
END;

CREATE TRIGGER trg_carryover_source_year_update
BEFORE UPDATE OF budget_request_id, source_fiscal_year
ON budget_carryovers
BEGIN
    SELECT RAISE(
        ABORT,
        'carryover source fiscal year mismatch'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_requests AS r
        WHERE r.budget_request_id =
            NEW.budget_request_id
          AND r.fiscal_year =
            NEW.source_fiscal_year
    );
END;


-- ============================================================
-- 3. Debt obligation authorizations
--    債務負担行為
-- ============================================================

CREATE TABLE budget_obligations (
    obligation_id INTEGER PRIMARY KEY,
    obligation_uuid TEXT NOT NULL UNIQUE,

    project_id INTEGER NOT NULL,

    obligation_title TEXT NOT NULL
        CHECK (
            length(trim(obligation_title)) > 0
        ),

    authorization_fiscal_year INTEGER NOT NULL
        CHECK (
            authorization_fiscal_year
                BETWEEN 1900 AND 2200
        ),

    start_fiscal_year INTEGER NOT NULL,
    end_fiscal_year INTEGER NOT NULL,

    limit_amount_yen INTEGER NOT NULL
        CHECK (limit_amount_yen >= 0),

    authorization_status_code TEXT NOT NULL
        DEFAULT 'DRAFT'
        CHECK (
            authorization_status_code IN (
                'DRAFT',
                'AUTHORIZED',
                'REVISED',
                'CANCELLED'
            )
        ),

    authorized_on TEXT,
    decision_reference TEXT,
    purpose TEXT,
    notes TEXT,

    recorded_by_user_id INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        start_fiscal_year BETWEEN 1900 AND 2200
    ),

    CHECK (
        end_fiscal_year BETWEEN 1900 AND 2200
    ),

    CHECK (
        start_fiscal_year <= end_fiscal_year
    )
);

CREATE INDEX idx_budget_obligations_project
ON budget_obligations(project_id);

CREATE INDEX idx_budget_obligations_period
ON budget_obligations(
    start_fiscal_year,
    end_fiscal_year
);


-- ============================================================
-- 4. Debt obligation yearly payment schedules
-- ============================================================

CREATE TABLE budget_obligation_years (
    obligation_year_id INTEGER PRIMARY KEY,

    obligation_id INTEGER NOT NULL,

    fiscal_year INTEGER NOT NULL
        CHECK (
            fiscal_year BETWEEN 1900 AND 2200
        ),

    planned_payment_amount_yen INTEGER NOT NULL
        CHECK (
            planned_payment_amount_yen >= 0
        ),

    notes TEXT,

    FOREIGN KEY (obligation_id)
        REFERENCES budget_obligations(obligation_id)
        ON DELETE RESTRICT,

    UNIQUE (obligation_id, fiscal_year)
);

CREATE INDEX idx_budget_obligation_years_fiscal
ON budget_obligation_years(fiscal_year);


CREATE TRIGGER trg_obligation_year_period_insert
BEFORE INSERT ON budget_obligation_years
BEGIN
    SELECT RAISE(
        ABORT,
        'obligation year outside authorized period'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_obligations AS o
        WHERE o.obligation_id = NEW.obligation_id
          AND NEW.fiscal_year
              BETWEEN o.start_fiscal_year
                  AND o.end_fiscal_year
    );
END;

CREATE TRIGGER trg_obligation_year_period_update
BEFORE UPDATE OF obligation_id, fiscal_year
ON budget_obligation_years
BEGIN
    SELECT RAISE(
        ABORT,
        'obligation year outside authorized period'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_obligations AS o
        WHERE o.obligation_id = NEW.obligation_id
          AND NEW.fiscal_year
              BETWEEN o.start_fiscal_year
                  AND o.end_fiscal_year
    );
END;


-- ============================================================
-- 5. Continuing expenditures
--    継続費
-- ============================================================

CREATE TABLE budget_continuing_expenditures (
    continuing_id INTEGER PRIMARY KEY,
    continuing_uuid TEXT NOT NULL UNIQUE,

    project_id INTEGER NOT NULL,

    continuing_title TEXT NOT NULL
        CHECK (
            length(trim(continuing_title)) > 0
        ),

    start_fiscal_year INTEGER NOT NULL
        CHECK (
            start_fiscal_year BETWEEN 1900 AND 2200
        ),

    end_fiscal_year INTEGER NOT NULL
        CHECK (
            end_fiscal_year BETWEEN 1900 AND 2200
        ),

    total_amount_yen INTEGER NOT NULL
        CHECK (total_amount_yen >= 0),

    status_code TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (
            status_code IN (
                'DRAFT',
                'AUTHORIZED',
                'REVISED',
                'CLOSED',
                'CANCELLED'
            )
        ),

    authorized_on TEXT,
    decision_reference TEXT,
    purpose TEXT,
    notes TEXT,

    recorded_by_user_id INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        start_fiscal_year <= end_fiscal_year
    )
);

CREATE INDEX idx_budget_continuing_project
ON budget_continuing_expenditures(project_id);

CREATE INDEX idx_budget_continuing_period
ON budget_continuing_expenditures(
    start_fiscal_year,
    end_fiscal_year
);


-- ============================================================
-- 6. Continuing expenditure annual allocations
--    継続費の年割額
-- ============================================================

CREATE TABLE budget_continuing_years (
    continuing_year_id INTEGER PRIMARY KEY,

    continuing_id INTEGER NOT NULL,

    fiscal_year INTEGER NOT NULL
        CHECK (
            fiscal_year BETWEEN 1900 AND 2200
        ),

    annual_amount_yen INTEGER NOT NULL
        CHECK (
            annual_amount_yen >= 0
        ),

    notes TEXT,

    FOREIGN KEY (continuing_id)
        REFERENCES budget_continuing_expenditures(
            continuing_id
        )
        ON DELETE RESTRICT,

    UNIQUE (continuing_id, fiscal_year)
);

CREATE INDEX idx_budget_continuing_years_fiscal
ON budget_continuing_years(fiscal_year);


CREATE TRIGGER trg_continuing_year_period_insert
BEFORE INSERT ON budget_continuing_years
BEGIN
    SELECT RAISE(
        ABORT,
        'continuing year outside authorized period'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_continuing_expenditures AS c
        WHERE c.continuing_id = NEW.continuing_id
          AND NEW.fiscal_year
              BETWEEN c.start_fiscal_year
                  AND c.end_fiscal_year
    );
END;

CREATE TRIGGER trg_continuing_year_period_update
BEFORE UPDATE OF continuing_id, fiscal_year
ON budget_continuing_years
BEGIN
    SELECT RAISE(
        ABORT,
        'continuing year outside authorized period'
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM budget_continuing_expenditures AS c
        WHERE c.continuing_id = NEW.continuing_id
          AND NEW.fiscal_year
              BETWEEN c.start_fiscal_year
                  AND c.end_fiscal_year
    );
END;
