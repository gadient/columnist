-- Dedicated completion timestamp.
-- Velocity/analytics previously keyed on cards.updated_at, but the snapshot write path rewrites
-- updated_at on every save — so any board edit made old completed work look newly completed.
-- completed_at is set precisely on the incomplete->complete transition (toggle, PATCH, and the
-- snapshot diff) and preserved across saves, so "completed in the last N days" is accurate.

ALTER TABLE cards ADD COLUMN completed_at TEXT;

-- Backfill: existing completed cards have no true historical completion time, so seed with their
-- updated_at (the closest available signal). Precise from here on. Guarded on NULL = re-runnable.
UPDATE cards SET completed_at = updated_at WHERE completed = 1 AND completed_at IS NULL;
