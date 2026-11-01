
-- Migration 0003: Extended Common Masters
-- All times use UTC where applicable.

CREATE TABLE organization_types (
    organization_type_id INTEGER PRIMARY KEY,
    type_code TEXT NOT NULL UNIQUE,
    type_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE document_types (
    document_type_id INTEGER PRIMARY KEY,
    type_code TEXT NOT NULL UNIQUE,
    type_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE reminder_statuses (
    reminder_status_id INTEGER PRIMARY KEY,
    status_code TEXT NOT NULL UNIQUE,
    status_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE tag_groups (
    tag_group_id INTEGER PRIMARY KEY,
    group_code TEXT NOT NULL UNIQUE,
    group_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE tags (
    tag_id INTEGER PRIMARY KEY,
    tag_group_id INTEGER NOT NULL,
    tag_code TEXT NOT NULL UNIQUE,
    tag_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    FOREIGN KEY (tag_group_id)
        REFERENCES tag_groups(tag_group_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_tags_group
ON tags(tag_group_id);

CREATE TABLE geometry_roles (
    geometry_role_id INTEGER PRIMARY KEY,
    role_code TEXT NOT NULL UNIQUE,
    role_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE process_results (
    process_result_id INTEGER PRIMARY KEY,
    result_code TEXT NOT NULL UNIQUE,
    result_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

CREATE TABLE operation_types (
    operation_type_id INTEGER PRIMARY KEY,
    operation_code TEXT NOT NULL UNIQUE,
    operation_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1))
);

-- Organization types

INSERT INTO organization_types
(type_code, type_name, sort_order)
VALUES
('GOVERNMENT', '行政機関', 1),
('COMPANY', '企業', 2),
('INDIVIDUAL', '個人', 3),
('ASSOCIATION', '団体', 4),
('OTHER', 'その他', 5);

-- Document types

INSERT INTO document_types
(type_code, type_name, sort_order)
VALUES
('PHOTO', '写真', 1),
('DRAWING', '図面', 2),
('REPORT', '報告書', 3),
('APPLICATION', '申請書', 4),
('PERMIT', '許可書', 5),
('OTHER', 'その他', 6);

-- Reminder statuses

INSERT INTO reminder_statuses
(status_code, status_name, sort_order)
VALUES
('PENDING', '未対応', 1),
('IN_PROGRESS', '対応中', 2),
('COMPLETED', '完了', 3),
('CANCELLED', '取消', 4);

-- Tag groups

INSERT INTO tag_groups
(group_code, group_name, sort_order)
VALUES
('DISASTER', '災害関連', 1),
('INSPECTION', '点検関連', 2),
('CONSTRUCTION', '工事関連', 3),
('MANAGEMENT', '管理上の注意事項', 4);

-- Initial tags
-- Group IDs are resolved using stable codes.

INSERT INTO tags
(tag_group_id, tag_code, tag_name, sort_order)
VALUES
(
    (SELECT tag_group_id FROM tag_groups
     WHERE group_code = 'DISASTER'),
    'DISASTER_HISTORY', '災害履歴あり', 1
),
(
    (SELECT tag_group_id FROM tag_groups
     WHERE group_code = 'INSPECTION'),
    'INSPECTION_REQUIRED', '点検対象', 1
),
(
    (SELECT tag_group_id FROM tag_groups
     WHERE group_code = 'CONSTRUCTION'),
    'CONSTRUCTION_HISTORY', '工事履歴あり', 1
),
(
    (SELECT tag_group_id FROM tag_groups
     WHERE group_code = 'MANAGEMENT'),
    'FOLLOW_UP_REQUIRED', '経過観察が必要', 1
);

-- GIS geometry roles

INSERT INTO geometry_roles
(role_code, role_name, sort_order)
VALUES
('FACILITY', '施設位置・形状', 1),
('CASE_LOCATION', '案件位置', 2),
('TARGET_AREA', '対象区域', 3),
('REFERENCE', '参考位置・形状', 4);

-- Process results

INSERT INTO process_results
(result_code, result_name, sort_order)
VALUES
('RESOLVED', '対応完了', 1),
('REFERRED', '他機関へ引継ぎ', 2),
('NO_ACTION', '対応不要', 3),
('UNRESOLVED', '未解決', 4),
('OTHER', 'その他', 5);

-- Audit operation types

INSERT INTO operation_types
(operation_code, operation_name, sort_order)
VALUES
('CREATE', '登録', 1),
('UPDATE', '更新', 2),
('DELETE', '削除', 3),
('LOGIN', 'ログイン', 4),
('LOGOUT', 'ログアウト', 5),
('EXPORT', 'エクスポート', 6),
('IMPORT', 'インポート', 7);
