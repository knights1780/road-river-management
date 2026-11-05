-- Migration: 0017_facility_photos
-- Photo metadata and immutable audit events. Image bytes live outside SQLite.
-- Both facility and repair photos are attached to exactly one parent.
CREATE TABLE facility_photos (
    photo_id INTEGER PRIMARY KEY,
    photo_uuid TEXT NOT NULL UNIQUE,
    facility_id INTEGER,
    repair_id INTEGER,
    photo_category TEXT NOT NULL CHECK (photo_category IN (
        'OVERVIEW', 'DAMAGE', 'BEFORE', 'DURING', 'AFTER', 'INSPECTION', 'OTHER'
    )),
    photographed_on TEXT,
    caption TEXT NOT NULL DEFAULT '' CHECK (length(caption) <= 1000),
    stored_filename TEXT NOT NULL UNIQUE,
    stored_media_type TEXT NOT NULL DEFAULT 'image/webp'
        CHECK (stored_media_type = 'image/webp'),
    stored_bytes INTEGER NOT NULL CHECK (stored_bytes > 0),
    stored_sha256 TEXT NOT NULL CHECK (length(stored_sha256) = 64),
    original_filename TEXT,
    original_stored_filename TEXT UNIQUE,
    original_media_type TEXT,
    original_bytes INTEGER CHECK (original_bytes IS NULL OR original_bytes > 0),
    original_sha256 TEXT,
    created_by_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hidden_at TEXT,
    hidden_by_user_id INTEGER,
    hidden_reason TEXT,
    FOREIGN KEY (facility_id) REFERENCES managed_facilities(facility_id) ON DELETE RESTRICT,
    FOREIGN KEY (repair_id) REFERENCES facility_repairs(repair_id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    FOREIGN KEY (hidden_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    CHECK ((facility_id IS NOT NULL AND repair_id IS NULL)
        OR (facility_id IS NULL AND repair_id IS NOT NULL)),
    CHECK (photographed_on IS NULL OR (length(photographed_on)=10 AND photographed_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')),
    CHECK ((original_stored_filename IS NULL AND original_media_type IS NULL
            AND original_bytes IS NULL AND original_sha256 IS NULL)
        OR (original_stored_filename IS NOT NULL AND original_media_type IS NOT NULL
            AND original_bytes IS NOT NULL AND length(original_sha256)=64)),
    CHECK ((hidden_at IS NULL AND hidden_by_user_id IS NULL AND hidden_reason IS NULL)
        OR (hidden_at IS NOT NULL AND hidden_by_user_id IS NOT NULL AND hidden_reason IS NOT NULL
            AND length(trim(hidden_reason)) BETWEEN 1 AND 500))
);

CREATE INDEX idx_facility_photos_facility
    ON facility_photos(facility_id, photographed_on DESC, photo_id DESC);
CREATE INDEX idx_facility_photos_repair
    ON facility_photos(repair_id, photographed_on DESC, photo_id DESC);
CREATE INDEX idx_facility_photos_visible
    ON facility_photos(hidden_at, photo_id DESC);

CREATE TABLE facility_photo_events (
    event_id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('REGISTERED', 'HIDDEN')),
    actor_user_id INTEGER NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (photo_id) REFERENCES facility_photos(photo_id) ON DELETE RESTRICT,
    FOREIGN KEY (actor_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);
CREATE INDEX idx_facility_photo_events_photo
    ON facility_photo_events(photo_id, event_id);

-- Event records may not be deleted or rewritten by application code.
CREATE TRIGGER trg_facility_photo_events_no_update
BEFORE UPDATE ON facility_photo_events
BEGIN
    SELECT RAISE(ABORT, 'photo events are immutable');
END;
CREATE TRIGGER trg_facility_photo_events_no_delete
BEFORE DELETE ON facility_photo_events
BEGIN
    SELECT RAISE(ABORT, 'photo events are immutable');
END;
