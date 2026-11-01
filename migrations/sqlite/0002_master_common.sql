
-- Migration: 0002_master_common
-- Purpose: Common master tables
-- Status: Development

CREATE TABLE departments (
    department_id INTEGER PRIMARY KEY,
    department_code TEXT NOT NULL UNIQUE,
    department_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE roles (
    role_id INTEGER PRIMARY KEY,
    role_code TEXT NOT NULL UNIQUE,
    role_name TEXT NOT NULL,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE importance_levels (
    importance_level_id INTEGER PRIMARY KEY,
    importance_code TEXT NOT NULL UNIQUE,
    importance_name TEXT NOT NULL,
    priority_order INTEGER NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE facility_statuses (
    facility_status_id INTEGER PRIMARY KEY,
    status_code TEXT NOT NULL UNIQUE,
    status_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE management_classifications (
    management_classification_id INTEGER PRIMARY KEY,
    classification_code TEXT NOT NULL UNIQUE,
    classification_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

-- Initial roles
INSERT INTO roles (
    role_code, role_name, description
) VALUES
    ('ADMINISTRATOR', '管理者', 'システム全体の管理'),
    ('EDITOR', '編集者', '業務データの登録・更新'),
    ('VIEWER', '閲覧者', '業務データの閲覧');

-- Initial importance levels
INSERT INTO importance_levels (
    importance_code, importance_name, priority_order
) VALUES
    ('CRITICAL', '緊急', 1),
    ('HIGH', '高', 2),
    ('NORMAL', '通常', 3),
    ('LOW', '低', 4);

-- Initial facility statuses
INSERT INTO facility_statuses (
    status_code, status_name, sort_order
) VALUES
    ('ACTIVE', '供用中', 1),
    ('SUSPENDED', '休止中', 2),
    ('UNDER_CONSTRUCTION', '工事中', 3),
    ('DECOMMISSIONED', '廃止', 4);

-- Initial management classifications
INSERT INTO management_classifications (
    classification_code, classification_name, sort_order
) VALUES
    ('NATIONAL', '国', 1),
    ('PREFECTURAL', '都道府県', 2),
    ('MUNICIPAL', '市町村', 3),
    ('OTHER', 'その他', 4);
