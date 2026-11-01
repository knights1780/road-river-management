
-- 0009_project_progress_reviews.sql
-- Project-level progress review prompts.
-- Existing migrations are unchanged.

CREATE TABLE project_progress_review_prompts (
    project_prompt_id INTEGER PRIMARY KEY,
    prompt_uuid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    latest_stage_id INTEGER,

    first_operation_uuid TEXT NOT NULL,
    latest_operation_uuid TEXT NOT NULL,

    prompt_reason_code TEXT NOT NULL
        CHECK (
            prompt_reason_code IN (
                'STAGE_PROGRESS_CHANGED',
                'STAGE_STATUS_CHANGED',
                'PROGRESS_WEIGHT_CHANGED',
                'OTHER'
            )
        ),

    reference_progress_percent REAL
        CHECK (
            reference_progress_percent IS NULL
            OR reference_progress_percent BETWEEN 0 AND 100
        ),

    official_progress_percent REAL NOT NULL
        CHECK (official_progress_percent BETWEEN 0 AND 100),

    review_status_code TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (
            review_status_code IN (
                'PENDING',
                'REVIEWED_UPDATED',
                'REVIEWED_UNCHANGED',
                'DISMISSED'
            )
        ),

    event_count INTEGER NOT NULL DEFAULT 1
        CHECK (event_count >= 1),

    assigned_user_id INTEGER,
    first_notified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_notified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    due_on TEXT,
    resolved_on TEXT,
    resolved_by_user_id INTEGER,
    resolution_reason TEXT,

    FOREIGN KEY (project_id)
        REFERENCES maintenance_projects(project_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (project_id, latest_stage_id)
        REFERENCES project_stages(project_id, stage_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (assigned_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (resolved_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (
        (
            review_status_code = 'PENDING'
            AND resolved_on IS NULL
        )
        OR
        (
            review_status_code <> 'PENDING'
            AND resolved_on IS NOT NULL
        )
    )
);

-- Only one unresolved review prompt per project.
CREATE UNIQUE INDEX uq_project_progress_review_pending
ON project_progress_review_prompts(project_id)
WHERE review_status_code = 'PENDING';

CREATE INDEX idx_project_progress_review_status
ON project_progress_review_prompts(
    review_status_code,
    due_on
);

CREATE INDEX idx_project_progress_review_history
ON project_progress_review_prompts(
    project_id,
    first_notified_at
);
