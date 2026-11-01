
-- Migration: 0005_facility_foundations
-- Purpose: Facility registry and GIS foundations

-- 1. Facility categories

CREATE TABLE facility_categories (
    facility_category_id INTEGER PRIMARY KEY,
    category_code TEXT NOT NULL UNIQUE,
    category_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

-- 2. Facility types

CREATE TABLE facility_types (
    facility_type_id INTEGER PRIMARY KEY,
    facility_category_id INTEGER NOT NULL,
    type_code TEXT NOT NULL UNIQUE,
    type_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    FOREIGN KEY (facility_category_id)
        REFERENCES facility_categories(facility_category_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_facility_types_category
ON facility_types(facility_category_id);

-- 3. Managed facilities

CREATE TABLE managed_facilities (
    facility_id INTEGER PRIMARY KEY,
    facility_uuid TEXT NOT NULL UNIQUE,
    facility_code TEXT UNIQUE,
    facility_name TEXT NOT NULL,
    facility_type_id INTEGER NOT NULL,
    facility_status_id INTEGER NOT NULL,
    management_classification_id INTEGER,
    responsible_department_id INTEGER,
    description TEXT,
    installed_on TEXT,
    abolished_on TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (facility_type_id)
        REFERENCES facility_types(facility_type_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (facility_status_id)
        REFERENCES facility_statuses(facility_status_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (management_classification_id)
        REFERENCES management_classifications(
            management_classification_id
        )
        ON DELETE RESTRICT,

    FOREIGN KEY (responsible_department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,

    CHECK (
        abolished_on IS NULL
        OR installed_on IS NULL
        OR abolished_on >= installed_on
    )
);

CREATE INDEX idx_facilities_type
ON managed_facilities(facility_type_id);

CREATE INDEX idx_facilities_status
ON managed_facilities(facility_status_id);

CREATE INDEX idx_facilities_department
ON managed_facilities(responsible_department_id);

-- 4. Facility GIS geometries

CREATE TABLE facility_geometries (
    facility_geometry_id INTEGER PRIMARY KEY,
    facility_id INTEGER NOT NULL,
    geometry_role_id INTEGER NOT NULL,
    geometry_type TEXT NOT NULL
        CHECK (
            geometry_type IN (
                'Point',
                'MultiPoint',
                'LineString',
                'MultiLineString',
                'Polygon',
                'MultiPolygon'
            )
        ),
    geometry_json TEXT NOT NULL
        CHECK (json_valid(geometry_json)),
    coordinate_reference_system TEXT NOT NULL
        DEFAULT 'EPSG:4326'
        CHECK (
            coordinate_reference_system = 'EPSG:4326'
        ),
    is_primary INTEGER NOT NULL DEFAULT 0
        CHECK (is_primary IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (geometry_role_id)
        REFERENCES geometry_roles(geometry_role_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_facility_geometries_facility
ON facility_geometries(facility_id);

CREATE UNIQUE INDEX idx_facility_primary_geometry
ON facility_geometries(facility_id)
WHERE is_primary = 1;

-- 5. Facility tags

CREATE TABLE facility_tags (
    facility_tag_id INTEGER PRIMARY KEY,
    facility_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    assigned_by_user_id INTEGER,
    remarks TEXT,

    FOREIGN KEY (facility_id)
        REFERENCES managed_facilities(facility_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (tag_id)
        REFERENCES tags(tag_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (assigned_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    UNIQUE (facility_id, tag_id)
);

CREATE INDEX idx_facility_tags_tag
ON facility_tags(tag_id);

-- 6. Initial facility categories

INSERT INTO facility_categories (
    category_code,
    category_name,
    sort_order
)
VALUES
('ROAD', '道路', 1),
('RIVER', '河川', 2),
('PARK', '公園', 3),
('AGRICULTURAL', '農業土木', 4);

-- 7. Initial facility types
-- Category IDs are resolved by category_code.

INSERT INTO facility_types (
    facility_category_id,
    type_code,
    type_name,
    sort_order
)
VALUES
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'ROAD'),
    'ROAD', '道路', 1
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'ROAD'),
    'BRIDGE', '橋梁', 2
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'ROAD'),
    'STREETLIGHT', '街路灯', 3
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'RIVER'),
    'CLASS_A_RIVER', '一級河川', 1
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'RIVER'),
    'CLASS_B_RIVER', '二級河川', 2
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'RIVER'),
    'QUASI_RIVER', '準用河川', 3
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'RIVER'),
    'ORDINARY_RIVER', '普通河川', 4
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'RIVER'),
    'IRRIGATION_CANAL', '用水路', 5
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'PARK'),
    'CITY_PARK', '都市公園', 1
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'AGRICULTURAL'),
    'DRAINAGE_STATION', '排水機場', 1
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'AGRICULTURAL'),
    'PUMP_STATION', 'ポンプ場', 2
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'AGRICULTURAL'),
    'WATER_GATE', '水門', 3
),
(
    (SELECT facility_category_id
     FROM facility_categories
     WHERE category_code = 'AGRICULTURAL'),
    'SLUICE', '樋門・樋管', 4
);
