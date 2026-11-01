-- Migration 0014: fiscal budget-to-contract allocations and recorded payments.
-- Requires 0010, 0011, 0012, 0013. Existing migrations remain immutable.
-- Contract amount remains exclusively in construction_works.

CREATE TABLE budget_contract_allocations (
    contract_allocation_id INTEGER PRIMARY KEY,
    allocation_uuid TEXT NOT NULL UNIQUE,
    construction_work_id INTEGER NOT NULL,
    budget_request_id INTEGER NOT NULL,
    allocated_contract_amount_yen INTEGER NOT NULL
        CHECK (allocated_contract_amount_yen > 0),
    allocation_reason TEXT NOT NULL CHECK (length(trim(allocation_reason)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id) ON DELETE RESTRICT,
    FOREIGN KEY (budget_request_id)
        REFERENCES budget_requests(budget_request_id) ON DELETE RESTRICT,
    UNIQUE (construction_work_id, budget_request_id)
);
CREATE INDEX idx_contract_allocations_budget ON budget_contract_allocations(budget_request_id);

-- A payment is recorded exactly once per construction work/reference.
CREATE TABLE construction_payments (
    payment_id INTEGER PRIMARY KEY,
    payment_uuid TEXT NOT NULL UNIQUE,
    construction_work_id INTEGER NOT NULL,
    payment_reference TEXT NOT NULL CHECK (length(trim(payment_reference)) > 0),
    payment_fiscal_year INTEGER NOT NULL CHECK (payment_fiscal_year BETWEEN 1900 AND 2200),
    paid_on TEXT NOT NULL,
    paid_amount_yen INTEGER NOT NULL CHECK (paid_amount_yen > 0),
    payment_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id) ON DELETE RESTRICT,
    UNIQUE (construction_work_id, payment_reference),
    UNIQUE (payment_id, construction_work_id)
);
CREATE INDEX idx_construction_payments_year ON construction_payments(payment_fiscal_year);
CREATE INDEX idx_construction_payments_work ON construction_payments(construction_work_id);

-- A payment can be split across budget requests, but cannot be duplicated.
CREATE TABLE budget_payment_allocations (
    payment_allocation_id INTEGER PRIMARY KEY,
    payment_id INTEGER NOT NULL,
    construction_work_id INTEGER NOT NULL,
    budget_request_id INTEGER NOT NULL,
    allocated_payment_amount_yen INTEGER NOT NULL
        CHECK (allocated_payment_amount_yen > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payment_id, construction_work_id)
        REFERENCES construction_payments(payment_id, construction_work_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (construction_work_id, budget_request_id)
        REFERENCES budget_contract_allocations(construction_work_id, budget_request_id)
        ON DELETE RESTRICT,
    UNIQUE (payment_id, budget_request_id)
);
CREATE INDEX idx_payment_allocations_budget ON budget_payment_allocations(budget_request_id);

-- Guard totals even for direct SQL callers.
CREATE TRIGGER trg_payment_allocation_insert_limit
BEFORE INSERT ON budget_payment_allocations
BEGIN
    SELECT RAISE(ABORT, 'payment allocations exceed payment')
    WHERE NEW.allocated_payment_amount_yen + COALESCE((
        SELECT SUM(allocated_payment_amount_yen)
        FROM budget_payment_allocations
        WHERE payment_id = NEW.payment_id
    ), 0) > (
        SELECT paid_amount_yen FROM construction_payments
        WHERE payment_id = NEW.payment_id
    );
    SELECT RAISE(ABORT, 'payment allocations exceed contract allocation')
    WHERE NEW.allocated_payment_amount_yen + COALESCE((
        SELECT SUM(allocated_payment_amount_yen)
        FROM budget_payment_allocations
        WHERE construction_work_id = NEW.construction_work_id
          AND budget_request_id = NEW.budget_request_id
    ), 0) > (
        SELECT allocated_contract_amount_yen FROM budget_contract_allocations
        WHERE construction_work_id = NEW.construction_work_id
          AND budget_request_id = NEW.budget_request_id
    );
END;

CREATE TRIGGER trg_payment_allocation_update_limit
BEFORE UPDATE ON budget_payment_allocations
BEGIN
    SELECT RAISE(ABORT, 'payment allocations exceed payment')
    WHERE NEW.allocated_payment_amount_yen + COALESCE((
        SELECT SUM(allocated_payment_amount_yen)
        FROM budget_payment_allocations
        WHERE payment_id = NEW.payment_id
          AND payment_allocation_id <> OLD.payment_allocation_id
    ), 0) > (
        SELECT paid_amount_yen FROM construction_payments
        WHERE payment_id = NEW.payment_id
    );
    SELECT RAISE(ABORT, 'payment allocations exceed contract allocation')
    WHERE NEW.allocated_payment_amount_yen + COALESCE((
        SELECT SUM(allocated_payment_amount_yen)
        FROM budget_payment_allocations
        WHERE construction_work_id = NEW.construction_work_id
          AND budget_request_id = NEW.budget_request_id
          AND payment_allocation_id <> OLD.payment_allocation_id
    ), 0) > (
        SELECT allocated_contract_amount_yen FROM budget_contract_allocations
        WHERE construction_work_id = NEW.construction_work_id
          AND budget_request_id = NEW.budget_request_id
    );
END;

CREATE TRIGGER trg_contract_allocation_update_limit
BEFORE UPDATE OF allocated_contract_amount_yen ON budget_contract_allocations
BEGIN
    SELECT RAISE(ABORT, 'contract allocation is below allocated payments')
    WHERE NEW.allocated_contract_amount_yen < COALESCE((
        SELECT SUM(allocated_payment_amount_yen)
        FROM budget_payment_allocations
        WHERE construction_work_id = OLD.construction_work_id
          AND budget_request_id = OLD.budget_request_id
    ), 0);
END;

-- Reject an allocation to a work not linked to the request's project.
CREATE TRIGGER trg_contract_allocation_project_guard
BEFORE INSERT ON budget_contract_allocations
BEGIN
    SELECT RAISE(ABORT, 'budget project is not linked to work')
    WHERE NOT EXISTS (
        SELECT 1 FROM budget_requests AS r
        JOIN project_construction_links AS l ON l.project_id = r.project_id
        WHERE r.budget_request_id = NEW.budget_request_id
          AND l.construction_work_id = NEW.construction_work_id
    );
    SELECT RAISE(ABORT, 'contract allocations exceed contract amount')
    WHERE NEW.allocated_contract_amount_yen + COALESCE((
        SELECT SUM(allocated_contract_amount_yen)
        FROM budget_contract_allocations
        WHERE construction_work_id = NEW.construction_work_id
    ), 0) > (
        SELECT COALESCE(final_amount_yen, current_contract_amount_yen)
        FROM construction_works
        WHERE construction_work_id = NEW.construction_work_id
    );
END;

CREATE TRIGGER trg_construction_payment_total_guard
BEFORE INSERT ON construction_payments
BEGIN
    SELECT RAISE(ABORT, 'payments exceed recorded contract amount')
    WHERE NEW.paid_amount_yen + COALESCE((
        SELECT SUM(paid_amount_yen) FROM construction_payments
        WHERE construction_work_id = NEW.construction_work_id
    ), 0) > (
        SELECT COALESCE(final_amount_yen, current_contract_amount_yen)
        FROM construction_works
        WHERE construction_work_id = NEW.construction_work_id
    );
END;
