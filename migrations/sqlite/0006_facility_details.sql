
-- Migration: 0006_facility_details
-- Purpose: Facility-specific attributes and components

-- =========================================================
-- 1. Road details
-- =========================================================

CREATE TABLE road_details (
    facility_id INTEGER PRIMARY KEY,
    route_number TEXT,
    road_classification TEXT
        CHECK (
            road_classification IS NULL OR
            road_classification IN (
                'NATIONAL',
                'PREFECTURAL',
                'MUNICIPAL',
                'POSITION_DESIGNATED',
                'OTHER'
            )
        ),
    is_school_route INTEGER NOT NULL DEFAULT 0
        CHECK (is_school_route IN (0, 1)),
    road_width_m REAL CHECK (
        road_width_m IS NULL OR road_width_m >= 0
    ),
    road_length_m REAL CHECK (
        road_length_m IS NULL OR road_length_m >= 0
    ),
    pavement_type TEXT,
    remarks TEXT,
    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT
);

-- =========================================================
-- 2. Bridge details
-- =========================================================

CREATE TABLE bridge_details (
    facility_id INTEGER PRIMARY KEY,
    bridge_number TEXT UNIQUE,
    bridge_length_m REAL CHECK (
        bridge_length_m IS NULL OR bridge_length_m > 0
    ),
    bridge_width_m REAL CHECK (
        bridge_width_m IS NULL OR bridge_width_m > 0
    ),
    construction_year INTEGER CHECK (
        construction_year IS NULL OR
        construction_year BETWEEN 1800 AND 2200
    ),
    structure_type TEXT,
    is_direct_inspection INTEGER NOT NULL DEFAULT 0
        CHECK (is_direct_inspection IN (0, 1)),
    inspection_interval_years INTEGER NOT NULL DEFAULT 5
        CHECK (inspection_interval_years > 0),
    remarks TEXT,
    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_bridge_direct_inspection
ON bridge_details(is_direct_inspection);

-- =========================================================
-- 3. River details
-- =========================================================

CREATE TABLE river_details (
    facility_id INTEGER PRIMARY KEY,
    river_system_name TEXT,
    river_length_m REAL CHECK (
        river_length_m IS NULL OR river_length_m >= 0
    ),
    river_width_m REAL CHECK (
        river_width_m IS NULL OR river_width_m >= 0
    ),
    management_notes TEXT,
    remarks TEXT,
    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT
);

-- River classification is represented by facility_types.

-- =========================================================
-- 4. Park details
-- =========================================================

CREATE TABLE park_details (
    facility_id INTEGER PRIMARY KEY,
    park_area_sqm REAL CHECK (
        park_area_sqm IS NULL OR park_area_sqm >= 0
    ),
    opened_on TEXT,
    park_classification TEXT,
    remarks TEXT,
    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT
);

-- =========================================================
-- 5. Agricultural civil engineering details
-- =========================================================

CREATE TABLE agricultural_facility_details (
    facility_id INTEGER PRIMARY KEY,
    facility_function TEXT,
    operation_method TEXT,
    operating_organization_id INTEGER,
    installed_capacity_kw REAL CHECK (
        installed_capacity_kw IS NULL OR
        installed_capacity_kw >= 0
    ),
    remarks TEXT,
    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (operating_organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT
);

-- =========================================================
-- 6. Components belonging to facilities
-- Examples: pumps, gates, playground equipment, buildings
-- =========================================================

CREATE TABLE facility_components (
    component_id INTEGER PRIMARY KEY,
    component_uuid TEXT NOT NULL UNIQUE,
    facility_id INTEGER NOT NULL,
    parent_component_id INTEGER,
    component_code TEXT,
    component_name TEXT NOT NULL,
    component_type TEXT NOT NULL,
    manufacturer TEXT,
    model_number TEXT,
    serial_number TEXT,
    installed_on TEXT,
    abolished_on TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    remarks TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (parent_component_id)
        REFERENCES facility_components(component_id)
        ON DELETE RESTRICT,

    UNIQUE (facility_id, component_code),

    CHECK (
        parent_component_id IS NULL
        OR parent_component_id <> component_id
    ),

    CHECK (
        abolished_on IS NULL
        OR installed_on IS NULL
        OR abolished_on >= installed_on
    )
);

CREATE INDEX idx_components_facility
ON facility_components(facility_id);

CREATE INDEX idx_components_parent
ON facility_components(parent_component_id);

CREATE INDEX idx_components_type
ON facility_components(component_type);
