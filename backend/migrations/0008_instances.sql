-- Instance tenant boundary.
-- The top-level isolation entity: every user, workspace, and board belongs to
-- exactly one Instance. Sits ABOVE the existing per-workspace membership (0006);
-- an Instance groups all of one owner's users/workspaces/boards into one tenant so
-- "take down an Instance and its data" is a single cascade.

CREATE TABLE IF NOT EXISTS instances (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner_id TEXT,                 -- Cognito `sub` of the owner/creator; NULL for the backfilled default
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- New tenant FK on each owned entity. Bare ADD COLUMN matches the 0002/0006 pattern;
-- the migration runner applies each file exactly once, so non-idempotent ALTER is safe.
ALTER TABLE users      ADD COLUMN instance_id TEXT;
ALTER TABLE workspaces ADD COLUMN instance_id TEXT;
ALTER TABLE boards     ADD COLUMN instance_id TEXT;

-- Backfill: one default Instance holds everything that predates tenancy, so no row
-- is left orphaned. The UPDATEs are guarded on NULL and thus safe to re-run.
INSERT OR IGNORE INTO instances (id, name, owner_id, created_at, updated_at)
VALUES ('default', 'Default Instance', NULL, datetime('now'), datetime('now'));

UPDATE users      SET instance_id = 'default' WHERE instance_id IS NULL;
UPDATE workspaces SET instance_id = 'default' WHERE instance_id IS NULL;
UPDATE boards     SET instance_id = 'default' WHERE instance_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_instance      ON users(instance_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_instance ON workspaces(instance_id);
CREATE INDEX IF NOT EXISTS idx_boards_instance     ON boards(instance_id);
