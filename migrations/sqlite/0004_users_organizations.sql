
-- Migration: 0004_users_organizations
-- Purpose: Users, departments, roles and organizations
-- Status: Development

-- =========================================================
-- 1. Users
-- =========================================================

CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    user_uuid TEXT NOT NULL UNIQUE,
    login_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT,
    password_algorithm TEXT,
    auth_provider TEXT NOT NULL DEFAULT 'LOCAL',
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    is_locked INTEGER NOT NULL DEFAULT 0
        CHECK (is_locked IN (0, 1)),
    locked_at TEXT,
    locked_reason TEXT,
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        auth_provider <> 'LOCAL'
        OR (
            password_hash IS NOT NULL
            AND password_algorithm IS NOT NULL
        )
    )
);

CREATE INDEX idx_users_active
ON users(is_active, is_locked);

-- =========================================================
-- 2. User roles
-- =========================================================

CREATE TABLE user_roles (
    user_role_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT,
    assigned_by_user_id INTEGER,
    FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (role_id)
        REFERENCES roles(role_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (assigned_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,
    CHECK (
        revoked_at IS NULL
        OR revoked_at >= assigned_at
    )
);

CREATE UNIQUE INDEX idx_user_roles_active_unique
ON user_roles(user_id, role_id)
WHERE revoked_at IS NULL;

-- =========================================================
-- 3. Department assignment history
-- =========================================================

CREATE TABLE user_departments (
    user_department_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    is_primary INTEGER NOT NULL DEFAULT 1
        CHECK (is_primary IN (0, 1)),
    FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (department_id)
        REFERENCES departments(department_id)
        ON DELETE RESTRICT,
    CHECK (
        ended_at IS NULL
        OR ended_at >= started_at
    )
);

CREATE INDEX idx_user_departments_user
ON user_departments(user_id, started_at);

CREATE INDEX idx_user_departments_department
ON user_departments(department_id, ended_at);

CREATE UNIQUE INDEX idx_user_primary_department
ON user_departments(user_id)
WHERE ended_at IS NULL AND is_primary = 1;

-- =========================================================
-- 4. Organizations
-- =========================================================

CREATE TABLE organizations (
    organization_id INTEGER PRIMARY KEY,
    organization_uuid TEXT NOT NULL UNIQUE,
    organization_code TEXT UNIQUE,
    organization_name TEXT NOT NULL,
    organization_type_id INTEGER NOT NULL,
    postal_code TEXT,
    address TEXT,
    phone TEXT,
    email TEXT,
    remarks TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_type_id)
        REFERENCES organization_types(organization_type_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_organizations_name
ON organizations(organization_name);

-- =========================================================
-- 5. Organization contacts
-- =========================================================

CREATE TABLE organization_contacts (
    organization_contact_id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    contact_name TEXT NOT NULL,
    department_name TEXT,
    position_name TEXT,
    phone TEXT,
    mobile_phone TEXT,
    email TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0
        CHECK (is_primary IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    remarks TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id)
        REFERENCES organizations(organization_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_organization_contacts_org
ON organization_contacts(organization_id);

CREATE UNIQUE INDEX idx_organization_primary_contact
ON organization_contacts(organization_id)
WHERE is_primary = 1 AND is_active = 1;

-- =========================================================
-- 6. Login history
-- =========================================================

CREATE TABLE user_login_history (
    login_history_id INTEGER PRIMARY KEY,
    user_id INTEGER,
    attempted_login_id TEXT,
    login_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_code TEXT NOT NULL
        CHECK (
            result_code IN (
                'SUCCESS',
                'INVALID_CREDENTIALS',
                'ACCOUNT_LOCKED',
                'ACCOUNT_DISABLED',
                'ERROR'
            )
        ),
    client_identifier TEXT,
    auth_provider TEXT NOT NULL DEFAULT 'LOCAL',
    remarks TEXT,
    FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_user_login_history_user
ON user_login_history(user_id, login_at);

CREATE INDEX idx_user_login_history_date
ON user_login_history(login_at);
