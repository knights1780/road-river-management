
-- ============================================================
-- Migration: 0010_construction_works.sql
-- Common construction works and facility/project relationships.
-- Existing migrations 0001-0009 remain unchanged.
-- ============================================================

-- 1. Construction status master

CREATE TABLE construction_statuses (
    status_code TEXT PRIMARY KEY,
    status_name TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_terminal INTEGER NOT NULL DEFAULT 0
        CHECK (is_terminal IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

INSERT INTO construction_statuses (
    status_code, status_name, display_order, is_terminal
)
VALUES
    ('PLANNED', '計画中', 10, 0),
    ('PREPARING', '発注準備中', 20, 0),
    ('PROCUREMENT', '入札・見積中', 30, 0),
    ('CONTRACTED', '契約済', 40, 0),
    ('IN_PROGRESS', '施工中', 50, 0),
    ('ON_HOLD', '施工停止', 60, 0),
    ('COMPLETED', '完成', 70, 0),
    ('INSPECTED', '検査済', 80, 0),
    ('SETTLED', '精算完了', 90, 1),
    ('CANCELLED', '中止', 100, 1);


-- 2. Shared construction work master

CREATE TABLE construction_works (
    construction_work_id INTEGER PRIMARY KEY,
    construction_uuid TEXT NOT NULL UNIQUE,
    construction_code TEXT UNIQUE,
    construction_name TEXT NOT NULL,

    construction_type_code TEXT,
    status_code TEXT NOT NULL DEFAULT 'PLANNED',

    official_progress_percent REAL NOT NULL DEFAULT 0
        CHECK (official_progress_percent BETWEEN 0 AND 100),

    contractor_organization_id INTEGER,
    responsible_department_id INTEGER,
    responsible_user_id INTEGER,

    contract_date TEXT,
    planned_start_on TEXT,
    planned_end_on TEXT,
    actual_start_on TEXT,
    actual_completed_on TEXT,
    inspection_completed_on TEXT,

    initial_contract_amount_yen INTEGER
        CHECK (
            initial_contract_amount_yen IS NULL
            OR initial_contract_amount_yen >= 0
        ),

    current_contract_amount_yen INTEGER
        CHECK (
            current_contract_amount_yen IS NULL
            OR current_contract_amount_yen >= 0
        ),

    final_amount_yen INTEGER
        CHECK (
            final_amount_yen IS NULL
            OR final_amount_yen >= 0
        ),

    construction_description TEXT,
    completion_notes TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (status_code)
        REFERENCES construction_statuses(status_code)
        ON DELETE RESTRICT,

    FOREIGN KEY (contractor_organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (length(trim(construction_name)) > 0),

    CHECK (
        planned_start_on IS NULL
        OR planned_end_on IS NULL
        OR planned_end_on >= planned_start_on
    ),

    CHECK (
        actual_start_on IS NULL
        OR actual_completed_on IS NULL
        OR actual_completed_on >= actual_start_on
    ),

    CHECK (
        actual_completed_on IS NULL
        OR inspection_completed_on IS NULL
        OR inspection_completed_on >= actual_completed_on
    )
);

CREATE INDEX idx_construction_works_status
ON construction_works(status_code);

CREATE INDEX idx_construction_works_department
ON construction_works(responsible_department_id);

CREATE INDEX idx_construction_works_schedule
ON construction_works(planned_end_on);

CREATE INDEX idx_construction_works_contractor
ON construction_works(contractor_organization_id);


-- 3. Construction works and managed facilities

CREATE TABLE construction_work_facilities (
    work_facility_id INTEGER PRIMARY KEY,
    construction_work_id INTEGER NOT NULL,
    facility_id INTEGER NOT NULL,

    target_description TEXT,

    construction_length_m REAL
        CHECK (
            construction_length_m IS NULL
            OR construction_length_m >= 0
        ),

    construction_area_sqm REAL
        CHECK (
            construction_area_sqm IS NULL
            OR construction_area_sqm >= 0
        ),

    allocated_cost_yen INTEGER
        CHECK (
            allocated_cost_yen IS NULL
            OR allocated_cost_yen >= 0
        ),

    remarks TEXT,

    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    UNIQUE (construction_work_id, facility_id)
);

CREATE INDEX idx_construction_work_facilities_facility
ON construction_work_facilities(facility_id);


-- 4. Construction works and facility components

CREATE TABLE construction_work_components (
    work_component_id INTEGER PRIMARY KEY,
    construction_work_id INTEGER NOT NULL,
    component_id INTEGER NOT NULL,

    target_description TEXT,
    remarks TEXT,

    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (component_id)
        REFERENCES facility_components(component_id)
        ON DELETE RESTRICT,

    UNIQUE (construction_work_id, component_id)
);

CREATE INDEX idx_construction_work_components_component
ON construction_work_components(component_id);

-- Components must belong to a facility linked to the work.

CREATE TRIGGER trg_work_component_insert
BEFORE INSERT ON construction_work_components
BEGIN
    SELECT RAISE(ABORT, 'component facility not linked')
    WHERE NOT EXISTS (
        SELECT 1
        FROM facility_components AS component
        JOIN construction_work_facilities AS wf
          ON wf.facility_id = component.facility_id
        WHERE component.component_id = NEW.component_id
          AND wf.construction_work_id = NEW.construction_work_id
    );
END;

CREATE TRIGGER trg_work_component_update
BEFORE UPDATE OF construction_work_id, component_id
ON construction_work_components
BEGIN
    SELECT RAISE(ABORT, 'component facility not linked')
    WHERE NOT EXISTS (
        SELECT 1
        FROM facility_components AS component
        JOIN construction_work_facilities AS wf
          ON wf.facility_id = component.facility_id
        WHERE component.component_id = NEW.component_id
          AND wf.construction_work_id = NEW.construction_work_id
    );
END;

CREATE TRIGGER trg_work_facility_delete_guard
BEFORE DELETE ON construction_work_facilities
BEGIN
    SELECT RAISE(ABORT, 'facility has linked work components')
    WHERE EXISTS (
        SELECT 1
        FROM construction_work_components AS wc
        JOIN facility_components AS component
          ON component.component_id = wc.component_id
        WHERE wc.construction_work_id = OLD.construction_work_id
          AND component.facility_id = OLD.facility_id
    );
END;

CREATE TRIGGER trg_work_facility_update_guard
BEFORE UPDATE OF construction_work_id, facility_id
ON construction_work_facilities
BEGIN
    SELECT RAISE(ABORT, 'facility has linked work components')
    WHERE EXISTS (
        SELECT 1
        FROM construction_work_components AS wc
        JOIN facility_components AS component
          ON component.component_id = wc.component_id
        WHERE wc.construction_work_id = OLD.construction_work_id
          AND component.facility_id = OLD.facility_id
    );
END;


-- 5. Project stages linked to construction works

CREATE TABLE project_construction_links (
    project_construction_link_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    construction_work_id INTEGER NOT NULL,

    progress_weight REAL NOT NULL DEFAULT 1
        CHECK (progress_weight >= 0),

    remarks TEXT,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id, stage_id)
        REFERENCES project_stages(project_id, stage_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id)
        ON DELETE RESTRICT,

    UNIQUE (stage_id, construction_work_id)
);

CREATE INDEX idx_project_construction_work
ON project_construction_links(construction_work_id);

CREATE INDEX idx_project_construction_project
ON project_construction_links(project_id);

-- Only CONSTRUCTION stages may reference construction works.

CREATE TRIGGER trg_project_construction_stage_insert
BEFORE INSERT ON project_construction_links
BEGIN
    SELECT RAISE(ABORT, 'stage is not construction')
    WHERE NOT EXISTS (
        SELECT 1
        FROM project_stages
        WHERE stage_id = NEW.stage_id
          AND project_id = NEW.project_id
          AND stage_type_code = 'CONSTRUCTION'
    );
END;

CREATE TRIGGER trg_project_construction_stage_update
BEFORE UPDATE OF project_id, stage_id
ON project_construction_links
BEGIN
    SELECT RAISE(ABORT, 'stage is not construction')
    WHERE NOT EXISTS (
        SELECT 1
        FROM project_stages
        WHERE stage_id = NEW.stage_id
          AND project_id = NEW.project_id
          AND stage_type_code = 'CONSTRUCTION'
    );
END;


-- 6. Construction progress and status history

CREATE TABLE construction_status_history (
    construction_status_history_id INTEGER PRIMARY KEY,
    construction_work_id INTEGER NOT NULL,

    previous_status_code TEXT,
    new_status_code TEXT NOT NULL,

    previous_progress_percent REAL
        CHECK (
            previous_progress_percent IS NULL
            OR previous_progress_percent BETWEEN 0 AND 100
        ),

    new_progress_percent REAL NOT NULL
        CHECK (new_progress_percent BETWEEN 0 AND 100),

    change_reason TEXT,
    changed_by_user_id INTEGER,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (previous_status_code)
        REFERENCES construction_statuses(status_code)
        ON DELETE RESTRICT,

    FOREIGN KEY (new_status_code)
        REFERENCES construction_statuses(status_code)
        ON DELETE RESTRICT,

    FOREIGN KEY (changed_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        previous_status_code IS NOT new_status_code
        OR previous_progress_percent IS NOT new_progress_percent
    )
);

CREATE INDEX idx_construction_status_history_work
ON construction_status_history(
    construction_work_id,
    changed_at
);


-- 7. Construction change history

CREATE TABLE construction_change_history (
    construction_change_id INTEGER PRIMARY KEY,
    construction_work_id INTEGER NOT NULL,

    change_number INTEGER NOT NULL
        CHECK (change_number >= 1),

    change_type_code TEXT NOT NULL
        CHECK (
            change_type_code IN (
                'CONTRACT_AMOUNT',
                'CONSTRUCTION_PERIOD',
                'SCOPE',
                'CONTRACTOR',
                'OTHER'
            )
        ),

    changed_on TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    change_reason TEXT NOT NULL
        CHECK (length(trim(change_reason)) > 0),

    approved_by_user_id INTEGER,
    recorded_by_user_id INTEGER,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (construction_work_id)
        REFERENCES construction_works(construction_work_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (approved_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (construction_work_id, change_number)
);

CREATE INDEX idx_construction_change_history_work
ON construction_change_history(
    construction_work_id,
    changed_on
);
