
-- ============================================================
-- Migration: 0008_maintenance_planning
-- Purpose:
--   Long-term maintenance plans, projects, stages,
--   progress history, and progress-review reminders.
--
-- Existing migrations 0001-0007 must not be modified.
-- ============================================================

-- ============================================================
-- 1. Plan status master
-- ============================================================

CREATE TABLE plan_statuses (
    status_code TEXT PRIMARY KEY,
    status_name TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_terminal INTEGER NOT NULL DEFAULT 0
        CHECK (is_terminal IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

INSERT INTO plan_statuses
    (status_code, status_name, display_order, is_terminal)
VALUES
    ('DRAFT', '草案', 10, 0),
    ('UNDER_REVIEW', '審査中', 20, 0),
    ('APPROVED', '承認済', 30, 0),
    ('IN_PROGRESS', '実施中', 40, 0),
    ('UNDER_REVISION', '見直し中', 50, 0),
    ('ON_HOLD', '保留', 60, 0),
    ('COMPLETED', '完了', 70, 1),
    ('CANCELLED', '廃止', 80, 1);


-- ============================================================
-- 2. Project status master
-- ============================================================

CREATE TABLE project_statuses (
    status_code TEXT PRIMARY KEY,
    status_name TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_terminal INTEGER NOT NULL DEFAULT 0
        CHECK (is_terminal IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

INSERT INTO project_statuses
    (status_code, status_name, display_order, is_terminal)
VALUES
    ('DRAFT', '計画中', 10, 0),
    ('PREPARING', '実施準備中', 20, 0),
    ('IN_PROGRESS', '実施中', 30, 0),
    ('ON_HOLD', '保留', 40, 0),
    ('COMPLETED', '完了', 50, 1),
    ('CANCELLED', '中止', 60, 1);


-- ============================================================
-- 3. Stage type master
-- ============================================================

CREATE TABLE stage_types (
    stage_type_code TEXT PRIMARY KEY,
    stage_type_name TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    default_progress_weight REAL NOT NULL DEFAULT 0
        CHECK (default_progress_weight >= 0),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

INSERT INTO stage_types
    (
        stage_type_code,
        stage_type_name,
        display_order,
        default_progress_weight
    )
VALUES
    ('BUDGET_REQUEST', '予算要求', 10, 10),
    ('SURVEY', '調査', 20, 0),
    ('DESIGN', '設計', 30, 30),
    ('LAND_ACQUISITION', '用地', 40, 0),
    ('CONSTRUCTION', '工事', 50, 60),
    ('INSPECTION', '検査', 60, 0),
    ('OTHER', 'その他', 70, 0);


-- ============================================================
-- 4. Stage status master
-- ============================================================

CREATE TABLE stage_statuses (
    status_code TEXT PRIMARY KEY,
    status_name TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_terminal INTEGER NOT NULL DEFAULT 0
        CHECK (is_terminal IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

INSERT INTO stage_statuses
    (status_code, status_name, display_order, is_terminal)
VALUES
    ('NOT_STARTED', '未着手', 10, 0),
    ('PREPARING', '準備中', 20, 0),
    ('PROCUREMENT', '発注中', 30, 0),
    ('CONTRACTED', '契約済', 40, 0),
    ('IN_PROGRESS', '実施中', 50, 0),
    ('ON_HOLD', '一時停止', 60, 0),
    ('COMPLETED', '完了', 70, 1),
    ('CANCELLED', '中止', 80, 1);


-- ============================================================
-- 5. Workflow transitions
-- ============================================================

CREATE TABLE workflow_transitions (
    transition_id INTEGER PRIMARY KEY,
    workflow_type TEXT NOT NULL
        CHECK (
            workflow_type IN (
                'PLAN',
                'PROJECT',
                'STAGE',
                'CONSTRUCTION'
            )
        ),
    from_status_code TEXT NOT NULL,
    to_status_code TEXT NOT NULL,

    requires_reason INTEGER NOT NULL DEFAULT 0
        CHECK (requires_reason IN (0, 1)),

    requires_confirmation INTEGER NOT NULL DEFAULT 0
        CHECK (requires_confirmation IN (0, 1)),

    requires_progress_review INTEGER NOT NULL DEFAULT 0
        CHECK (requires_progress_review IN (0, 1)),

    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),

    UNIQUE (
        workflow_type,
        from_status_code,
        to_status_code
    ),

    CHECK (from_status_code <> to_status_code)
);

INSERT INTO workflow_transitions (
    workflow_type,
    from_status_code,
    to_status_code,
    requires_reason,
    requires_confirmation,
    requires_progress_review
)
VALUES
    ('PLAN','DRAFT','UNDER_REVIEW',0,0,0),
    ('PLAN','UNDER_REVIEW','APPROVED',0,1,0),
    ('PLAN','UNDER_REVIEW','DRAFT',1,0,0),
    ('PLAN','APPROVED','IN_PROGRESS',0,1,1),
    ('PLAN','APPROVED','UNDER_REVISION',1,1,1),
    ('PLAN','IN_PROGRESS','UNDER_REVISION',1,1,1),
    ('PLAN','UNDER_REVISION','IN_PROGRESS',1,1,1),
    ('PLAN','UNDER_REVISION','UNDER_REVIEW',1,0,1),
    ('PLAN','IN_PROGRESS','ON_HOLD',1,1,1),
    ('PLAN','ON_HOLD','IN_PROGRESS',1,1,1),
    ('PLAN','IN_PROGRESS','COMPLETED',0,1,1),
    ('PLAN','COMPLETED','UNDER_REVISION',1,1,1),
    ('PLAN','DRAFT','CANCELLED',1,1,0),
    ('PLAN','APPROVED','CANCELLED',1,1,1),
    ('PLAN','IN_PROGRESS','CANCELLED',1,1,1),
    ('PLAN','ON_HOLD','CANCELLED',1,1,1),
    ('PLAN','UNDER_REVISION','CANCELLED',1,1,1),

    ('PROJECT','DRAFT','PREPARING',0,0,1),
    ('PROJECT','PREPARING','IN_PROGRESS',0,0,1),
    ('PROJECT','IN_PROGRESS','ON_HOLD',1,1,1),
    ('PROJECT','ON_HOLD','IN_PROGRESS',1,1,1),
    ('PROJECT','IN_PROGRESS','COMPLETED',0,1,1),
    ('PROJECT','COMPLETED','IN_PROGRESS',1,1,1),
    ('PROJECT','DRAFT','CANCELLED',1,1,1),
    ('PROJECT','PREPARING','CANCELLED',1,1,1),
    ('PROJECT','IN_PROGRESS','CANCELLED',1,1,1),
    ('PROJECT','ON_HOLD','CANCELLED',1,1,1),

    ('STAGE','NOT_STARTED','PREPARING',0,0,1),
    ('STAGE','NOT_STARTED','IN_PROGRESS',0,0,1),
    ('STAGE','PREPARING','PROCUREMENT',0,0,1),
    ('STAGE','PREPARING','IN_PROGRESS',0,0,1),
    ('STAGE','PROCUREMENT','CONTRACTED',0,0,1),
    ('STAGE','CONTRACTED','IN_PROGRESS',0,0,1),
    ('STAGE','IN_PROGRESS','COMPLETED',0,1,1),
    ('STAGE','IN_PROGRESS','ON_HOLD',1,1,1),
    ('STAGE','ON_HOLD','IN_PROGRESS',1,1,1),
    ('STAGE','COMPLETED','IN_PROGRESS',1,1,1),
    ('STAGE','NOT_STARTED','CANCELLED',1,1,1),
    ('STAGE','PREPARING','CANCELLED',1,1,1),
    ('STAGE','PROCUREMENT','CANCELLED',1,1,1),
    ('STAGE','CONTRACTED','CANCELLED',1,1,1),
    ('STAGE','IN_PROGRESS','CANCELLED',1,1,1),
    ('STAGE','ON_HOLD','CANCELLED',1,1,1);


-- ============================================================
-- 6. Maintenance plans
-- ============================================================

CREATE TABLE maintenance_plans (
    plan_id INTEGER PRIMARY KEY,
    plan_uuid TEXT NOT NULL UNIQUE,
    plan_code TEXT UNIQUE,
    plan_name TEXT NOT NULL,
    description TEXT,

    start_fiscal_year INTEGER NOT NULL
        CHECK (start_fiscal_year BETWEEN 1900 AND 2200),

    end_fiscal_year INTEGER NOT NULL
        CHECK (end_fiscal_year BETWEEN 1900 AND 2200),

    status_code TEXT NOT NULL DEFAULT 'DRAFT',

    official_progress_percent REAL NOT NULL DEFAULT 0
        CHECK (
            official_progress_percent BETWEEN 0 AND 100
        ),

    responsible_department_id INTEGER,
    responsible_user_id INTEGER,

    approved_on TEXT,
    next_review_on TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (status_code)
        REFERENCES plan_statuses(status_code),

    FOREIGN KEY (responsible_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (end_fiscal_year >= start_fiscal_year),
    CHECK (length(trim(plan_name)) > 0)
);

CREATE INDEX idx_maintenance_plans_status
ON maintenance_plans(status_code);

CREATE INDEX idx_maintenance_plans_department
ON maintenance_plans(responsible_department_id);


-- ============================================================
-- 7. Maintenance projects
-- ============================================================

CREATE TABLE maintenance_projects (
    project_id INTEGER PRIMARY KEY,
    project_uuid TEXT NOT NULL UNIQUE,
    project_code TEXT UNIQUE,
    project_name TEXT NOT NULL,
    description TEXT,

    status_code TEXT NOT NULL DEFAULT 'DRAFT',

    official_progress_percent REAL NOT NULL DEFAULT 0
        CHECK (
            official_progress_percent BETWEEN 0 AND 100
        ),

    planned_start_fiscal_year INTEGER
        CHECK (
            planned_start_fiscal_year IS NULL
            OR planned_start_fiscal_year BETWEEN 1900 AND 2200
        ),

    planned_end_fiscal_year INTEGER
        CHECK (
            planned_end_fiscal_year IS NULL
            OR planned_end_fiscal_year BETWEEN 1900 AND 2200
        ),

    responsible_department_id INTEGER,
    responsible_user_id INTEGER,
    priority_level_id INTEGER,

    estimated_total_cost_yen INTEGER
        CHECK (
            estimated_total_cost_yen IS NULL
            OR estimated_total_cost_yen >= 0
        ),

    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (status_code)
        REFERENCES project_statuses(status_code),

    FOREIGN KEY (responsible_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (priority_level_id)
        REFERENCES importance_levels(importance_level_id)
        ON DELETE RESTRICT,

    CHECK (length(trim(project_name)) > 0),

    CHECK (
        planned_start_fiscal_year IS NULL
        OR planned_end_fiscal_year IS NULL
        OR planned_end_fiscal_year >= planned_start_fiscal_year
    )
);

CREATE INDEX idx_maintenance_projects_status
ON maintenance_projects(status_code);

CREATE INDEX idx_maintenance_projects_department
ON maintenance_projects(responsible_department_id);


-- ============================================================
-- 8. Plan items (plan-project membership)
-- ============================================================

CREATE TABLE maintenance_plan_items (
    plan_item_id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,

    display_order INTEGER NOT NULL DEFAULT 0,

    progress_weight REAL NOT NULL DEFAULT 1
        CHECK (progress_weight >= 0),

    is_included_in_progress INTEGER NOT NULL DEFAULT 1
        CHECK (is_included_in_progress IN (0, 1)),

    added_on TEXT NOT NULL DEFAULT CURRENT_DATE,
    excluded_on TEXT,
    remarks TEXT,

    FOREIGN KEY (plan_id)
        REFERENCES maintenance_plans(plan_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    UNIQUE (plan_id, project_id),

    CHECK (
        excluded_on IS NULL
        OR excluded_on >= added_on
    ),

    CHECK (
        excluded_on IS NULL
        OR is_included_in_progress = 0
    )
);

CREATE INDEX idx_plan_items_project
ON maintenance_plan_items(project_id);


-- ============================================================
-- 9. Project facility/component targets
-- ============================================================

CREATE TABLE project_facilities (
    project_facility_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    facility_id INTEGER NOT NULL,
    component_id INTEGER,

    target_description TEXT,
    remarks TEXT,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (component_id)
        REFERENCES facility_components(component_id)
        ON DELETE RESTRICT
);

-- Prevent duplicate whole-facility targets.
CREATE UNIQUE INDEX uq_project_whole_facility
ON project_facilities(project_id, facility_id)
WHERE component_id IS NULL;

-- Prevent duplicate component targets.
CREATE UNIQUE INDEX uq_project_component
ON project_facilities(project_id, component_id)
WHERE component_id IS NOT NULL;

CREATE INDEX idx_project_facilities_facility
ON project_facilities(facility_id);

-- Ensure a component belongs to the selected facility.
CREATE TRIGGER trg_project_facility_component_insert
BEFORE INSERT ON project_facilities
WHEN NEW.component_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'component facility mismatch')
    WHERE NOT EXISTS (
        SELECT 1
        FROM facility_components
        WHERE component_id = NEW.component_id
          AND facility_id = NEW.facility_id
    );
END;

CREATE TRIGGER trg_project_facility_component_update
BEFORE UPDATE OF facility_id, component_id
ON project_facilities
WHEN NEW.component_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'component facility mismatch')
    WHERE NOT EXISTS (
        SELECT 1
        FROM facility_components
        WHERE component_id = NEW.component_id
          AND facility_id = NEW.facility_id
    );
END;


-- ============================================================
-- 10. Project stages
-- ============================================================

CREATE TABLE project_stages (
    stage_id INTEGER PRIMARY KEY,
    stage_uuid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,

    stage_type_code TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL DEFAULT 0,

    status_code TEXT NOT NULL DEFAULT 'NOT_STARTED',

    progress_percent REAL NOT NULL DEFAULT 0
        CHECK (progress_percent BETWEEN 0 AND 100),

    progress_weight REAL NOT NULL DEFAULT 0
        CHECK (progress_weight >= 0),

    is_included_in_progress INTEGER NOT NULL DEFAULT 1
        CHECK (is_included_in_progress IN (0, 1)),

    planned_start_on TEXT,
    planned_end_on TEXT,
    actual_start_on TEXT,
    actual_end_on TEXT,

    responsible_department_id INTEGER,
    responsible_user_id INTEGER,

    hold_reason TEXT,
    next_action TEXT,
    next_action_due_on TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (stage_type_code)
        REFERENCES stage_types(stage_type_code),

    FOREIGN KEY (status_code)
        REFERENCES stage_statuses(status_code),

    FOREIGN KEY (responsible_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (project_id, stage_id),

    CHECK (length(trim(stage_name)) > 0),

    CHECK (
        planned_start_on IS NULL
        OR planned_end_on IS NULL
        OR planned_end_on >= planned_start_on
    ),

    CHECK (
        actual_start_on IS NULL
        OR actual_end_on IS NULL
        OR actual_end_on >= actual_start_on
    )
);

CREATE INDEX idx_project_stages_project
ON project_stages(project_id, stage_order);

CREATE INDEX idx_project_stages_status
ON project_stages(status_code);

CREATE INDEX idx_project_stages_next_action
ON project_stages(next_action_due_on);


-- ============================================================
-- 11. Plan progress history
-- ============================================================

CREATE TABLE plan_progress_history (
    plan_progress_history_id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,

    previous_progress_percent REAL NOT NULL
        CHECK (previous_progress_percent BETWEEN 0 AND 100),

    new_progress_percent REAL NOT NULL
        CHECK (new_progress_percent BETWEEN 0 AND 100),

    previous_status_code TEXT,
    new_status_code TEXT,

    reference_progress_percent REAL
        CHECK (
            reference_progress_percent IS NULL
            OR reference_progress_percent BETWEEN 0 AND 100
        ),

    change_reason TEXT,
    changed_by_user_id INTEGER,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (plan_id)
        REFERENCES maintenance_plans(plan_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (previous_status_code)
        REFERENCES plan_statuses(status_code),

    FOREIGN KEY (new_status_code)
        REFERENCES plan_statuses(status_code),

    FOREIGN KEY (changed_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        previous_progress_percent <> new_progress_percent
        OR previous_status_code IS NOT new_status_code
    )
);

CREATE INDEX idx_plan_progress_history_plan
ON plan_progress_history(plan_id, changed_at);


-- ============================================================
-- 12. Project and stage progress history
-- ============================================================

CREATE TABLE project_progress_history (
    project_progress_history_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    stage_id INTEGER,

    progress_scope TEXT NOT NULL
        CHECK (progress_scope IN ('PROJECT', 'STAGE')),

    previous_progress_percent REAL NOT NULL
        CHECK (previous_progress_percent BETWEEN 0 AND 100),

    new_progress_percent REAL NOT NULL
        CHECK (new_progress_percent BETWEEN 0 AND 100),

    previous_status_code TEXT,
    new_status_code TEXT,

    change_reason TEXT,
    changed_by_user_id INTEGER,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id, stage_id)
        REFERENCES project_stages(project_id, stage_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (changed_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        (progress_scope = 'PROJECT' AND stage_id IS NULL)
        OR
        (progress_scope = 'STAGE' AND stage_id IS NOT NULL)
    ),

    CHECK (
        previous_progress_percent <> new_progress_percent
        OR previous_status_code IS NOT new_status_code
    )
);

CREATE INDEX idx_project_progress_history_project
ON project_progress_history(project_id, changed_at);

CREATE INDEX idx_project_progress_history_stage
ON project_progress_history(stage_id, changed_at);


-- ============================================================
-- 13. Progress review prompts
-- ============================================================

CREATE TABLE progress_review_prompts (
    prompt_id INTEGER PRIMARY KEY,
    prompt_uuid TEXT NOT NULL UNIQUE,
    operation_uuid TEXT NOT NULL,

    plan_id INTEGER NOT NULL,
    project_id INTEGER,
    stage_id INTEGER,

    prompt_reason_code TEXT NOT NULL
        CHECK (
            prompt_reason_code IN (
                'STAGE_PROGRESS_CHANGED',
                'STAGE_STATUS_CHANGED',
                'PROJECT_PROGRESS_CHANGED',
                'PROJECT_STATUS_CHANGED',
                'PLAN_MEMBERSHIP_CHANGED',
                'PROGRESS_WEIGHT_CHANGED',
                'BUDGET_DECISION_CHANGED',
                'CONSTRUCTION_STATUS_CHANGED',
                'OTHER'
            )
        ),

    reference_progress_percent REAL
        CHECK (
            reference_progress_percent IS NULL
            OR reference_progress_percent BETWEEN 0 AND 100
        ),

    official_progress_percent REAL NOT NULL
        CHECK (
            official_progress_percent BETWEEN 0 AND 100
        ),

    review_status_code TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (
            review_status_code IN (
                'PENDING',
                'REVIEWED_UPDATED',
                'REVIEWED_UNCHANGED',
                'DISMISSED'
            )
        ),

    assigned_user_id INTEGER,
    due_on TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_on TEXT,
    resolved_by_user_id INTEGER,
    resolution_reason TEXT,

    FOREIGN KEY (plan_id)
        REFERENCES maintenance_plans(plan_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id, stage_id)
        REFERENCES project_stages(project_id, stage_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (assigned_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (resolved_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    UNIQUE (plan_id, operation_uuid),

    CHECK (
        stage_id IS NULL
        OR project_id IS NOT NULL
    ),

    CHECK (
        (review_status_code = 'PENDING' AND resolved_on IS NULL)
        OR
        (review_status_code <> 'PENDING' AND resolved_on IS NOT NULL)
    )
);

CREATE INDEX idx_progress_review_prompts_pending
ON progress_review_prompts(
    review_status_code,
    due_on
);

CREATE INDEX idx_progress_review_prompts_plan
ON progress_review_prompts(plan_id, created_at);
