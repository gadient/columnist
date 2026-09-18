-- In-app feedback. Invited users leave a note; the Instance's primary user reads it
-- back without needing database access.
--
-- No AUTOINCREMENT and no rowid dependency: this must also run on Postgres (a supported deployment
-- backend), so the id is a TEXT uuid like the other application tables. `user_id` is nullable so
-- feedback still records when Cognito is off (local dev has no caller identity) — the row is the
-- point, attribution is a bonus.
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY,
  instance_id TEXT,
  user_id TEXT,
  user_email TEXT,
  category TEXT NOT NULL,            -- chat | notes | general | bug
  rating INTEGER,                    -- optional 1-5; NULL when not offered or skipped
  message TEXT NOT NULL,
  page TEXT,                         -- where they were when they wrote it
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_instance_created ON feedback(instance_id, created_at);
