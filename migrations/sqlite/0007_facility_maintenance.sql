
-- Migration: 0007_facility_maintenance
-- Purpose: Facility inspections, repairs, construction,
--          disaster records, and component maintenance.
-- Dates: YYYY-MM-DD
-- Timestamps: UTC ISO 8601 or SQLite UTC CURRENT_TIMESTAMP

-- =========================================================
-- 1. Facility inspections
-- =========================================================

CREATE TABLE facility_inspections (
    inspection_id INTEGER PRIMARY KEY,
    inspection_uuid TEXT NOT NULL UNIQUE,
    facility_id INTEGER NOT NULL,

    inspection_date TEXT NOT NULL,
    inspection_type TEXT NOT NULL
        CHECK (
            inspection_type IN (
                'STATUTORY',
                'PERIODIC',
                'EMERGENCY',
                'FOLLOW_UP',
                'OTHER'
            )
        ),

    inspection_method TEXT NOT NULL
        CHECK (
            inspection_method IN (
                'DIRECT',
                'OUTSOURCED',
                'OTHER'
            )
        ),

    inspection_name TEXT,
    inspection_result TEXT,
    condition_rating TEXT,
    findings TEXT,
    recommendations TEXT,

    inspector_user_id INTEGER,
    contractor_organization_id INTEGER,

    next_inspection_due_date TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (inspector_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (contractor_organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT,

    CHECK (
        next_inspection_due_date IS NULL
        OR next_inspection_due_date >= inspection_date
    )
);

CREATE INDEX idx_inspections_facility_date
ON facility_inspections(facility_id, inspection_date);

CREATE INDEX idx_inspections_due_date
ON facility_inspections(next_inspection_due_date);


-- =========================================================
-- 2. Facility repairs
-- =========================================================

CREATE TABLE facility_repairs (
    repair_id INTEGER PRIMARY KEY,
    repair_uuid TEXT NOT NULL UNIQUE,
    facility_id INTEGER NOT NULL,

    repair_name TEXT NOT NULL,
    repair_type TEXT NOT NULL
        CHECK (
            repair_type IN (
                'PLANNED',
                'EMERGENCY',
                'PREVENTIVE',
                'OTHER'
            )
        ),

    started_on TEXT,
    completed_on TEXT,

    repair_description TEXT,
    repair_reason TEXT,

    contractor_organization_id INTEGER,
    responsible_user_id INTEGER,

    actual_cost_yen INTEGER
        CHECK (
            actual_cost_yen IS NULL
            OR actual_cost_yen >= 0
        ),

    completion_result TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (contractor_organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        completed_on IS NULL
        OR started_on IS NULL
        OR completed_on >= started_on
    )
);

CREATE INDEX idx_repairs_facility
ON facility_repairs(facility_id);

CREATE INDEX idx_repairs_completed
ON facility_repairs(completed_on);


-- =========================================================
-- 3. Facility construction history
-- =========================================================

CREATE TABLE facility_construction_history (
    construction_history_id INTEGER PRIMARY KEY,
    construction_uuid TEXT NOT NULL UNIQUE,
    facility_id INTEGER NOT NULL,

    project_name TEXT NOT NULL,
    construction_type TEXT,

    contract_date TEXT,
    started_on TEXT,
    completed_on TEXT,

    contractor_organization_id INTEGER,

    contract_amount_yen INTEGER
        CHECK (
            contract_amount_yen IS NULL
            OR contract_amount_yen >= 0
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

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (contractor_organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT,

    CHECK (
        completed_on IS NULL
        OR started_on IS NULL
        OR completed_on >= started_on
    )
);

CREATE INDEX idx_construction_facility
ON facility_construction_history(facility_id);

CREATE INDEX idx_construction_completed
ON facility_construction_history(completed_on);


-- =========================================================
-- 4. Facility disaster history
-- =========================================================

CREATE TABLE facility_disaster_history (
    disaster_history_id INTEGER PRIMARY KEY,
    disaster_uuid TEXT NOT NULL UNIQUE,
    facility_id INTEGER NOT NULL,

    occurred_on TEXT NOT NULL,

    disaster_type TEXT NOT NULL
        CHECK (
            disaster_type IN (
                'FLOOD',
                'TYPHOON',
                'EARTHQUAKE',
                'LANDSLIDE',
                'HEAVY_RAIN',
                'OTHER'
            )
        ),

    disaster_name TEXT,
    damage_description TEXT NOT NULL,
    response_description TEXT,

    damage_severity TEXT
        CHECK (
            damage_severity IS NULL
            OR damage_severity IN (
                'MINOR',
                'MODERATE',
                'SEVERE',
                'CRITICAL',
                'UNKNOWN'
            )
        ),

    recorded_by_user_id INTEGER,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_disaster_facility
ON facility_disaster_history(facility_id);

CREATE INDEX idx_disaster_occurred
ON facility_disaster_history(occurred_on);


-- =========================================================
-- 5. Component maintenance history
-- =========================================================

CREATE TABLE component_maintenance_history (
    component_maintenance_id INTEGER PRIMARY KEY,
    maintenance_uuid TEXT NOT NULL UNIQUE,
    component_id INTEGER NOT NULL,

    maintenance_date TEXT NOT NULL,

    maintenance_type TEXT NOT NULL
        CHECK (
            maintenance_type IN (
                'INSPECTION',
                'REPAIR',
                'REPLACEMENT',
                'OVERHAUL',
                'OTHER'
            )
        ),

    maintenance_name TEXT NOT NULL,
    maintenance_description TEXT,

    contractor_organization_id INTEGER,
    responsible_user_id INTEGER,

    cost_yen INTEGER
        CHECK (
            cost_yen IS NULL
            OR cost_yen >= 0
        ),

    result_description TEXT,
    remarks TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (component_id)
        REFERENCES facility_components(component_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (contractor_organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_component_maintenance_component
ON component_maintenance_history(
    component_id,
    maintenance_date
);
